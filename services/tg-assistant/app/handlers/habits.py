"""Привычки: настраиваемый список (хорошие/вредные) + ежедневный трекинг.

Всё управление — на кнопках (хаб «🌱 Привычки»): отметить за день, добавить,
убрать. Название новой привычки вводится одним сообщением (иначе никак), тип —
кнопкой. Текстовые команды /habit_add · /habit_del · /habit_list оставлены как
запасной путь.

Ежедневный экран — тумблеры без счётчиков и стриков: хорошую отмечаешь, если
сделал; вредную — если сорвался (было). Динамика и счётчики — только в 📈.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain import habits as hb
from app.domain.checkin import CheckIn
from app.handlers._service import AppContext, finalize_checkin
from app.keyboards import habits as hkb
from app.keyboards.common import ActionCB, cancel_kb, with_cancel
from app.states.checkin_states import HabitsStates

log = logging.getLogger("assistant.habits")
router = Router(name="habits")

_SEL_KEY = "habits_sel"  # временный: выбранные id (не уходит во frontmatter)
_MARK_Q = (
    "🌱 Привычки за сегодня.\n"
    "🌱 хорошую — отметь, если сделал.\n"
    "🚫 вредную — отметь, если сорвался (было).\n"
    "Что не отметил — просто не было. Никаких обнулений и наказаний."
)
_CLOSE = "Записал. Без счётчиков — динамику смотри в 📈 Аналитике."
_HUB_HINT = "🌱 Привычки. Отмечай день, добавляй и убирай — всё кнопками."


def _today() -> str:
    return date_cls.today().isoformat()


def _load(data: dict) -> CheckIn:
    return CheckIn.from_dict(data["checkin"])


async def _save(state: FSMContext, checkin: CheckIn) -> None:
    await state.update_data(checkin=checkin.to_dict())


# ============================================================================
# Хаб — точка входа с кнопками
# ============================================================================

def _hub_kb(habits: list[hb.Habit]) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    if habits:
        kb.append([InlineKeyboardButton(text="✅ Отметить за день", callback_data="hb:mark")])
    row = [InlineKeyboardButton(text="➕ Добавить", callback_data="hb:add")]
    if habits:
        row.append(InlineKeyboardButton(text="🗑 Убрать", callback_data="hb:del"))
    kb.append(row)
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _hub_text(habits: list[hb.Habit]) -> str:
    if not habits:
        return (
            "🌱 Привычки\n\nПока пусто. Нажми «➕ Добавить» — заведи первую "
            "(хорошую или вредную)."
        )
    good, bad = hb.split_by_kind(habits)
    lines = ["🌱 Привычки", ""]
    if good:
        lines.append("🌱 Хорошие:")
        lines += [f"  • {h.name}" for h in good]
    if bad:
        if good:
            lines.append("")
        lines.append("🚫 Вредные:")
        lines += [f"  • {h.name}" for h in bad]
    return "\n".join(lines)


async def open_hub(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    habits = await hb.list_habits(ctx.repos)
    await message.answer(_hub_text(habits), reply_markup=_hub_kb(habits))


async def _hub_edit(cb: CallbackQuery, ctx: AppContext) -> None:
    habits = await hb.list_habits(ctx.repos)
    await cb.message.edit_text(_hub_text(habits), reply_markup=_hub_kb(habits))


@router.message(Command("habits"))
async def cmd_habits(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await open_hub(message, state, ctx)


# alias для меню/оркестратора: «🌱 Привычки» открывает хаб
async def start_habits(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await open_hub(message, state, ctx)


@router.callback_query(F.data == "hb:hub")
async def cb_hub(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await state.clear()
    await _hub_edit(cb, ctx)
    await cb.answer()


# ============================================================================
# Добавление привычки (тип кнопкой → название текстом)
# ============================================================================

def _kind_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌱 Хорошая", callback_data="hb:addkind:good"),
         InlineKeyboardButton(text="🚫 Вредная", callback_data="hb:addkind:bad")],
        [InlineKeyboardButton(text="← Назад", callback_data="hb:hub")],
    ])


@router.callback_query(F.data == "hb:add")
async def cb_add(cb: CallbackQuery) -> None:
    await cb.message.edit_text("Какую привычку добавить?", reply_markup=_kind_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("hb:addkind:"))
async def cb_add_kind(cb: CallbackQuery, state: FSMContext) -> None:
    kind = cb.data.split(":")[-1]
    kind = hb.GOOD if kind not in (hb.GOOD, hb.BAD) else kind
    await state.update_data(new_kind=kind)
    await state.set_state(HabitsStates.adding)
    kind_ru = "хорошей" if kind == hb.GOOD else "вредной"
    await cb.message.edit_text(f"Название {kind_ru} привычки — одним сообщением.")
    await cb.message.answer("Жду название…", reply_markup=cancel_kb())
    await cb.answer()


@router.message(HabitsStates.adding, F.text)
async def on_add_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    data = await state.get_data()
    kind = data.get("new_kind", hb.GOOD)
    habit = await hb.add_habit(ctx.repos, kind, message.text.strip())
    await state.clear()
    kind_ru = "хорошая" if habit.is_good else "вредная"
    habits = await hb.list_habits(ctx.repos)
    await message.answer(f"Добавил ({kind_ru}): {habit.name}.")
    await message.answer(_HUB_HINT, reply_markup=_hub_kb(habits))


# ============================================================================
# Удаление привычки (список кнопок 🗑)
# ============================================================================

def _del_kb(habits: list[hb.Habit]) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for h in habits:
        icon = "🌱" if h.is_good else "🚫"
        kb.append([InlineKeyboardButton(text=f"🗑 {icon} {h.name}",
                                        callback_data=f"hb:delone:{h.id}")])
    kb.append([InlineKeyboardButton(text="← Готово", callback_data="hb:hub")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data == "hb:del")
async def cb_del(cb: CallbackQuery, ctx: AppContext) -> None:
    habits = await hb.list_habits(ctx.repos)
    if not habits:
        await _hub_edit(cb, ctx)
        await cb.answer()
        return
    await cb.message.edit_text("Тапни, чтобы убрать:", reply_markup=_del_kb(habits))
    await cb.answer()


@router.callback_query(F.data.startswith("hb:delone:"))
async def cb_del_one(cb: CallbackQuery, ctx: AppContext) -> None:
    hid = cb.data.split(":")[-1]
    removed = await hb.remove_habit(ctx.repos, hid)
    habits = await hb.list_habits(ctx.repos)
    if not habits:
        await _hub_edit(cb, ctx)
    else:
        await cb.message.edit_reply_markup(reply_markup=_del_kb(habits))
    await cb.answer(f"Убрал: {removed.name}" if removed else "Не нашёл")


# ============================================================================
# Ежедневный трекинг (тумблеры)
# ============================================================================

async def _start_marking(message: Message, state: FSMContext, ctx: AppContext) -> None:
    habits = await hb.list_habits(ctx.repos)
    if not habits:
        await open_hub(message, state, ctx)
        return
    checkin_id = await ctx.repos.checkins.create(_today(), "habits")
    checkin = CheckIn(date=_today(), slot="habits", checkin_id=checkin_id)
    checkin.set(_SEL_KEY, [])
    await state.update_data(checkin=checkin.to_dict())
    await message.answer(_MARK_Q, reply_markup=with_cancel(hkb.habits_kb(habits, [])))
    await state.set_state(HabitsStates.mark)


@router.callback_query(F.data == "hb:mark")
async def cb_mark(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await _start_marking(cb.message, state, ctx)


@router.callback_query(HabitsStates.mark, hkb.HabitCB.filter())
async def on_toggle(cb: CallbackQuery, callback_data: hkb.HabitCB, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    selected = checkin.toggle_multi(_SEL_KEY, callback_data.value)
    await _save(state, checkin)
    habits = await hb.list_habits(ctx.repos)
    await cb.message.edit_reply_markup(reply_markup=hkb.habits_kb(habits, selected))
    await cb.answer()


@router.callback_query(HabitsStates.mark, ActionCB.filter(F.name == "done"))
async def on_done(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    checkin = _load(await state.get_data())
    selected = set(checkin.get(_SEL_KEY, []))
    habits = await hb.list_habits(ctx.repos)
    done = [h.name for h in habits if h.is_good and h.id in selected]
    slip = [h.name for h in habits if not h.is_good and h.id in selected]
    # чистим временный ключ, кладём итог строковыми списками (чистый frontmatter)
    checkin.answers.pop(_SEL_KEY, None)
    checkin.set("habits_done", done)      # хорошие: сделал
    checkin.set("habits_slip", slip)      # вредные: было / сорвался
    await finalize_checkin(ctx, checkin)
    await cb.message.edit_text("Отметил.")
    await cb.message.answer(_CLOSE)
    await state.clear()
    await cb.answer()


# ============================================================================
# Текстовые команды — запасной путь
# ============================================================================

@router.message(Command("habit_add"))
async def cmd_habit_add(message: Message, ctx: AppContext) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or parts[1] not in (hb.GOOD, hb.BAD):
        await message.answer(
            "Формат: /habit_add <good|bad> <название>\n"
            "Или проще — кнопкой в «🌱 Привычки» → ➕ Добавить."
        )
        return
    habit = await hb.add_habit(ctx.repos, parts[1], parts[2])
    kind_ru = "хорошая" if habit.is_good else "вредная"
    await message.answer(f"Добавил ({kind_ru}): {habit.name}.")


@router.message(Command("habit_del"))
async def cmd_habit_del(message: Message, ctx: AppContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /habit_del <номер или название>. Или кнопкой: «🌱 Привычки» → 🗑 Убрать.")
        return
    removed = await hb.remove_habit(ctx.repos, parts[1].strip())
    await message.answer(f"Убрал: {removed.name}." if removed else "Не нашёл такую привычку.")


@router.message(Command("habit_list"))
async def cmd_habit_list(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await open_hub(message, state, ctx)
