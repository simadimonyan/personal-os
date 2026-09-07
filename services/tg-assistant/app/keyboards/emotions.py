"""Клавиатура эмоций — МУЛЬТИВЫБОР (§1.1 MASTER-PLAN).

- toggle: выбранная эмоция показывает ✓;
- anger («Злость») — отдельной видимой кнопкой (терапевтически разрешаем злость);
- «Не считывается» обязательно (честный ответ при диссоциации);
- кнопка «Готово».

Сообщение редактируется при каждом toggle (ADR-6: один месседж с ✓-отметками).
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import EMOTIONS

# машинный ключ -> подпись на кнопке (порядок задаёт раскладку)
EMOTION_LABELS: dict[str, str] = {
    "anxiety": "Тревога",
    "shame": "Стыд",
    "envy": "Зависть",
    "loneliness": "Одиночество",
    "anger": "Злость",          # отдельной видимой кнопкой
    "sadness": "Грусть",
    "apathy": "Апатия",
    "joy": "Радость",
    "calm": "Спокойствие",
    "hope": "Надежда",
    "unreadable": "Не считывается",
}


class EmotionCB(CallbackData, prefix="emo"):
    value: str   # машинный ключ эмоции из EMOTIONS


def emotions_kb(selected: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in EMOTIONS:
        label = EMOTION_LABELS[key]
        if key in selected:
            label = f"✓ {label}"
        builder.button(text=label, callback_data=EmotionCB(value=key))
    # «Своё» (свободный текст) + «Готово» отдельной строкой через ActionCB
    from app.keyboards.common import OTHER_BTN_TEXT, ActionCB

    builder.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    builder.button(text="Готово", callback_data=ActionCB(name="done"))
    # 2 в ряд эмоции (11 шт), последняя строка — Своё + Готово
    builder.adjust(2, 2, 2, 2, 2, 1, 2)
    return builder.as_markup()
