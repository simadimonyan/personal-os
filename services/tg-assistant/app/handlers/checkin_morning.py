"""Утренний чек-ин — полный FSM (§3.1 MASTER-PLAN).

Поток: valence → arousal → ротируемый q3 (sleep / first-emotion) →
опц. body_word → подтверждение → запись в SQLite + outbox(Obsidian).

Старт инициируется либо планировщиком (start_morning), либо вручную из меню.
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
from app.keyboards.common import ActionCB, with_cancel
from app.keyboards.emotions import EmotionCB, emotions_kb
from app.keyboards.enums import EnumCB, sleep_rest_kb
from app.keyboards.scales import ScaleCB
from app.obsidian import note_body
from app.prompts import confirmations, pools
from app.states.checkin_states import MorningStates

log = logging.getLogger("assistant.morning")
router = Router(name="checkin_morning")


def _today() -> str:
    return date_cls.today().isoformat()


async def start_morning(message: Message, state: FSMContext, ctx: AppContext) -> None:
    """Запускает утренний чек-ин: создаёт строку checkins и шлёт первый вопрос."""
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "morning")
    checkin = CheckIn(date=_today(), slot="morning", checkin_id=checkin_id)
    await state.update_data(checkin=checkin.to_dict())

    greeting = await ctx.rotator.next_item("greeting_morning", pools.GREETING_MORNING)
    q = await ctx.rotator.next_item("q_valence", pools.Q_VALENCE)
    await message.answer(f"{greeting}\n\n{q}", reply_markup=with_cancel(scales.valence_kb()))
    await state.set_state(MorningStates.valence)


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


@router.callback_query(MorningStates.valence, ScaleCB.filter(F.metric == "valence"))
async def on_valence(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("valence", callback_data.value)
    await _save(state, checkin)

    q = await ctx.rotator.next_item("q_arousal", pools.Q_AROUSAL)
    await cb.message.edit_text(f"Тон: {note_body.fmt_valence(callback_data.value)}")
    await cb.message.answer(q, reply_markup=scales.arousal_kb())
    await state.set_state(MorningStates.arousal)
    await cb.answer()


@router.callback_query(MorningStates.arousal, ScaleCB.filter(F.metric == "arousal"))
async def on_arousal(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("arousal", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Энергия: {callback_data.value}")

    # ротируемый третий вопрос
    idx = await ctx.rotator.next_index("morning_q3", len(pools.MORNING_Q3))
    q3 = pools.MORNING_Q3[idx]
    await state.update_data(q3_kind=q3["kind"])

    if q3["kind"] == "sleep":
        await cb.message.answer(q3["text"], reply_markup=sleep_rest_kb())
    else:  # emotion
        checkin.set("emotions", [])
        await _save(state, checkin)
        await cb.message.answer(q3["text"] + "\n(можно несколько, потом «Готово»)", reply_markup=emotions_kb([]))
    await state.set_state(MorningStates.q3)
    await cb.answer()


# --- q3 = sleep ---
@router.callback_query(MorningStates.q3, EnumCB.filter(F.metric == "sleep_quality"))
async def on_sleep(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("sleep_quality", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Сон: {callback_data.value}")
    await _ask_body_word(cb.message, state, ctx)
    await cb.answer()


# --- q3 = emotion (мультивыбор) ---
@router.callback_query(MorningStates.q3, EmotionCB.filter())
async def on_emotion_toggle(cb: CallbackQuery, callback_data: EmotionCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("emotions", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=emotions_kb(selected))
    await cb.answer()


@router.callback_query(MorningStates.q3, ActionCB.filter(F.name == "done"))
async def on_emotion_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    sel = checkin.get("emotions", [])
    await cb.message.edit_text("Эмоции отмечены." if sel else "Без эмоций.")
    await _ask_body_word(cb.message, state, ctx)
    await cb.answer()


# ---- «Своё» для q3 (sleep — один выбор; emotion — мультивыбор) ----

@router.callback_query(MorningStates.q3, ActionCB.filter(F.name == "other"))
async def on_q3_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(MorningStates.q3, F.text)
async def on_q3_other_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    checkin = _load(data)
    text = message.text.strip()
    if data.get("q3_kind") == "sleep":
        # один выбор — текст становится значением сна, идём дальше
        checkin.set("sleep_quality", text)
        await _save(state, checkin)
        await _ask_body_word(message, state, ctx)
    else:
        # эмоции — мультивыбор: добавляем в свободное поле, остаёмся
        append_free(checkin, text, "эмоции")
        await _save(state, checkin)
        await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


async def _ask_body_word(message: Message, state: FSMContext, ctx: AppContext) -> None:
    from app.keyboards.common import skip_kb

    prompt = await ctx.rotator.next_item("body_word_prompt", pools.BODY_WORD_PROMPT)
    await message.answer(prompt, reply_markup=skip_kb())
    await state.set_state(MorningStates.body_word)


@router.callback_query(MorningStates.body_word, ActionCB.filter(F.name == "skip"))
async def on_body_word_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text(confirmations.skipped())
    await _finish(cb.message, state, ctx)
    await cb.answer()


@router.message(MorningStates.body_word, F.text)
async def on_body_word_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    # «одно слово» — берём первое слово, против перфекционизма
    word = message.text.strip().split()[0] if message.text.strip() else None
    if word:
        checkin.set("body_word", word)
        await _save(state, checkin)
    await _finish(message, state, ctx)


async def _finish(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    flags = await finalize_checkin(ctx, checkin)
    await message.answer(confirmations.confirm_morning())
    follow = flag_followup(flags)
    if follow:
        await message.answer(follow)
    await state.clear()
