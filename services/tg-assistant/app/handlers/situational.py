"""Ситуативный чек-ин «Накрыло» — FSM (§3.4 MASTER-PLAN).

Поток: body_tension (мультивыбор, сокр.) → what (эмоция/триггер) →
intensity → опц. текст → ВСЕГДА заземление
«Заметил. Это сигнал, не факт. Один медленный вдох.»

Сознательно НЕ спрашивает «почему» (§3.4: запускает анестетик-анализ).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.checkin import CheckIn
from app.handlers._other import append_free, ask_other
from app.handlers._service import AppContext, finalize_checkin, flag_followup
from app.keyboards import scales
from app.keyboards.body import BodyCB, body_kb
from app.keyboards.common import ActionCB, skip_kb, with_cancel
from app.keyboards.scales import ScaleCB
from app.keyboards.triggers import SituWhatCB, situational_what_kb
from app.prompts import grounding, pools
from app.states.checkin_states import SituationalStates

log = logging.getLogger("assistant.situational")
router = Router(name="situational")


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def start_situational(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "situational")
    checkin = CheckIn(date=_today(), slot="situational", checkin_id=checkin_id)
    checkin.set("body_tension", [])
    await state.update_data(checkin=checkin.to_dict())

    greeting = await ctx.rotator.next_item("greeting_situational", pools.GREETING_SITUATIONAL)
    q = await ctx.rotator.next_item("situational_body", pools.SITUATIONAL_BODY)
    await message.answer(f"{greeting}\n\n{q}", reply_markup=with_cancel(body_kb([], full=False)))
    await state.set_state(SituationalStates.body_tension)


@router.callback_query(SituationalStates.body_tension, BodyCB.filter())
async def on_body_toggle(cb: CallbackQuery, callback_data: BodyCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("body_tension", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=body_kb(selected, full=False))
    await cb.answer()


@router.callback_query(SituationalStates.body_tension, ActionCB.filter(F.name == "done"))
async def on_body_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Отметил.")
    q = await ctx.rotator.next_item("situational_what", pools.SITUATIONAL_WHAT)
    await cb.message.answer(q, reply_markup=situational_what_kb())
    await state.set_state(SituationalStates.what)
    await cb.answer()


@router.callback_query(SituationalStates.what, SituWhatCB.filter())
async def on_what(cb: CallbackQuery, callback_data: SituWhatCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    kind, _, value = callback_data.value.partition("|")
    if kind == "emo":
        checkin.set("emotions", [value])
    else:  # trig
        checkin.set("trigger_type", value)
    await _save(state, checkin)
    await cb.message.edit_text("Отметил.")
    q = await ctx.rotator.next_item("situational_intensity", pools.SITUATIONAL_INTENSITY)
    await cb.message.answer(q, reply_markup=scales.intensity_kb())
    await state.set_state(SituationalStates.intensity)
    await cb.answer()


# ---- «Своё» (свободный текст) ----

@router.callback_query(SituationalStates.body_tension, ActionCB.filter(F.name == "other"))
async def on_body_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(SituationalStates.body_tension, F.text)
async def on_body_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "тело")
    await _save(state, checkin)
    await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


@router.callback_query(SituationalStates.what, ActionCB.filter(F.name == "other"))
async def on_what_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(SituationalStates.what, F.text)
async def on_what_other_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "что")
    await _save(state, checkin)
    q = await ctx.rotator.next_item("situational_intensity", pools.SITUATIONAL_INTENSITY)
    await message.answer(q, reply_markup=scales.intensity_kb())
    await state.set_state(SituationalStates.intensity)


@router.callback_query(SituationalStates.intensity, ScaleCB.filter(F.metric == "intensity"))
async def on_intensity(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("intensity", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Интенсивность: {callback_data.value}/10")
    await cb.message.answer(
        "Одна фраза — что за мысль крутится? (опц.)",
        reply_markup=skip_kb(),
    )
    await state.set_state(SituationalStates.free_text)
    await cb.answer()


@router.callback_query(SituationalStates.free_text, ActionCB.filter(F.name == "skip"))
async def on_free_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.delete_reply_markup()
    await _finish(cb.message, state, ctx)
    await cb.answer()


@router.message(SituationalStates.free_text, F.text)
async def on_free_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
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
    # ВСЕГДА заземление в конце
    await message.answer(grounding.situational())
    await state.clear()
