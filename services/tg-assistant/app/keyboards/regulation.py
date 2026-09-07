"""Клавиатура «что помогло выдохнуть» — МУЛЬТИВЫБОР (§3.3 MASTER-PLAN)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import REGULATION

REGULATION_LABELS: dict[str, str] = {
    "люди_контакт": "Люди",
    "работа_проект": "Работа",
    "спорт_тело": "Спорт/тело",
    "прогулка_природа": "Природа",
    "сон_отдых": "Сон",
    "дневник_анализ": "Дневник",
    "юмор": "Юмор",
    "ничего": "Ничего",
}


class RegulationCB(CallbackData, prefix="reg"):
    value: str


def regulation_kb(selected: list[str]) -> InlineKeyboardMarkup:
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for key in REGULATION:
        label = REGULATION_LABELS[key]
        if key in selected:
            label = f"✓ {label}"
        builder.button(text=label, callback_data=RegulationCB(value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.button(text="Готово", callback_data=ActionCB(name="done"))
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()
