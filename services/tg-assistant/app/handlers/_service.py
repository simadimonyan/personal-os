"""Сервисный слой между хендлерами и доменом/хранилищем.

Содержит общую логику завершения чек-ина:
- вычислить флаги по истории SQLite;
- сохранить ответы и статус done;
- положить запись в outbox (надёжная доставка в Obsidian).

Хендлеры остаются тонкими: собирают ответы в CheckIn и зовут finalize_checkin.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.checkin import CheckIn
from app.domain.flags import FlagInput, compute_flags, response_for_flags
from app.domain.rotation import Rotator
from app.prompts import pools
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.service")


class AppContext:
    """Зависимости приложения, прокидываемые в хендлеры через workflow_data."""

    def __init__(self, repos: Repositories, rotator: Rotator) -> None:
        self.repos = repos
        self.rotator = rotator


async def rotated(ctx: AppContext, pool_key: str) -> str:
    """Возвращает следующую (не повторяющую предыдущую) формулировку пула."""
    pool = pools.POOL_KEYS[pool_key]
    # для пулов из dict (MORNING_Q3) обрабатывается отдельно вызывающим кодом
    return await ctx.rotator.next_item(pool_key, pool)


async def rotated_index(ctx: AppContext, pool_key: str, size: int) -> int:
    return await ctx.rotator.next_index(pool_key, size)


async def finalize_checkin(
    ctx: AppContext,
    checkin: CheckIn,
) -> list[str]:
    """Завершает чек-ин: флаги -> SQLite done -> outbox enqueue.

    Возвращает список сработавших флагов (для корректировки тона ответа).
    """
    repos = ctx.repos

    # история завершённых чек-инов для флагов (новые первыми)
    history_rows = await repos.checkins.history(limit=60)
    history = [
        FlagInput(date=r.date, slot=r.slot, answers=r.answers)
        for r in history_rows
        if r.id != checkin.checkin_id
    ]
    current = FlagInput(
        date=checkin.date,
        slot=checkin.slot,
        answers=checkin.answers,
        free_text=checkin.free_text,
    )
    flags = compute_flags(current, history)

    # сохранить ответы + завершить
    if checkin.checkin_id is not None:
        await repos.checkins.update_answers(checkin.checkin_id, checkin.answers)
        await repos.checkins.finish(checkin.checkin_id, flags)

    # положить в outbox — воркер запишет в Obsidian с ретраями
    payload: dict[str, Any] = {
        "date": checkin.date,
        "slot": checkin.slot,
        "answers": checkin.answers,
        "flags": flags,
        "hhmm": checkin.started_hhmm,
        "free_text": checkin.free_text,
        "checkin_id": checkin.checkin_id,
    }
    cid = checkin.checkin_id if checkin.checkin_id is not None else 0
    await repos.outbox.enqueue(cid, payload)

    log.info(
        "checkin finalized: slot=%s flags=%s",
        checkin.slot,
        ",".join(flags) if flags else "none",
    )
    return flags


def flag_followup(flags: list[str]) -> str | None:
    """Текст мягкой реакции на флаги (§6) — добавляется ПОСЛЕ подтверждения."""
    return response_for_flags(flags)
