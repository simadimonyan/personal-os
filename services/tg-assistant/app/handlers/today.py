"""Статус «Сегодня» (кнопка 📊 Сегодня).

Показывает по текущему дню: что отмечено (утро/день/вечер), накрывало ли,
и какие заметки-«мысли» сохранены. Тексты заметок берутся из локальной БД,
не из логов (приватность сохраняется).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.keyboards import menu
from app.storage.repositories import CheckinRow, Repositories

log = logging.getLogger("assistant.today")
router = Router(name="today")

_SLOT_LINE = {
    "morning": "☀️ Утро",
    "day": "🌤 День",
    "evening": "🌙 Вечер",
}


def _hhmm(started_at: str) -> str:
    # started_at = ISO8601 "YYYY-MM-DDTHH:MM:SS"
    return started_at[11:16] if len(started_at) >= 16 else "—"


def _snippet(text: str, limit: int = 50) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


_FORK_RU = {
    "sam": "решил сам",
    "obey": "подчинился",
    "spite": "поспорил-назло",
    "none": "развилки не было",
}


def build_status(rows: list[CheckinRow], date: str) -> str:
    done_slots = {r.slot for r in rows}
    situational = [r for r in rows if r.slot == "situational"]
    notes = [r for r in rows if r.slot == "note"]
    agency = [r for r in rows if r.slot == "agency"]
    deeds = [r for r in rows if r.slot == "deed"]

    lines = [f"📊 Сегодня · {date}", ""]
    for slot in ("morning", "day", "evening"):
        mark = "✅" if slot in done_slots else "⚪️ ещё нет"
        lines.append(f"{_SLOT_LINE[slot]} — {mark}")

    lines.append("")
    if agency:
        fork = agency[-1].answers.get("agency_fork")
        lines.append(f"🧭 Развилка — {_FORK_RU.get(fork, 'отмечена')}")
    else:
        lines.append("🧭 Развилка — ⚪️ ещё нет")
    if deeds:
        lines.append(f"📌 Поступки: {len(deeds)}")

    habit_rows = [r for r in rows if r.slot == "habits"]
    if habit_rows:
        done: list[str] = []
        slip: list[str] = []
        for r in habit_rows:
            done += [x for x in (r.answers.get("habits_done") or []) if x]
            slip += [x for x in (r.answers.get("habits_slip") or []) if x]
        if done:
            lines.append("🌱 Сделал: " + ", ".join(dict.fromkeys(done)))
        if slip:
            lines.append("🚫 Сорвался: " + ", ".join(dict.fromkeys(slip)))

    lines.append("")
    if situational:
        lines.append(f"🆘 Накрывало: {len(situational)} раз(а)")
    else:
        lines.append("🆘 Накрывало: не было")

    lines.append("")
    if notes:
        lines.append(f"📝 Заметки ({len(notes)}):")
        for r in notes:
            text = str(r.answers.get("text", "")).strip()
            tags = r.answers.get("tags") or []
            extra = [t for t in tags if t != "мысль"]
            tag_str = ("  " + " ".join(f"#{t}" for t in extra)) if extra else ""
            lines.append(f" • {_hhmm(r.started_at)} {_snippet(text)}{tag_str}")
    else:
        lines.append("📝 Заметок сегодня нет.")

    return "\n".join(lines)


@router.message(F.text == menu.BTN_TODAY)
async def show_today(message: Message, repos: Repositories) -> None:
    today = date_cls.today().isoformat()
    rows = await repos.checkins.for_date(today)
    await message.answer(build_status(rows, today))


@router.callback_query(menu.MenuCB.filter(F.action == "today"))
async def cb_today(cb: CallbackQuery, repos: Repositories) -> None:
    await cb.answer()
    await show_today(cb.message, repos)
