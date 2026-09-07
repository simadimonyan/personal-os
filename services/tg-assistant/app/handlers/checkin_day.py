"""Дневной чек-ин — полный FSM (§3.2 MASTER-PLAN).

Поток: arousal → мультивыбор emotions → мультивыбор body_tension
(ротируемая формулировка) → trigger_type → запись.
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
from app.keyboards.common import ActionCB, with_cancel
from app.keyboards.emotions import EmotionCB, emotions_kb
from app.keyboards.scales import ScaleCB
from app.keyboards.triggers import TriggerCB, triggers_kb
from app.prompts import confirmations, pools
from app.states.checkin_states import DayStates

log = logging.getLogger("assistant.day")
router = Router(name="checkin_day")


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def start_day(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "day")
    checkin = CheckIn(date=_today(), slot="day", checkin_id=checkin_id)
    checkin.set("emotions", [])
    checkin.set("body_tension", [])
    await state.update_data(checkin=checkin.to_dict())

    greeting = await ctx.rotator.next_item("greeting_day", pools.GREETING_DAY)
    q = await ctx.rotator.next_item("q_arousal", pools.Q_AROUSAL)
    await message.answer(f"{greeting}\n\n{q}", reply_markup=with_cancel(scales.arousal_kb()))
    await state.set_state(DayStates.arousal)


@router.callback_query(DayStates.arousal, ScaleCB.filter(F.metric == "arousal"))
async def on_arousal(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("arousal", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Энергия: {callback_data.value}")
    await cb.message.answer(
        "Что есть прямо сейчас? Отметь всё, что узнаёшь — или «Не считывается».",
        reply_markup=emotions_kb([]),
    )
    await state.set_state(DayStates.emotions)
    await cb.answer()


@router.callback_query(DayStates.emotions, EmotionCB.filter())
async def on_emotion_toggle(cb: CallbackQuery, callback_data: EmotionCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("emotions", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=emotions_kb(selected))
    await cb.answer()


@router.callback_query(DayStates.emotions, ActionCB.filter(F.name == "done"))
async def on_emotion_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Отметил.")
    body_q = await ctx.rotator.next_item("body_question", pools.BODY_QUESTION)
    await cb.message.answer(body_q, reply_markup=body_kb([]))
    await state.set_state(DayStates.body_tension)
    await cb.answer()


@router.callback_query(DayStates.body_tension, BodyCB.filter())
async def on_body_toggle(cb: CallbackQuery, callback_data: BodyCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("body_tension", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=body_kb(selected))
    await cb.answer()


@router.callback_query(DayStates.body_tension, ActionCB.filter(F.name == "done"))
async def on_body_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Отметил.")
    trig_q = await ctx.rotator.next_item("trigger_question", pools.TRIGGER_QUESTION)
    await cb.message.answer(trig_q, reply_markup=triggers_kb())
    await state.set_state(DayStates.trigger)
    await cb.answer()


@router.callback_query(DayStates.trigger, TriggerCB.filter())
async def on_trigger(cb: CallbackQuery, callback_data: TriggerCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("trigger_type", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text("Принял.")
    await _finish(cb.message, state, ctx)
    await cb.answer()


# ---- «Своё» (свободный текст вместо выбора) ----

@router.callback_query(DayStates.emotions, ActionCB.filter(F.name == "other"))
async def on_emotions_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(DayStates.emotions, F.text)
async def on_emotions_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "эмоции")
    await _save(state, checkin)
    await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


@router.callback_query(DayStates.body_tension, ActionCB.filter(F.name == "other"))
async def on_body_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(DayStates.body_tension, F.text)
async def on_body_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "тело")
    await _save(state, checkin)
    await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


@router.callback_query(DayStates.trigger, ActionCB.filter(F.name == "other"))
async def on_trigger_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(DayStates.trigger, F.text)
async def on_trigger_other_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("trigger_type", message.text.strip())
    await _save(state, checkin)
    await _finish(message, state, ctx)


async def _finish(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    flags = await finalize_checkin(ctx, checkin)
    await message.answer(confirmations.confirm_day())
    follow = flag_followup(flags)
    if follow:
        await message.answer(follow)
    await state.clear()
