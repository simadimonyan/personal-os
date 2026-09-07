"""АТОМАРНАЯ запись дневного файла Obsidian (§1.3, ADR-3).

Гарантии:
- весь файловый I/O — через asyncio.to_thread (event loop не блокируется);
- запись через .tmp + os.replace() (атомарный rename на одной FS) — Яндекс.Диск
  никогда не видит полу-записанный файл;
- asyncio.Lock per-date — два слота близко не гонятся за один файл;
- read-modify-write: читаем свежую версию файла (не кэш), merge frontmatter,
  append секции тела, пишем обратно.

Структура файла:
    ---
    <frontmatter>
    ---
    # Состояние · YYYY-MM-DD
    ## ☀️ Утро · HH:MM
    ...
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.obsidian import frontmatter as fm
from app.obsidian import note_body
from app.obsidian.atomic import atomic_write
from app.obsidian.paths import VaultPaths

log = logging.getLogger("assistant.obsidian.writer")


class ObsidianWriter:
    def __init__(self, paths: VaultPaths) -> None:
        self._paths = paths
        # один Lock на каждую дату (имя файла)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock_for(self, date: str) -> asyncio.Lock:
        return self._locks[date]

    async def write_slot(
        self,
        date: str,
        slot: str,
        answers: dict[str, Any],
        flags: list[str] | None = None,
        hhmm: str | None = None,
        free_text: str | None = None,
    ) -> None:
        """Атомарно дописывает секцию слота в дневной файл.

        Бросает исключение при ошибке I/O — вызывающий код (outbox worker)
        ловит и ретраит.
        """
        hhmm = hhmm or datetime.now().strftime("%H:%M")
        patch = fm.build_patch_from_answers(answers, slot, flags)
        section = note_body.render_section(slot, hhmm, answers, free_text=free_text)

        async with self._lock_for(date):
            await asyncio.to_thread(
                self._read_modify_write, date, patch, section
            )
        log.info("obsidian write ok: date=%s slot=%s", date, slot)

    async def append_raw_section(self, date: str, section: str, patch: dict[str, Any] | None = None) -> None:
        """Дописывает произвольную готовую секцию (заметки, голос-плейсхолдер)."""
        async with self._lock_for(date):
            await asyncio.to_thread(
                self._read_modify_write, date, patch or {}, section
            )
        log.info("obsidian append raw ok: date=%s", date)

    async def replace_last_section(self, date: str, new_section: str) -> bool:
        """Заменяет последнюю секцию (## ...) в теле файла. Для edit_last.

        Возвращает True если замена выполнена.
        """
        async with self._lock_for(date):
            return await asyncio.to_thread(self._replace_last, date, new_section)

    async def append_review(self, name: str, section: str, title: str) -> None:
        """Дописывает секцию в rolling-файл обзора (недельные/месячные метрики).

        Файл создаётся с заголовком `title`, если его нет. Новые секции идут в конец.
        Атомарно, под lock по имени файла.
        """
        async with self._lock_for(f"review:{name}"):
            await asyncio.to_thread(self._append_review, name, section, title)
        log.info("obsidian review append ok: %s", name)

    def _append_review(self, name: str, section: str, title: str) -> None:
        path = self._paths.review_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing.strip():
            existing = f"# {title}\n"
        content = existing.rstrip() + "\n\n" + section.rstrip() + "\n"
        self._atomic_write(path, content)

    async def write_thought_note(
        self, filename: str, frontmatter: dict[str, Any], body: str
    ) -> str:
        """Пишет отдельный файл заметки-мысли в папку идей (уникальное имя по ts).

        Возвращает строковый путь. Бросает исключение при ошибке I/O (outbox ретраит).
        """
        path = self._paths.thought_file(filename)

        def _write() -> None:
            self._paths.ensure_dirs()
            self._atomic_write(path, self._render_thought(frontmatter, body))

        # lock по имени файла — на случай дубля одного и того же ts
        async with self._lock_for(f"thought:{filename}"):
            await asyncio.to_thread(_write)
        log.info("obsidian thought note written: %s", filename)
        return str(path)

    @staticmethod
    def _render_thought(frontmatter: dict[str, Any], body: str) -> str:
        lines = ["---"]
        for key, value in frontmatter.items():
            if isinstance(value, (list, tuple)):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        return "\n".join(lines) + "\n\n" + body.rstrip() + "\n"

    # ---- блокирующие операции (выполняются в потоке) ----

    def _read_modify_write(self, date: str, patch: dict[str, Any], section: str) -> None:
        path = self._paths.diary_file(date)
        self._paths.ensure_dirs()

        existing_fm, body = self._read(path)

        data = fm.parse_frontmatter(existing_fm)
        data = fm.ensure_base_frontmatter(data, date)
        merged = fm.merge_frontmatter(data, patch)
        fm_text = fm.dump_frontmatter(merged)

        if not body.strip():
            body = note_body.render_title(date) + "\n"
        new_body = note_body.append_section(body, section)

        content = f"---\n{fm_text}---\n\n{new_body}"
        self._atomic_write(path, content)

    def _replace_last(self, date: str, new_section: str) -> bool:
        path = self._paths.diary_file(date)
        if not path.exists():
            return False
        existing_fm, body = self._read(path)

        lines = body.splitlines()
        # найти индекс последнего заголовка секции '## '
        last_idx = None
        for i, line in enumerate(lines):
            if line.startswith("## "):
                last_idx = i
        if last_idx is None:
            return False

        new_lines = lines[:last_idx] + new_section.splitlines()
        new_body = "\n".join(new_lines).rstrip() + "\n"
        content = f"---\n{existing_fm}---\n\n{new_body}"
        self._atomic_write(path, content)
        return True

    @staticmethod
    def _read(path: Path) -> tuple[str, str]:
        if not path.exists():
            return "", ""
        text = path.read_text(encoding="utf-8")
        return fm.split_document(text)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Запись через .tmp + os.replace. Делегирует общему хелперу atomic_write."""
        atomic_write(path, content)
