"""Обзоры авторства: неделя (3 вопроса) и месяц (7 фактов да/нет).

Пишутся в rolling-файлы `02 — Внутренний мир/Обзоры авторства/*` через outbox
(review-payload). Это НЕ дневник состояний и НЕ участвует в heatmap/баллах.

Guard от повторного пинга той же недели/месяца — в settings
(`weekly_review_done=YYYY-WW`, `monthly_review_done=YYYY-MM`); ставится по
завершении (в т.ч. при ручном запуске из меню).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.checkin import CheckIn
from app.handlers._service import AppContext
from app.keyboards import agency as akb
from app.keyboards.common import ActionCB, skip_kb, with_cancel
from app.keyboards.enums import EnumCB
from app.states.checkin_states import MonthlyStates, WeeklyStates

log = logging.getLogger("assistant.review")
router = Router(name="review")

WEEKLY_FILE = "Недельные обзоры"
WEEKLY_TITLE = "Недельные обзоры авторства"
MONTHLY_FILE = "Месячные обзоры"
MONTHLY_TITLE = "Месячные обзоры авторства"


def _today() -> str:
    return date_cls.today().isoformat()


def week_key(d: date_cls | None = None) -> str:
    d = d or date_cls.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def month_key(d: date_cls | None = None) -> str:
    d = d or date_cls.today()
    return f"{d.year}-{d.month:02d}"


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def _enqueue_review(ctx: AppContext, slot: str, answers: dict,
                          name: str, title: str, section: str) -> None:
    """Создаёт запись checkin (FK для outbox) и кладёт review-payload."""
    cid = await ctx.repos.checkins.create(_today(), slot)
    await ctx.repos.checkins.update_answers(cid, answers)
    await ctx.repos.checkins.finish(cid, [])
    await ctx.repos.outbox.enqueue(
        cid, {"review": {"name": name, "title": title, "section": section}}
    )


# ============================================================================
# Недельный обзор — 3 вопроса
# ============================================================================

_W1_Q = (
    "Обзор недели.\n\n1/3. На этой неделе ты действовал только там, где решаешь "
    "один, — или и с живыми людьми тоже?\n"
    "(с живыми — это сказать границу, попросить о помощи, вытянуть тяжёлый разговор)"
)
_W2_Q = "2/3. Где-то спорил по делу, а не защищал себя? (не «я хороший», а «вот это условие — нет»)"
_W3_Q = "3/3. Своё важное на этой неделе отнёс тому, кто поддержит, — или тому, кто оценивает?"
_W_TEXT = "Одна фраза-пример — или «Пропустить»."


async def start_weekly(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin = CheckIn(date=_today(), slot="weekly")
    await state.update_data(checkin=checkin.to_dict())
    await message.answer(_W1_Q, reply_markup=with_cancel(akb.weekly_q1_kb()))
    await state.set_state(WeeklyStates.q1)


@router.callback_query(WeeklyStates.q1, EnumCB.filter(F.metric == "weekly_q1"))
async def w_q1(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q1", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text("Сила: " + akb.WEEKLY_Q1_LABELS[callback_data.value] + ".")
    await cb.message.answer(_W_TEXT, reply_markup=skip_kb())
    await state.set_state(WeeklyStates.q1_text)
    await cb.answer()


@router.callback_query(WeeklyStates.q1_text, ActionCB.filter(F.name == "skip"))
async def w_q1_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text("Ок.")
    await cb.message.answer(_W2_Q, reply_markup=akb.weekly_q2_kb())
    await state.set_state(WeeklyStates.q2)
    await cb.answer()


@router.message(WeeklyStates.q1_text, F.text)
async def w_q1_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q1_note", message.text.strip())
    await _save(state, checkin)
    await message.answer(_W2_Q, reply_markup=akb.weekly_q2_kb())
    await state.set_state(WeeklyStates.q2)


@router.callback_query(WeeklyStates.q2, EnumCB.filter(F.metric == "weekly_q2"))
async def w_q2(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q2", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text("Позиция про дело: " + akb.WEEKLY_Q2_LABELS[callback_data.value] + ".")
    await cb.message.answer(_W_TEXT, reply_markup=skip_kb())
    await state.set_state(WeeklyStates.q2_text)
    await cb.answer()


@router.callback_query(WeeklyStates.q2_text, ActionCB.filter(F.name == "skip"))
async def w_q2_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text("Ок.")
    await cb.message.answer(_W3_Q, reply_markup=akb.weekly_q3_kb())
    await state.set_state(WeeklyStates.q3)
    await cb.answer()


@router.message(WeeklyStates.q2_text, F.text)
async def w_q2_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q2_note", message.text.strip())
    await _save(state, checkin)
    await message.answer(_W3_Q, reply_markup=akb.weekly_q3_kb())
    await state.set_state(WeeklyStates.q3)


@router.callback_query(WeeklyStates.q3, EnumCB.filter(F.metric == "weekly_q3"))
async def w_q3(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q3", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text("Кому нёс важное: " + akb.WEEKLY_Q3_LABELS[callback_data.value] + ".")
    await cb.message.answer(_W_TEXT, reply_markup=skip_kb())
    await state.set_state(WeeklyStates.q3_text)
    await cb.answer()


@router.callback_query(WeeklyStates.q3_text, ActionCB.filter(F.name == "skip"))
async def w_q3_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Ок.")
    await _finish_weekly(cb.message, state, ctx)
    await cb.answer()


@router.message(WeeklyStates.q3_text, F.text)
async def w_q3_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("weekly_q3_note", message.text.strip())
    await _save(state, checkin)
    await _finish_weekly(message, state, ctx)


def _weekly_section(a: dict) -> str:
    def line(label: str, val_map: dict, key: str, note_key: str) -> str:
        val = a.get(key)
        txt = val_map.get(val, "—") if val else "—"
        note = a.get(note_key)
        return f"- {label}: {txt}" + (f" — {note}" if note else "")

    lines = [f"## Неделя {week_key()} · {_today()}"]
    lines.append(line("Где действовал", akb.WEEKLY_Q1_LABELS, "weekly_q1", "weekly_q1_note"))
    lines.append(line("Спорил по делу", akb.WEEKLY_Q2_LABELS, "weekly_q2", "weekly_q2_note"))
    lines.append(line("Важное отнёс", akb.WEEKLY_Q3_LABELS, "weekly_q3", "weekly_q3_note"))
    return "\n".join(lines)


async def _finish_weekly(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    section = _weekly_section(checkin.answers)
    await _enqueue_review(ctx, "weekly", checkin.answers, WEEKLY_FILE, WEEKLY_TITLE, section)
    await ctx.repos.settings.set("weekly_review_done", week_key())
    await message.answer("Записал обзор недели. Это карта, не оценка.")
    await state.clear()


# ============================================================================
# Месячный обзор — 7 фактов да/нет (один экран тумблеров)
# ============================================================================

_M_Q = (
    "Обзор месяца. Отметь то, что было хотя бы раз — фактами, без баллов.\n"
    "Что не отмечено = не было. Итога и суммы нет."
)


async def start_monthly(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin = CheckIn(date=_today(), slot="monthly")
    checkin.set("month_facts", [])
    await state.update_data(checkin=checkin.to_dict())
    await message.answer(_M_Q, reply_markup=with_cancel(akb.monthly_kb([])))
    await state.set_state(MonthlyStates.facts)


@router.callback_query(MonthlyStates.facts, akb.MonthFactCB.filter())
async def m_toggle(cb: CallbackQuery, callback_data: akb.MonthFactCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("month_facts", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=akb.monthly_kb(selected))
    await cb.answer()


@router.callback_query(MonthlyStates.facts, ActionCB.filter(F.name == "done"))
async def m_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    selected = set(checkin.get("month_facts", []))
    lines = [f"## Месяц {month_key()}"]
    for key, label in akb.MONTHLY_FACTS:
        mark = "✅" if key in selected else "⬜️"
        lines.append(f"- {mark} {label}")
    section = "\n".join(lines)
    await _enqueue_review(ctx, "monthly", checkin.answers, MONTHLY_FILE, MONTHLY_TITLE, section)
    await ctx.repos.settings.set("monthly_review_done", month_key())
    await cb.message.edit_text("Записал обзор месяца. Факты, не приговор.")
    await state.clear()
    await cb.answer()
