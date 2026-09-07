"""Рендер списка задач: нумерация, группировка по времени/проектам/приоритету."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.render import tasks_view


@dataclass
class Row:
    uid: str
    content: str
    priority: int = 1
    due_date: str | None = None
    due_datetime: str | None = None
    project_id: str | None = None
    labels: list[str] = field(default_factory=list)
    done: bool = False


TODAY = date(2026, 6, 26)


def test_time_view_groups_and_numbering():
    rows = [
        Row("a", "Просрочка", due_date="2026-06-20"),
        Row("b", "Сегодня", priority=4, due_date="2026-06-26"),
        Row("c", "Потом", due_date="2026-07-01"),
        Row("d", "Без срока"),
    ]
    text, order = tasks_view.render("time", rows, {}, today=TODAY)
    assert "⚠️ Просрочено" in text and "📅 Сегодня" in text
    assert "📆 Предстоящее" in text and "📥 Без срока" in text
    # нумерация сквозная, порядок секций: overdue → today → upcoming → nodate
    assert order == ["a", "b", "c", "d"]
    assert " 1. " in text and " 4. " in text
    assert "🔴 Сегодня" in text  # приоритет 4 → красный


def test_projects_view_uses_names():
    rows = [Row("a", "Работа-таск", project_id="work"),
            Row("b", "Инбокс-таск")]
    text, order = tasks_view.render("projects", rows, {"work": "Работа"}, today=TODAY)
    assert "🗂 Работа" in text and "📥 Inbox" in text
    assert set(order) == {"a", "b"}


def test_priority_view_orders_high_first():
    rows = [Row("a", "низкий", priority=1), Row("b", "высокий", priority=4)]
    text, order = tasks_view.render("priority", rows, {}, today=TODAY)
    assert order == ["b", "a"]  # p1 (4) выводится раньше p4 (1)


def test_empty():
    text, order = tasks_view.render("time", [], {}, today=TODAY)
    assert order == [] and "Пусто" in text
