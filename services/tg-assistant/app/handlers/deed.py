"""Событийный лог поступка (слот deed) — только когда случилось, без напоминаний.

A. Столкнулся / накрыло → что сделал: выход / проглотил из страха / сорвался (+ кому).
B. Сказал тяжёлое / попросил → кому: поддержке / источнику оценки.
C. Начал контакт первым.

Ловится постфактум: «стало нормально с собой — что я только что сделал с живым
человеком?». Пустой день/неделя — не провал, отдельного порицания нет.
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
from app.states.checkin_states import DeedStates

log = logging.getLogger("assistant.deed")
router = Router(name="deed")

_KIND_Q = "Что за поступок отметить?"
_NOTE_Q = "Одна фраза — что именно? (опц.)"
_CLOSE = "Записал. Ценность — в самом действии, не в реакции."


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def start_deed(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "deed")
    checkin = CheckIn(date=_today(), slot="deed", checkin_id=checkin_id)
    await state.update_data(checkin=checkin.to_dict())
    await message.answer(_KIND_Q, reply_markup=with_cancel(akb.deed_kind_kb()))
    await state.set_state(DeedStates.kind)


@router.callback_query(DeedStates.kind, EnumCB.filter(F.metric == "deed_kind"))
async def on_kind(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    kind = callback_data.value
    checkin.set("deed_kind", kind)
    await _save(state, checkin)
    await cb.message.edit_text(akb.DEED_KIND_LABELS.get(kind, kind) + ".")
    if kind == "A":
        await cb.message.answer("Что сделал с этим?", reply_markup=akb.deed_action_kb())
        await state.set_state(DeedStates.action)
    elif kind == "B":
        await cb.message.answer("Кому отнёс?", reply_markup=akb.deed_addressee_kb())
        await state.set_state(DeedStates.addressee)
    else:  # C — начал контакт первым: детали не нужны, сразу опц. текст
        await _ask_note(cb.message, state)
    await cb.answer()


@router.callback_query(DeedStates.action, EnumCB.filter(F.metric == "deed_action"))
async def on_action(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("deed_action", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Что сделал: {akb.DEED_ACTION_LABELS.get(callback_data.value, callback_data.value)}.")
    await cb.message.answer("Кому это относилось?", reply_markup=akb.deed_addressee_kb())
    await state.set_state(DeedStates.addressee)
    await cb.answer()


@router.callback_query(DeedStates.addressee, EnumCB.filter(F.metric == "deed_addressee"))
async def on_addressee(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("deed_addressee", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Кому: {akb.DEED_ADDRESSEE_LABELS.get(callback_data.value, callback_data.value)}.")
    await _ask_note(cb.message, state)
    await cb.answer()


async def _ask_note(message: Message, state: FSMContext) -> None:
    await message.answer(_NOTE_Q, reply_markup=skip_kb())
    await state.set_state(DeedStates.note)


@router.callback_query(DeedStates.note, ActionCB.filter(F.name == "skip"))
async def on_note_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Ок.")
    await _finish(cb.message, state, ctx)
    await cb.answer()


@router.message(DeedStates.note, F.text)
async def on_note_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.free_text = message.text.strip()
    await _save(state, checkin)
    await _finish(message, state, ctx)


async def _finish(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    flags = await finalize_checkin(ctx, checkin)
    follow = flag_followup(flags)
    if follow:
        await message.answer(follow)
    await message.answer(_CLOSE)
    await state.clear()
