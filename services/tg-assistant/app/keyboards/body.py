"""Клавиатура зон тела — МУЛЬТИВЫБОР (§1.1 MASTER-PLAN).

«Ничего не замечаю» обязательно — диагностически ценный ответ (маркер диссоциации),
не пустая клетка.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import BODY_ZONES

ZONE_LABELS: dict[str, str] = {
    "грудь": "Грудь",
    "горло": "Горло",
    "челюсть": "Челюсть",
    "плечи": "Плечи",
    "живот": "Живот",
    "голова": "Голова",
    "глаза": "Глаза",
    "спина": "Спина",
    "руки": "Руки",
    "ничего_не_замечаю": "Ничего не замечаю",
}


class BodyCB(CallbackData, prefix="body"):
    value: str   # машинный ключ зоны из BODY_ZONES


def body_kb(selected: list[str], full: bool = True) -> InlineKeyboardMarkup:
    """full=True — все зоны; full=False — сокращённый набор для ситуативного (§3.4)."""
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    zones = BODY_ZONES if full else (
        "грудь", "горло", "живот", "голова", "плечи", "челюсть", "ничего_не_замечаю",
    )
    builder = InlineKeyboardBuilder()
    for key in zones:
        label = ZONE_LABELS[key]
        if key in selected:
            label = f"✓ {label}"
        builder.button(text=label, callback_data=BodyCB(value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.button(text="Готово", callback_data=ActionCB(name="done"))

    if full:
        builder.adjust(3, 3, 3, 1, 2)
    else:
        builder.adjust(3, 3, 1, 2)
    return builder.as_markup()
