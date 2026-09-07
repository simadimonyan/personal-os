"""Background worker надёжной доставки записи в Obsidian (§5.4, ADR-2).

Каждые poll_interval секунд берёт done=0 записи из outbox, у которых наступило
next_attempt_at, пытается записать в vault. При успехе — done=1 + пометить
checkin.written. При ошибке — attempts++ и exponential backoff
(next_attempt_at = now + min(base * 2**attempts, cap)).

Это гарантирует: ни один завершённый чек-ин не потерян, даже если в момент
записи Яндекс.Диск занят / нет места / файл временно заблокирован.

Payload в outbox:
    {
      "date": "YYYY-MM-DD",
      "slot": "morning",
      "answers": {...},
      "flags": [...],
      "hhmm": "10:42",
      "free_text": null,        # опц.
      "raw_section": null,      # опц.: для заметок/голоса — готовая секция
      "raw_patch": {}           # опц.: frontmatter-патч к raw_section
    }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import Settings
from app.obsidian.writer import ObsidianWriter
from app.storage.repositories import CheckinRepo, OutboxRepo

log = logging.getLogger("assistant.obsidian.outbox")


class OutboxWorker:
    def __init__(
        self,
        settings: Settings,
        outbox: OutboxRepo,
        checkins: CheckinRepo,
        writer: ObsidianWriter,
    ) -> None:
        self._s = settings
        self._outbox = outbox
        self._checkins = checkins
        self._writer = writer
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="outbox-worker")
            log.info("outbox worker started (interval=%ss)", self._s.outbox.poll_interval_seconds)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
            log.info("outbox worker stopped")

    async def _run(self) -> None:
        interval = self._s.outbox.poll_interval_seconds
        while not self._stop.is_set():
            try:
                await self._process_batch()
            except Exception:  # noqa: BLE001 — воркер не должен падать
                log.exception("outbox batch failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def _process_batch(self) -> None:
        due = await self._outbox.due(limit=20)
        for row in due:
            await self._process_one(row)

    async def _process_one(self, row) -> None:  # noqa: ANN001
        p = row.payload
        try:
            if p.get("review"):
                rv = p["review"]
                await self._writer.append_review(rv["name"], rv["section"], rv["title"])
            elif p.get("thought_note"):
                tn = p["thought_note"]
                await self._writer.write_thought_note(
                    tn["filename"], tn["frontmatter"], tn["body"]
                )
            elif p.get("raw_section"):
                await self._writer.append_raw_section(
                    p["date"], p["raw_section"], p.get("raw_patch") or {}
                )
            else:
                await self._writer.write_slot(
                    date=p["date"],
                    slot=p["slot"],
                    answers=p.get("answers", {}),
                    flags=p.get("flags") or [],
                    hhmm=p.get("hhmm"),
                    free_text=p.get("free_text"),
                )
            await self._outbox.mark_done(row.id)
            if p.get("checkin_id"):
                await self._checkins.mark_written(p["checkin_id"])
            elif row.checkin_id:
                await self._checkins.mark_written(row.checkin_id)
        except Exception as exc:  # noqa: BLE001
            delay = self._backoff(row.attempts)
            next_at = (datetime.now() + timedelta(seconds=delay)).isoformat(timespec="seconds")
            await self._outbox.mark_failed(row.id, repr(exc), next_at)
            log.warning(
                "obsidian write failed (outbox id=%s attempt=%s), retry in %ss: %s",
                row.id, row.attempts + 1, delay, exc,
            )

    def _backoff(self, attempts: int) -> int:
        base = self._s.outbox.backoff_base_seconds
        cap = self._s.outbox.backoff_cap_seconds
        return min(base * (2 ** attempts), cap)
