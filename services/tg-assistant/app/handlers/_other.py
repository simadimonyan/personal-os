"""Поддержка кнопки «Своё» (свободный текст вместо выбора из клавиатуры).

Механика единообразна для всех чек-инов:
- нажатие «Своё» (ActionCB name=other) → бот просит вписать текст, состояние не меняется;
- любой текст в этом состоянии перехватывается соответствующим F.text-хендлером:
    • мультивыбор  → текст добавляется в свободное поле чек-ина, выбор продолжается;
    • один выбор   → текст становится значением поля, и сценарий идёт дальше.

Здесь — только общие утилиты; маршрутизация в каждом хендлере своя (нужно знать
«что дальше» для одиночного выбора).
"""

from __future__ import annotations

from aiogram.types import CallbackQuery

from app.domain.checkin import CheckIn
from app.keyboards.common import OTHER_PROMPT


async def ask_other(cb: CallbackQuery) -> None:
    """Реакция на кнопку «Своё»: просим вписать текст, остаёмся в текущем состоянии."""
    await cb.message.answer(OTHER_PROMPT)
    await cb.answer()


def append_free(checkin: CheckIn, text: str, label: str | None = None) -> None:
    """Добавляет свободный текст в free_text чек-ина (для мультивыбора)."""
    add = text.strip()
    if not add:
        return
    if label:
        add = f"[{label}] {add}"
    checkin.free_text = ((checkin.free_text + " ") if checkin.free_text else "") + add
