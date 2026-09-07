"""Общие кнопки и callback-фабрики: «Расширить», «На сегодня хватит», «Пропустить», «Готово».

Подписи кнопок здесь — это UI-микротексты, держим их рядом с клавиатурами
(а не в prompts/, который про диалоговые формулировки).
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ActionCB(CallbackData, prefix="act"):
    name: str   # expand | enough | skip | done | yes | no | other | cancel


# Текст кнопки «вписать своё» — свободный текст вместо выбора из клавиатуры.
# Назван «Своё», чтобы не путать с категорией «Другое» (напр. в темах руминаций).
OTHER_BTN_TEXT = "✏️ Своё"
# Подсказка, когда пользователь нажал «Своё».
OTHER_PROMPT = "Впиши своими словами одним сообщением:"


def _btn(text: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=ActionCB(name=name).pack())


def skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("Пропустить", "skip")]])


def done_kb(skippable: bool = True) -> InlineKeyboardMarkup:
    row = [_btn("Готово", "done")]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def expand_or_enough_kb() -> InlineKeyboardMarkup:
    """Вечерний выбор: расширить (deep) или хватит."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            _btn("Расширить →", "expand"),
            _btn("На сегодня хватит", "enough"),
        ]]
    )


def yes_no_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            _btn("Да", "yes"),
            _btn("Нет", "no"),
            _btn("Пропустить", "skip"),
        ]]
    )


# Кнопка «Отмена» — выход из любого режима ввода (FSM) без записи.
# Ловится глобальным обработчиком app.handlers.cancel (StateFilter("*")).
CANCEL_BTN_TEXT = "❌ Отмена"


def cancel_button() -> InlineKeyboardButton:
    return _btn(CANCEL_BTN_TEXT, "cancel")


def cancel_kb() -> InlineKeyboardMarkup:
    """Самостоятельная клавиатура только с «Отмена» — для голых текстовых промптов."""
    return InlineKeyboardMarkup(inline_keyboard=[[cancel_button()]])


def with_cancel(markup: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """Добавляет ряд «Отмена» к существующей inline-клавиатуре (или создаёт новую)."""
    rows = [list(row) for row in markup.inline_keyboard] if markup else []
    rows.append([cancel_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)
