"""Ежедневная «Развилка» + база (слот agency) — метрики авторства, не состояния.

Поток: fork (решил сам / подчинился / поспорил-назло / развилки не было)
       → [если развилка была] addressee (кому) → note (что именно, опц.)
       → base (мультивыбор сон/тело/ходьба) → мягкое закрытие.

Ядро — мерка Димитри «решил сам · было страшно · сделал». Никаких баллов,
сумм, стриков и вердиктов. «Развилки не было» — честный ответ, не провал.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.checkin import CheckIn
from app.handlers._service import AppContext, finalize_checkin, flag_followup
from app.keyboards import agency as akb
from app.keyboards.common import ActionCB, skip_kb, with_cancel
from app.keyboards.enums import EnumCB
from app.states.checkin_states import AgencyStates

log = logging.getLogger("assistant.agency")
router = Router(name="agency")

_FORK_Q = (
    "Сегодня был момент выбора — ты решил сам, подчинился или поспорил назло?\n"
    "«Сам» — это когда решение твоё: не по приказу и не назло. Страх при этом "
    "не значит «нельзя» — страх значит «важно»."
)
_BASE_Q = "База сегодня — что было? (отметь и «Готово»)"
_CLOSE = "Записал. Это про действие, не про оценку. День закрыт."


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def start_agency(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "agency")
    checkin = CheckIn(date=_today(), slot="agency", checkin_id=checkin_id)
    checkin.set("base_done", [])
    await state.update_data(checkin=checkin.to_dict())
    await message.answer(_FORK_Q, reply_markup=with_cancel(akb.agency_fork_kb()))
    await state.set_state(AgencyStates.fork)


@router.callback_query(AgencyStates.fork, EnumCB.filter(F.metric == "agency_fork"))
async def on_fork(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("agency_fork", callback_data.value)
    await _save(state, checkin)
    label = akb.AGENCY_FORK_LABELS.get(callback_data.value, callback_data.value)
    await cb.message.edit_text(f"Развилка: {label}.")
    if callback_data.value == "none":
        # развилки не было — сразу к базе, без порицания
        await _ask_base(cb.message, state)
    else:
        await cb.message.answer("Кому это относилось?", reply_markup=akb.agency_addressee_kb())
        await state.set_state(AgencyStates.addressee)
    await cb.answer()


@router.callback_query(AgencyStates.addressee, EnumCB.filter(F.metric == "agency_addressee"))
async def on_addressee(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("agency_addressee", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Кому: {akb.ADDRESSEE_LABELS.get(callback_data.value, callback_data.value)}.")
    await cb.message.answer("Что именно? Одна фраза — или «Пропустить».", reply_markup=skip_kb())
    await state.set_state(AgencyStates.note)
    await cb.answer()


@router.callback_query(AgencyStates.addressee, ActionCB.filter(F.name == "other"))
async def on_addressee_other(cb: CallbackQuery, state: FSMContext) -> None:
    from app.handlers._other import ask_other
    await ask_other(cb)


@router.message(AgencyStates.addressee, F.text)
async def on_addressee_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    # свободный адресат уходит в текст секции
    checkin.free_text = f"кому: {message.text.strip()}"
    await _save(state, checkin)
    await message.answer("Что именно? Одна фраза — или «Пропустить».", reply_markup=skip_kb())
    await state.set_state(AgencyStates.note)


@router.callback_query(AgencyStates.note, ActionCB.filter(F.name == "skip"))
async def on_note_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text("Ок.")
    await _ask_base(cb.message, state)
    await cb.answer()


@router.message(AgencyStates.note, F.text)
async def on_note_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    add = message.text.strip()
    checkin.free_text = ((checkin.free_text + " · ") if checkin.free_text else "") + add
    await _save(state, checkin)
    await _ask_base(message, state)


async def _ask_base(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    if checkin.get("base_done") is None:
        checkin.set("base_done", [])
        await _save(state, checkin)
    await message.answer(_BASE_Q, reply_markup=akb.base_kb(checkin.get("base_done", [])))
    await state.set_state(AgencyStates.base)


@router.callback_query(AgencyStates.base, akb.BaseCB.filter())
async def on_base_toggle(cb: CallbackQuery, callback_data: akb.BaseCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("base_done", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=akb.base_kb(selected))
    await cb.answer()


@router.callback_query(AgencyStates.base, ActionCB.filter(F.name == "done"))
async def on_base_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Отметил.")
    await _finish(cb.message, state, ctx)
    await cb.answer()


async def _finish(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    flags = await finalize_checkin(ctx, checkin)
    follow = flag_followup(flags)
    if follow:
        await message.answer(follow)
    await message.answer(_CLOSE)
    await state.clear()
