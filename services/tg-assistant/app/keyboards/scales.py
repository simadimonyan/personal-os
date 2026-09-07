"""Клавиатуры-шкалы (§3 MASTER-PLAN).

valence: −5..+5 (одна-две строки), arousal/anxiety/self_criticism: 1..10.
Ответ одним тапом, при выборе сообщение редактируется (показывает выбор).

CallbackData factory ScaleCB: metric — машинный ключ метрики, value — выбранное число.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class ScaleCB(CallbackData, prefix="scale"):
    metric: str       # машинный ключ: valence|arousal|anxiety|self_criticism|felt_vs_analyzed|intensity
    value: int


def valence_kb() -> InlineKeyboardMarkup:
    """−5..+5. Две строки по 6/5, с эмодзи на краях."""
    builder = InlineKeyboardBuilder()
    labels = {
        -5: "😣 −5", -4: "−4", -3: "−3", -2: "−2", -1: "−1",
        0: "0",
        1: "+1", 2: "+2", 3: "+3", 4: "+4", 5: "+5 😄",
    }
    for v in range(-5, 6):
        builder.button(text=labels[v], callback_data=ScaleCB(metric="valence", value=v))
    builder.adjust(6, 5)
    return builder.as_markup()


def _scale_1_10(metric: str, low_emoji: str = "", high_emoji: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for v in range(1, 11):
        text = str(v)
        if v == 1 and low_emoji:
            text = f"{low_emoji} 1"
        elif v == 10 and high_emoji:
            text = f"10 {high_emoji}"
        builder.button(text=text, callback_data=ScaleCB(metric=metric, value=v))
    builder.adjust(5, 5)
    return builder.as_markup()


def arousal_kb() -> InlineKeyboardMarkup:
    return _scale_1_10("arousal", low_emoji="🪫", high_emoji="⚡")


def anxiety_kb() -> InlineKeyboardMarkup:
    return _scale_1_10("anxiety")


def self_criticism_kb() -> InlineKeyboardMarkup:
    return _scale_1_10("self_criticism")


def felt_vs_analyzed_kb() -> InlineKeyboardMarkup:
    """0..10 slider. 0 = только думал, 10 = прожил."""
    builder = InlineKeyboardBuilder()
    for v in range(0, 11):
        text = str(v)
        if v == 0:
            text = "0 думал"
        elif v == 10:
            text = "прожил 10"
        builder.button(text=text, callback_data=ScaleCB(metric="felt_vs_analyzed", value=v))
    builder.adjust(6, 5)
    return builder.as_markup()


def intensity_kb() -> InlineKeyboardMarkup:
    """0..10 интенсивность ситуативного."""
    builder = InlineKeyboardBuilder()
    for v in range(0, 11):
        builder.button(text=str(v), callback_data=ScaleCB(metric="intensity", value=v))
    builder.adjust(6, 5)
    return builder.as_markup()
