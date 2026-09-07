"""Одиночный выбор для enum-метрик: sleep_quality, rumination_level, human_contact.

§3.1 (сон), §3.3 (руминации, контакт). Один тап — одно значение.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import (
    HUMAN_CONTACT,
    RUMINATION_TOPICS,
    SLEEP_QUALITY,
)


class EnumCB(CallbackData, prefix="enum"):
    metric: str   # sleep_quality | rumination_level | human_contact
    value: str    # машинное значение


# --- sleep_quality (§3.1) ---
SLEEP_LABELS = {
    "глубокий": "Глубокий",
    "рваный": "Рваный",
    "поздний": "Поздний",
    "норма": "Норма",
}

# Краткая версия «как проснулся» (§3.1 п.3 вариант) — мапится на sleep_quality
SLEEP_REST_LABELS = {
    "норма": "Отдохнул",
    "рваный": "Так себе",
    "поздний": "Разбит",
}


def sleep_quality_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in SLEEP_QUALITY:
        builder.button(text=SLEEP_LABELS[key], callback_data=EnumCB(metric="sleep_quality", value=key))
    builder.adjust(2, 2)
    return builder.as_markup()


def sleep_rest_kb() -> InlineKeyboardMarkup:
    """«как проснулся — тело отдохнувшее или нет?»"""
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for key, label in SLEEP_REST_LABELS.items():
        builder.button(text=label, callback_data=EnumCB(metric="sleep_quality", value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.adjust(3, 1)
    return builder.as_markup()


# --- rumination_level (§3.3) ---
RUMINATION_LABELS = {
    "0": "Нет",
    "1": "Немного",
    "2": "Сильно, не отпускало",
}


def rumination_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in RUMINATION_LABELS.items():
        builder.button(text=label, callback_data=EnumCB(metric="rumination_level", value=value))
    builder.adjust(1, 1, 1)
    return builder.as_markup()


# --- human_contact (§3.3) ---
CONTACT_LABELS = {
    "да_глубокий": "Глубокий",
    "да_поверхностный": "Поверхностный",
    "нет": "Не было",
}


def human_contact_kb() -> InlineKeyboardMarkup:
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for key in HUMAN_CONTACT:
        builder.button(text=CONTACT_LABELS[key], callback_data=EnumCB(metric="human_contact", value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.adjust(3, 1)
    return builder.as_markup()


# --- rumination_topics (мультивыбор) ---
RUMINATION_TOPIC_LABELS = {
    "отношения": "Отношения",
    "самообвинение": "Я виноват / разбор себя",
    "работа": "Работа",
    "сравнение": "Сравнение",
    "будущее": "Будущее",
    "другое": "Другое",
}


class RuminationTopicCB(CallbackData, prefix="rumtop"):
    value: str


def rumination_topics_kb(selected: list[str]) -> InlineKeyboardMarkup:
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder = InlineKeyboardBuilder()
    for key in RUMINATION_TOPICS:
        label = RUMINATION_TOPIC_LABELS[key]
        if key in selected:
            label = f"✓ {label}"
        builder.button(text=label, callback_data=RuminationTopicCB(value=key))
    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.button(text="Готово", callback_data=ActionCB(name="done"))
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()
