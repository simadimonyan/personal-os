"""Аналитика состояний (кнопка 📈 Аналитика).

Шлёт PNG-карту «как на GitHub» за месяц + инлайн-навигацию:
- ◀️ / ▶️ — листать месяцы (edit_media того же сообщения);
- кнопки-числа дней → детальная статистика дня;
- «Σ за месяц» → агрегат за месяц.

Числа считаются в app.domain.stats (чистые функции), рендер PNG — в
app.render.heatmap_png. Тексты ответов формируются здесь.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from app.domain import stats as stats_domain
from app.keyboards import menu
from app.render.heatmap_png import render_month_heatmap
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.stats")
router = Router(name="stats")

_MONTHS_RU = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
_EMOTION_RU = {
    "anxiety": "тревога", "shame": "стыд", "envy": "зависть",
    "loneliness": "одиночество", "anger": "злость", "sadness": "грусть",
    "apathy": "апатия", "joy": "радость", "calm": "спокойствие",
    "hope": "надежда", "unreadable": "не считывается",
}


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _ru_label(key: str) -> str:
    return _EMOTION_RU.get(key, key.replace("_", " "))


def _build_kb(year: int, month: int) -> InlineKeyboardMarkup:
    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, +1)
    rows: list[list[InlineKeyboardButton]] = [[
        InlineKeyboardButton(text="◀️", callback_data=f"hm:{py:04d}-{pm:02d}"),
        InlineKeyboardButton(text=f"{_MONTHS_RU[month - 1]} {year}", callback_data="noop"),
        InlineKeyboardButton(text="▶️", callback_data=f"hm:{ny:04d}-{nm:02d}"),
    ]]
    days = calendar.monthrange(year, month)[1]
    row: list[InlineKeyboardButton] = []
    for day in range(1, days + 1):
        row.append(InlineKeyboardButton(
            text=str(day), callback_data=f"day:{year:04d}-{month:02d}-{day:02d}"))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="Σ за месяц", callback_data=f"period:{year:04d}-{month:02d}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_png(repos: Repositories, year: int, month: int) -> tuple[bytes, str]:
    start, end = _month_bounds(year, month)
    counts = await repos.checkins.counts_by_date(start, end)
    png = await asyncio.to_thread(render_month_heatmap, year, month, counts)
    marked_days = len(counts)
    total_marks = sum(counts.values())
    caption = (
        f"📈 Состояния · {_MONTHS_RU[month - 1]} {year}\n"
        f"Отмечено дней: {marked_days} · всего отметок: {total_marks}\n"
        f"Тапни по числу дня — стата дня. «Σ за месяц» — сводка."
    )
    return png, caption


@router.message(F.text == menu.BTN_ANALYTICS)
async def show_analytics(message: Message, repos: Repositories) -> None:
    today = date_cls.today()
    png, caption = await _render_png(repos, today.year, today.month)
    await message.answer_photo(
        BufferedInputFile(png, filename="heatmap.png"),
        caption=caption,
        reply_markup=_build_kb(today.year, today.month),
    )


@router.callback_query(F.data.startswith("hm:"))
async def nav_month(cb: CallbackQuery, repos: Repositories) -> None:
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    png, caption = await _render_png(repos, year, month)
    try:
        await cb.message.edit_media(
            InputMediaPhoto(media=BufferedInputFile(png, filename="heatmap.png"),
                            caption=caption),
            reply_markup=_build_kb(year, month),
        )
    except Exception:  # noqa: BLE001 — то же изображение/старое сообщение
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("day:"))
async def show_day(cb: CallbackQuery, repos: Repositories) -> None:
    date = cb.data.split(":", 1)[1]
    rows = await repos.checkins.for_date(date)
    ds = stats_domain.build_day_stats(date, rows)
    await cb.message.answer(_format_day(ds))
    await cb.answer()


@router.callback_query(F.data.startswith("period:"))
async def show_period(cb: CallbackQuery, repos: Repositories) -> None:
    ym = cb.data.split(":", 1)[1]
    year, month = int(ym[:4]), int(ym[5:7])
    start, end = _month_bounds(year, month)
    rows = await repos.checkins.for_period(start, end)
    ps = stats_domain.build_period_stats(start, end, rows)
    await cb.message.answer(_format_period(ps, year, month))
    await cb.answer()


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ---- форматирование ----

_SLOT_RU = {"morning": "☀️ утро", "day": "🌤 день", "evening": "🌙 вечер"}


def _format_day(ds: stats_domain.DayStats) -> str:
    if not ds.has_any:
        return f"📅 {ds.date}\n\nВ этот день отметок не было."
    lines = [f"📅 {ds.date}", ""]
    if ds.slots_done:
        lines.append("Отмечено: " + ", ".join(_SLOT_RU.get(s, s) for s in ds.slots_done))
    if ds.situational_count:
        lines.append(f"🆘 Накрывало: {ds.situational_count} раз(а)")
    if ds.note_count:
        lines.append(f"📝 Заметок: {ds.note_count}")
    nums = []
    if ds.valence_avg is not None:
        nums.append(f"тон {ds.valence_avg:+g}")
    if ds.arousal_avg is not None:
        nums.append(f"энергия {ds.arousal_avg:g}")
    if ds.anxiety_avg is not None:
        nums.append(f"тревога {ds.anxiety_avg:g}/10")
    if ds.self_criticism_avg is not None:
        nums.append(f"критик {ds.self_criticism_avg:g}/10")
    if nums:
        lines += ["", "Средние: " + " · ".join(nums)]
    if ds.emotions:
        lines.append("Эмоции: " + ", ".join(f"{_ru_label(e)}×{n}" for e, n in ds.emotions))
    if ds.triggers:
        lines.append("Триггеры: " + ", ".join(f"{_ru_label(t)}×{n}" for t, n in ds.triggers))
    if ds.habits_done:
        lines.append("🌱 Сделал: " + ", ".join(ds.habits_done))
    if ds.habits_slip:
        lines.append("🚫 Сорвался: " + ", ".join(ds.habits_slip))
    return "\n".join(lines)


def _format_period(ps: stats_domain.PeriodStats, year: int, month: int) -> str:
    lines = [f"Σ {_MONTHS_RU[month - 1]} {year}", ""]
    lines.append(f"Отметок состояний: {ps.total_marks}")
    if ps.total_days:
        lines.append(f"Дней с отметками: {ps.days_with_marks} из {ps.total_days}")
    nums = []
    if ps.valence_avg is not None:
        nums.append(f"тон {ps.valence_avg:+g}")
    if ps.arousal_avg is not None:
        nums.append(f"энергия {ps.arousal_avg:g}")
    if ps.anxiety_avg is not None:
        nums.append(f"тревога {ps.anxiety_avg:g}/10")
    if ps.self_criticism_avg is not None:
        nums.append(f"критик {ps.self_criticism_avg:g}/10")
    if nums:
        lines += ["", "Средние: " + " · ".join(nums)]
    if ps.emotions:
        lines.append("Топ-эмоции: " + ", ".join(f"{_ru_label(e)}×{n}" for e, n in ps.emotions))
    if ps.triggers:
        lines.append("Топ-триггеры: " + ", ".join(f"{_ru_label(t)}×{n}" for t, n in ps.triggers))
    if ps.situational_count:
        lines.append(f"🆘 Накрывало: {ps.situational_count} раз(а)")
    if ps.note_count:
        lines.append(f"📝 Заметок: {ps.note_count}")
    if ps.habits_done or ps.habits_slip:
        lines += ["", f"🌱 Привычки (отмечал {ps.habit_days} дн.):"]
        if ps.habits_done:
            lines.append("  сделал: " + ", ".join(f"{name} ×{n}" for name, n in ps.habits_done))
        if ps.habits_slip:
            lines.append("  срывы: " + ", ".join(f"{name} ×{n}" for name, n in ps.habits_slip))
    return "\n".join(lines)


@router.callback_query(menu.MenuCB.filter(F.action == "stats"))
async def cb_analytics(cb: CallbackQuery, repos: Repositories) -> None:
    await cb.answer()
    await show_analytics(cb.message, repos)
