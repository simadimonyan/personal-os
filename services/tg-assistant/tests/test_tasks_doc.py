"""Round-trip рендера/парсинга файла задач + парсинг ручных правок."""

from __future__ import annotations

from dataclasses import dataclass

from app.obsidian.tasks_doc import parse_tasks, render_tasks


@dataclass
class _T:
    uid: str
    content: str
    done: bool


def test_render_then_parse_roundtrip():
    tasks = [
        _T("a1b2c3", "Купить молоко", False),
        _T("d4e5f6", "Позвонить врачу", False),
        _T("0099aa", "Сдать отчёт", True),
    ]
    text = render_tasks(tasks, now_iso="2026-06-25T12:00:00")
    parsed = parse_tasks(text)

    by_uid = {p.uid: p for p in parsed}
    assert set(by_uid) == {"a1b2c3", "d4e5f6", "0099aa"}
    assert by_uid["a1b2c3"].content == "Купить молоко"
    assert by_uid["a1b2c3"].done is False
    assert by_uid["0099aa"].done is True


def test_render_sections_present():
    text = render_tasks([_T("x1", "одна", False)], now_iso="2026-06-25T12:00:00")
    assert "## Активные" in text
    assert "## Выполнено" in text
    assert "- [ ] одна ^x1" in text


def test_parse_manual_line_without_uid():
    text = "## Активные\n- [ ] Задача добавленная руками\n- [x] Готовая ^zz9\n"
    parsed = parse_tasks(text)
    assert len(parsed) == 2
    manual = [p for p in parsed if p.uid is None]
    assert len(manual) == 1
    assert manual[0].content == "Задача добавленная руками"
    assert manual[0].done is False


def test_parse_toggled_checkbox():
    # пользователь руками поставил [x]
    text = "- [X] Сделал дело ^abc123\n"
    parsed = parse_tasks(text)
    assert parsed[0].uid == "abc123"
    assert parsed[0].done is True


def test_parse_ignores_non_task_lines():
    text = "# Задачи\n\nкакой-то текст\n- [ ] реальная ^q1\n_пусто_\n"
    parsed = parse_tasks(text)
    assert len(parsed) == 1
    assert parsed[0].uid == "q1"
