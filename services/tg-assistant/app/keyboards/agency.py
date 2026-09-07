"""Клавиатуры для метрик агентности/авторства (слоты agency, deed + обзоры).

Метрики действий, не состояния. Никаких числовых шкал — только факты-выборы.
Enum-метрики переиспользуют EnumCB (metric, value); мультивыборы (база, месячные
факты) — свои CallbackData по образцу RegulationCB/BodyCB.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.metrics import (
    ADDRESSEE,
    AGENCY_FORK,
    BASE_DONE,
    DEED_ACTION,
    DEED_ADDRESSEE,
    DEED_KIND,
)
from app.keyboards.common import ActionCB
from app.keyboards.enums import EnumCB

# ---- подписи (машинный ключ -> человекочитаемо) ----

AGENCY_FORK_LABELS = {
    "sam": "Решил сам",
    "obey": "Подчинился",
    "spite": "Поспорил-назло",
    "none": "Развилки не было",
}
ADDRESSEE_LABELS = {
    "close": "Близкие",
    "friends": "Друзья",
    "work": "Дело / учёба",
    "self": "Сам с собой",
}
BASE_LABELS = {"sleep": "Спал норм", "body": "Двигал тело", "walk": "Выходил походить"}
DEED_KIND_LABELS = {
    "A": "Столкнулся / накрыло",
    "B": "Сказал тяжёлое / попросил",
    "C": "Начал контакт первым",
}
DEED_ACTION_LABELS = {
    "out": "Нашёл выход",
    "swallow": "Проглотил из страха",
    "burst": "Сорвался",
}
DEED_ADDRESSEE_LABELS = {"support": "Тому, кто поддержит", "judge": "Тому, кто оценивает"}


def _enum_kb(metric: str, options: tuple[str, ...], labels: dict[str, str],
             *, columns: int = 1, other: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in options:
        b.button(text=labels[key], callback_data=EnumCB(metric=metric, value=key))
    if other:
        from app.keyboards.common import OTHER_BTN_TEXT
        b.button(text=OTHER_BTN_TEXT, callback_data=ActionCB(name="other"))
    # ряды по `columns` штук для вариантов + «своё» отдельным рядом
    n_full, rem = divmod(len(options), columns)
    rows = [columns] * n_full + ([rem] if rem else [])
    if other:
        rows.append(1)
    b.adjust(*(rows or [len(options)]))
    return b.as_markup()


# ---- agency ----

def agency_fork_kb() -> InlineKeyboardMarkup:
    return _enum_kb("agency_fork", AGENCY_FORK, AGENCY_FORK_LABELS, columns=1)


def agency_addressee_kb() -> InlineKeyboardMarkup:
    return _enum_kb("agency_addressee", ADDRESSEE, ADDRESSEE_LABELS, columns=2, other=True)


class BaseCB(CallbackData, prefix="base"):
    value: str


def base_kb(selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in BASE_DONE:
        label = BASE_LABELS[key]
        if key in selected:
            label = f"✓ {label}"
        b.button(text=label, callback_data=BaseCB(value=key))
    b.button(text="Готово", callback_data=ActionCB(name="done"))
    b.adjust(1, 1, 1, 1)
    return b.as_markup()


# ---- deed (событие A/B/C) ----

def deed_kind_kb() -> InlineKeyboardMarkup:
    return _enum_kb("deed_kind", DEED_KIND, DEED_KIND_LABELS, columns=1)


def deed_action_kb() -> InlineKeyboardMarkup:
    return _enum_kb("deed_action", DEED_ACTION, DEED_ACTION_LABELS, columns=1)


def deed_addressee_kb() -> InlineKeyboardMarkup:
    return _enum_kb("deed_addressee", DEED_ADDRESSEE, DEED_ADDRESSEE_LABELS, columns=2)


# ---- обзор недели (3 вопроса, категориальные ответы) ----

WEEKLY_Q1_LABELS = {"self_only": "Только там, где решаю один", "live": "И с живыми людьми"}
WEEKLY_Q2_LABELS = {"yes": "Да, по делу", "no": "Нет"}
WEEKLY_Q3_LABELS = {"support": "Тому, кто поддержит", "judge": "Тому, кто оценивает", "none": "Не было"}


def weekly_q1_kb() -> InlineKeyboardMarkup:
    return _enum_kb("weekly_q1", tuple(WEEKLY_Q1_LABELS), WEEKLY_Q1_LABELS, columns=1)


def weekly_q2_kb() -> InlineKeyboardMarkup:
    return _enum_kb("weekly_q2", tuple(WEEKLY_Q2_LABELS), WEEKLY_Q2_LABELS, columns=2)


def weekly_q3_kb() -> InlineKeyboardMarkup:
    return _enum_kb("weekly_q3", tuple(WEEKLY_Q3_LABELS), WEEKLY_Q3_LABELS, columns=1)


# ---- обзор месяца (7 фактов да/нет, один экран тумблеров) ----

# 7 обобщённых фактов авторства — про действия, не про конкретные проекты/людей
MONTHLY_FACTS: tuple[tuple[str, str], ...] = (
    ("boundary", "Сказал «нет» или обозначил границу близким"),
    ("position", "В споре держал позицию по делу, а не защищал себя"),
    ("goal", "Сделал реальный шаг к своей большой цели"),
    ("for_self", "Сделал что-то для себя (сделал бы, даже если никто не узнает)"),
    ("ask", "Попросил о помощи или сказал тяжёлое тому, кто поддержит"),
    ("initiative", "Первым начал контакт — разговор, знакомство"),
    ("base", "База (сон, тело, движение) была большинство дней"),
)
MONTHLY_FACT_LABELS = dict(MONTHLY_FACTS)


class MonthFactCB(CallbackData, prefix="mfact"):
    value: str


def monthly_kb(selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in MONTHLY_FACTS:
        mark = "✅" if key in selected else "⬜️"
        b.button(text=f"{mark} {label}", callback_data=MonthFactCB(value=key))
    b.button(text="Готово", callback_data=ActionCB(name="done"))
    b.adjust(*([1] * len(MONTHLY_FACTS)), 1)
    return b.as_markup()
