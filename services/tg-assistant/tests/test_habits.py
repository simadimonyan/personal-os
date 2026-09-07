"""Тесты привычек: настраиваемый список, рендер секции, счётчики только в аналитике."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain import habits as hb
from app.domain import stats as st
from app.obsidian import note_body as nb
from app.storage.repositories import CheckinRow


class _FakeSettings:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, key, default=None):  # noqa: ANN001
        return self.kv.get(key, default)

    async def set(self, key, value):  # noqa: ANN001
        self.kv[key] = value


def _repos() -> SimpleNamespace:
    return SimpleNamespace(settings=_FakeSettings())


# --- CRUD ---

async def test_add_list_remove():
    repos = _repos()
    assert await hb.list_habits(repos) == []
    a = await hb.add_habit(repos, "good", "Спорт")
    b = await hb.add_habit(repos, "bad", "Поздний сон")
    lst = await hb.list_habits(repos)
    assert [h.name for h in lst] == ["Спорт", "Поздний сон"]
    assert a.is_good and not b.is_good

    # дубль по имени+типу не плодит запись
    a2 = await hb.add_habit(repos, "good", "спорт")
    assert a2.id == a.id
    assert len(await hb.list_habits(repos)) == 2

    # удаление по номеру (1-based)
    removed = await hb.remove_habit(repos, "2")
    assert removed is not None and removed.name == "Поздний сон"
    assert [h.name for h in await hb.list_habits(repos)] == ["Спорт"]

    # удаление по имени
    assert await hb.remove_habit(repos, "Спорт") is not None
    assert await hb.list_habits(repos) == []


async def test_remove_missing_returns_none():
    repos = _repos()
    await hb.add_habit(repos, "good", "Вода")
    assert await hb.remove_habit(repos, "нет такой") is None
    assert await hb.remove_habit(repos, "99") is None


# --- рендер секции в дневник ---

def test_habits_section_render():
    a = {"habits_done": ["Спорт", "Чтение"], "habits_slip": ["Залип в телефон"]}
    out = nb.render_section("habits", "22:10", a)
    assert "## 🌱 Привычки · 22:10" in out
    assert "Сделал: Спорт, Чтение." in out
    assert "Сорвался: Залип в телефон." in out


def test_habits_section_empty():
    out = nb.render_section("habits", "22:10", {"habits_done": [], "habits_slip": []})
    assert "ничего не отметил" in out.lower()


# --- аналитика: счётчики по ДНЯМ (дедуп по дате) ---

def _row(date: str, done=None, slip=None) -> CheckinRow:  # noqa: ANN001
    return CheckinRow(
        id=1, date=date, slot="habits", status="done",
        started_at=f"{date}T22:00:00", finished_at=None,
        answers={"habits_done": done or [], "habits_slip": slip or []},
        flags=[], written=True,
    )


def test_period_counts_days_not_occurrences():
    rows = [
        _row("2026-07-01", done=["Спорт"], slip=["Залип"]),
        _row("2026-07-01", done=["Спорт"]),          # тот же день — не удваивает
        _row("2026-07-02", done=["Спорт", "Чтение"]),
        _row("2026-07-03", slip=["Залип"]),
    ]
    ps = st.build_period_stats("2026-07-01", "2026-07-03", rows)
    done = dict(ps.habits_done)
    slip = dict(ps.habits_slip)
    assert done["Спорт"] == 2          # 01 и 02, но 01 один раз
    assert done["Чтение"] == 1
    assert slip["Залип"] == 2          # 01 и 03
    assert ps.habit_days == 3


def test_day_stats_lists_habits_without_counts():
    rows = [_row("2026-07-02", done=["Спорт"], slip=["Залип"])]
    ds = st.build_day_stats("2026-07-02", rows)
    assert ds.habits_done == ["Спорт"]
    assert ds.habits_slip == ["Залип"]
