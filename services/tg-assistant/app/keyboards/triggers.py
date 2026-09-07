"""Клавиатура категорий триггеров (§3.2, §3.4 MASTER-PLAN).

Одиночный выбор (enum trigger_type). Включает служебные «Не пойму» / «Не было».
Для ситуативного — сокращённый набор «на что похоже» (эмоции + триггеры).
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import TRIGGER_TYPES

TRIGGER_LABELS: dict[str, str] = {
    "сравнение_с_другими": "Сравнение",
    "потеря_контроля": "Потеря контроля",
    "ревность_сигнал": "Ревность",
    "насмешка_обесценивание": "Насмешка",
    "соц_ненормальность": "Соц. ненормальность",
    "усталость_тело": "Усталость",
    "отвержение": "Отвержение",
    "неизвестно": "Не пойму",
    "не_было": "Не было",
}


class TriggerCB(CallbackData, prefix="trig"):
    value: str   # машинный ключ триггера


def triggers_kb() -> InlineKeyboardMarkup:
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for key in TRIGGER_TYPES:
        builder.button(text=TRIGGER_LABELS[key], callback_data=TriggerCB(value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()


# Ситуативный «на что похоже» (§3.4 п.2): смесь эмоций и триггеров одной кнопкой.
# Значения мапятся: эмоции -> emotions, триггеры -> trigger_type (см. handler).
# Разделитель вида/ключа — '|', т.к. aiogram CallbackData запрещает ':' в значении.
SITUATIONAL_WHAT_OPTIONS: dict[str, str] = {
    "emo|anxiety": "Тревога",
    "emo|anger": "Злость",
    "emo|apathy": "Пустота",
    "emo|shame": "Стыд / я плохой",
    "trig|потеря_контроля": "Прокрут мыслей",
    "emo|envy": "Ревность",
    "trig|неизвестно": "Не пойму",
}


class SituWhatCB(CallbackData, prefix="situw"):
    value: str   # 'emo|<key>' | 'trig|<key>'


def situational_what_kb() -> InlineKeyboardMarkup:
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for value, label in SITUATIONAL_WHAT_OPTIONS.items():
        builder.button(text=label, callback_data=SituWhatCB(value=value))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()
