"""Вечерний чек-ин — полный FSM (§3.3 MASTER-PLAN).

Quick-ядро: valence → anxiety → self_criticism → rumination_level
            (→ rumination_topics если ≥1).
Затем выбор: [Расширить →] (deep dive) или [На сегодня хватит].

Deep dive: felt_vs_analyzed → human_contact → ресурс/радость ИЛИ злость (ротация)
           → свободный текст ресурса → regulation (мультивыбор) →
           опц. свободное поле.

ВСЕГДА завершается заземлением (grounding), не вопросом — даже если всплыл
тяжёлый триггер (§3.3, §5.1: не оставлять с открытой раной).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.domain import habits as hb
from app.domain.checkin import CheckIn
from app.handlers._other import append_free, ask_other
from app.handlers._service import AppContext, finalize_checkin, flag_followup
from app.keyboards import scales
from app.keyboards.common import ActionCB, expand_or_enough_kb, skip_kb, with_cancel
from app.keyboards.menu import MenuCB
from app.keyboards.enums import (
    EnumCB,
    RuminationTopicCB,
    human_contact_kb,
    rumination_kb,
    rumination_topics_kb,
)
from app.keyboards.regulation import RegulationCB, regulation_kb
from app.keyboards.scales import ScaleCB
from app.obsidian import note_body
from app.prompts import confirmations, grounding, pools
from app.states.checkin_states import EveningStates

log = logging.getLogger("assistant.evening")
router = Router(name="checkin_evening")

# каждый N-й вечер (по ротации) вместо ресурса предлагаем слот «Злость» (2-3×/нед)
ANGER_EVERY = 3


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


async def start_evening(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    checkin_id = await ctx.repos.checkins.create(_today(), "evening")
    checkin = CheckIn(date=_today(), slot="evening", checkin_id=checkin_id)
    await state.update_data(checkin=checkin.to_dict())

    greeting = await ctx.rotator.next_item("greeting_evening", pools.GREETING_EVENING)
    q = await ctx.rotator.next_item("q_valence", pools.Q_VALENCE)
    await message.answer(f"{greeting}\n\n{q}", reply_markup=with_cancel(scales.valence_kb()))
    await state.set_state(EveningStates.valence)


# ---- quick-ядро ----

@router.callback_query(EveningStates.valence, ScaleCB.filter(F.metric == "valence"))
async def on_valence(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("valence", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Тон за день: {note_body.fmt_valence(callback_data.value)}")
    q = await ctx.rotator.next_item("q_anxiety", pools.Q_ANXIETY)
    await cb.message.answer(q, reply_markup=scales.anxiety_kb())
    await state.set_state(EveningStates.anxiety)
    await cb.answer()


@router.callback_query(EveningStates.anxiety, ScaleCB.filter(F.metric == "anxiety"))
async def on_anxiety(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("anxiety", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Тревога: {callback_data.value}/10")
    q = await ctx.rotator.next_item("q_self_criticism", pools.Q_SELF_CRITICISM)
    await cb.message.answer(q, reply_markup=scales.self_criticism_kb())
    await state.set_state(EveningStates.self_criticism)
    await cb.answer()


@router.callback_query(EveningStates.self_criticism, ScaleCB.filter(F.metric == "self_criticism"))
async def on_self_criticism(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("self_criticism", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Критик: {callback_data.value}/10")
    q = await ctx.rotator.next_item("rumination_question", pools.RUMINATION_QUESTION)
    await cb.message.answer(q, reply_markup=rumination_kb())
    await state.set_state(EveningStates.rumination)
    await cb.answer()


@router.callback_query(EveningStates.rumination, EnumCB.filter(F.metric == "rumination_level"))
async def on_rumination(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    level = int(callback_data.value)
    checkin.set("rumination_level", level)
    await _save(state, checkin)
    labels = {0: "нет", 1: "немного", 2: "сильно"}
    await cb.message.edit_text(f"Руминации: {labels[level]}")

    if level >= 1:
        checkin.set("rumination_topics", [])
        await _save(state, checkin)
        await cb.message.answer("На какую тему?", reply_markup=rumination_topics_kb([]))
        await state.set_state(EveningStates.rumination_topics)
    else:
        await _offer_expand(cb.message, state)
    await cb.answer()


@router.callback_query(EveningStates.rumination_topics, RuminationTopicCB.filter())
async def on_rum_topic_toggle(cb: CallbackQuery, callback_data: RuminationTopicCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("rumination_topics", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=rumination_topics_kb(selected))
    await cb.answer()


@router.callback_query(EveningStates.rumination_topics, ActionCB.filter(F.name == "done"))
async def on_rum_topic_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_text("Отметил.")
    await _offer_expand(cb.message, state)
    await cb.answer()


async def _offer_expand(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Можно закрыть на этом или добавить пару штрихов.",
        reply_markup=expand_or_enough_kb(),
    )
    # остаёмся в текущем стейте; ловим expand/enough ниже фильтром по name


@router.callback_query(ActionCB.filter(F.name == "enough"))
async def on_enough(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if await state.get_state() is None:
        await cb.answer()
        return
    await cb.message.edit_text(grounding.enough())
    await _finish(cb.message, state, ctx)
    await cb.answer()


@router.callback_query(ActionCB.filter(F.name == "expand"))
async def on_expand(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    if await state.get_state() is None:
        await cb.answer()
        return
    await cb.message.edit_text("Хорошо, ещё пара штрихов.")
    q = await ctx.rotator.next_item("felt_vs_analyzed_question", pools.FELT_VS_ANALYZED_QUESTION)
    await cb.message.answer(q, reply_markup=scales.felt_vs_analyzed_kb())
    await state.set_state(EveningStates.felt_vs_analyzed)
    await cb.answer()


# ---- deep dive ----

@router.callback_query(EveningStates.felt_vs_analyzed, ScaleCB.filter(F.metric == "felt_vs_analyzed"))
async def on_felt(cb: CallbackQuery, callback_data: ScaleCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("felt_vs_analyzed", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text(f"Прожил/продумал: {callback_data.value}/10")
    q = await ctx.rotator.next_item("human_contact_question", pools.HUMAN_CONTACT_QUESTION)
    await cb.message.answer(q, reply_markup=human_contact_kb())
    await state.set_state(EveningStates.human_contact)
    await cb.answer()


@router.callback_query(EveningStates.human_contact, EnumCB.filter(F.metric == "human_contact"))
async def on_contact(cb: CallbackQuery, callback_data: EnumCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("human_contact", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_text("Отметил.")
    await _ask_resource(cb.message, state, ctx)
    await cb.answer()


async def _ask_resource(message: Message, state: FSMContext, ctx: AppContext) -> None:
    # ротация: ресурс/радость или злость (2-3×/нед)
    idx = await ctx.rotator.next_index("evening_resource_or_anger", ANGER_EVERY)
    if idx == 0:
        q = await ctx.rotator.next_item("anger_question", pools.ANGER_QUESTION)
        await state.update_data(resource_kind="anger")
    else:
        q = await ctx.rotator.next_item("resource_question", pools.RESOURCE_QUESTION)
        await state.update_data(resource_kind="resource")
    await message.answer(q + "\n(одна фраза или «Пропустить»)", reply_markup=skip_kb())
    await state.set_state(EveningStates.resource_text)


@router.callback_query(EveningStates.resource_text, ActionCB.filter(F.name == "skip"))
async def on_resource_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text(confirmations.skipped())
    await _ask_regulation(cb.message, state, ctx)
    await cb.answer()


@router.message(EveningStates.resource_text, F.text)
async def on_resource_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    checkin = _load(data)
    text = message.text.strip()
    if data.get("resource_kind") == "anger":
        checkin.free_text = (checkin.free_text or "") + f"[злость] {text}"
    else:
        checkin.set("resource_note", text)
    await _save(state, checkin)
    await _ask_regulation(message, state, ctx)


async def _ask_regulation(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("regulation_used", [])
    await _save(state, checkin)
    q = await ctx.rotator.next_item("regulation_question", pools.REGULATION_QUESTION)
    await message.answer(q, reply_markup=regulation_kb([]))
    await state.set_state(EveningStates.regulation)


@router.callback_query(EveningStates.regulation, RegulationCB.filter())
async def on_regulation_toggle(cb: CallbackQuery, callback_data: RegulationCB, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi("regulation_used", callback_data.value)
    await _save(state, checkin)
    await cb.message.edit_reply_markup(reply_markup=regulation_kb(selected))
    await cb.answer()


@router.callback_query(EveningStates.regulation, ActionCB.filter(F.name == "done"))
async def on_regulation_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text("Отметил.")
    prompt = await ctx.rotator.next_item("free_field_prompt", pools.FREE_FIELD_PROMPT)
    await cb.message.answer(prompt, reply_markup=skip_kb())
    await state.set_state(EveningStates.free_text)
    await cb.answer()


# ---- «Своё» (свободный текст вместо выбора) ----

@router.callback_query(EveningStates.rumination_topics, ActionCB.filter(F.name == "other"))
async def on_rum_topic_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(EveningStates.rumination_topics, F.text)
async def on_rum_topic_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "руминации")
    await _save(state, checkin)
    await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


@router.callback_query(EveningStates.human_contact, ActionCB.filter(F.name == "other"))
async def on_contact_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(EveningStates.human_contact, F.text)
async def on_contact_other_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    checkin.set("human_contact", message.text.strip())
    await _save(state, checkin)
    await _ask_resource(message, state, ctx)


@router.callback_query(EveningStates.regulation, ActionCB.filter(F.name == "other"))
async def on_regulation_other(cb: CallbackQuery, state: FSMContext) -> None:
    await ask_other(cb)


@router.message(EveningStates.regulation, F.text)
async def on_regulation_other_text(message: Message, state: FSMContext) -> None:
    checkin = _load(await state.get_data())
    append_free(checkin, message.text, "помогло")
    await _save(state, checkin)
    await message.answer("Добавил. Можно отметить ещё или нажать «Готово».")


@router.callback_query(EveningStates.free_text, ActionCB.filter(F.name == "skip"))
async def on_free_skip(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.message.edit_text(confirmations.skipped())
    await _finish(cb.message, state, ctx)
    await cb.answer()


@router.message(EveningStates.free_text, F.text)
async def on_free_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    add = message.text.strip()
    checkin.free_text = ((checkin.free_text + " ") if checkin.free_text else "") + add
    await _save(state, checkin)
    await _finish(message, state, ctx)


async def _finish(message: Message, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    flags = await finalize_checkin(ctx, checkin)
    # сначала мягкая реакция на флаги (если есть), затем ВСЕГДА заземление
    follow = flag_followup(flags)
    if follow:
        await message.answer(follow)
    await state.clear()
    # заземление + опц. кнопки, которые едут на вечернем пинге (без отдельного нытья):
    #   «🧭 Развилка дня» (агентность) и «🌱 Привычки» (если заведены и не отмечены).
    # Каждая — отдельная секция, не балл; уже отмеченное сегодня не предлагаем.
    rows: list[list[InlineKeyboardButton]] = []
    if not await ctx.repos.checkins.has_done(_today(), "agency"):
        rows.append([InlineKeyboardButton(text="🧭 Развилка дня",
                                          callback_data=MenuCB(action="agency").pack())])
    if (await hb.list_habits(ctx.repos)) and not await ctx.repos.checkins.has_done(_today(), "habits"):
        rows.append([InlineKeyboardButton(text="🌱 Привычки",
                                          callback_data="hb:mark")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    await message.answer(grounding.evening(), reply_markup=kb)
