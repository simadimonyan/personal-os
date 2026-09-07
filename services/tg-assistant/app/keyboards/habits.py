"""Клавиатура ежедневного экрана привычек — тумблеры да/нет по каждой привычке.

Хорошая: галочка = «сделал». Вредная: галочка = «было / сорвался» (отмечаешь
момент срыва). Без счётчиков и стриков прямо в экране — только факт дня.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.habits import Habit
from app.keyboards.common import ActionCB


class HabitCB(CallbackData, prefix="habit"):
    value: str  # id привычки


def habits_kb(habits: list[Habit], selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for h in habits:
        checked = h.id in selected
        box = "✅" if checked else "⬜️"
        # хорошая: галочка = сделал; вредная: галочка = было/сорвался
        icon = "🌱" if h.is_good else "🚫"
        b.button(text=f"{box} {icon} {h.name}", callback_data=HabitCB(value=h.id))
    b.button(text="Готово", callback_data=ActionCB(name="done"))
    b.adjust(*([1] * len(habits)), 1)
    return b.as_markup()
