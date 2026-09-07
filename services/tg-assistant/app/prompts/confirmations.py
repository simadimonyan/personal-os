"""Подтверждения после записи (§3.5 MASTER-PLAN).

Нейтрально-принимающий тон. БЕЗ «молодец / так держать / серия».
После низких баллов — нейтральное подтверждение, без советов.
"""

from __future__ import annotations

import random

CONFIRM_MORNING = [
    "Записал. Спасибо, что отметил.",
    "Отметил. Спасибо.",
    "Записал. Хорошего тебе дня.",
]

CONFIRM_DAY = [
    "Принял.",
    "Записал.",
    "Отметил. Спасибо.",
]

CONFIRM_GENERIC = [
    "Записал. Спасибо, что отметил.",
    "Принял.",
    "Отметил.",
]

# Сообщение при пропуске опционального шага
SKIPPED = [
    "Хорошо, пропустим.",
    "Ок, дальше.",
]


def confirm_morning() -> str:
    return random.choice(CONFIRM_MORNING)


def confirm_day() -> str:
    return random.choice(CONFIRM_DAY)


def confirm_generic() -> str:
    return random.choice(CONFIRM_GENERIC)


def skipped() -> str:
    return random.choice(SKIPPED)
