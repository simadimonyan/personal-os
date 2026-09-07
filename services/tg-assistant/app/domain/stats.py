"""Чистые агрегаты статистики состояний над списком CheckinRow.

Без I/O и без Telegram — легко тестировать. Хендлер stats.py форматирует результат
в текст (русские подписи), а считается всё здесь.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta

from app.storage.repositories import CheckinRow

_PLAN_SLOTS = ("morning", "day", "evening")
# Триггеры-«пустышки» не считаем за триггер.
_EMPTY_TRIGGERS = {"не_было", "неизвестно", ""}


@dataclass
class DayStats:
    date: str
    slots_done: list[str] = field(default_factory=list)   # из morning/day/evening
    situational_count: int = 0
    note_count: int = 0
    valence_avg: float | None = None
    arousal_avg: float | None = None
    anxiety_avg: float | None = None
    self_criticism_avg: float | None = None
    emotions: list[tuple[str, int]] = field(default_factory=list)
    triggers: list[tuple[str, int]] = field(default_factory=list)
    habits_done: list[str] = field(default_factory=list)   # хорошие, что сделал в этот день
    habits_slip: list[str] = field(default_factory=list)   # вредные, что было (сорвался)

    @property
    def has_any(self) -> bool:
        return bool(self.slots_done) or self.situational_count or self.note_count


@dataclass
class PeriodStats:
    start: str
    end: str
    total_marks: int = 0          # число чек-инов состояний (morning/day/evening/situational)
    days_with_marks: int = 0
    total_days: int = 0
    valence_avg: float | None = None
    arousal_avg: float | None = None
    anxiety_avg: float | None = None
    self_criticism_avg: float | None = None
    emotions: list[tuple[str, int]] = field(default_factory=list)
    triggers: list[tuple[str, int]] = field(default_factory=list)
    situational_count: int = 0
    note_count: int = 0
    # привычки: (название, число ДНЕЙ с отметкой) — счётчики только в аналитике
    habits_done: list[tuple[str, int]] = field(default_factory=list)   # хорошие: сделал
    habits_slip: list[tuple[str, int]] = field(default_factory=list)   # вредные: было
    habit_days: int = 0            # сколько дней вообще отмечал привычки


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _collect_metric(rows: list[CheckinRow], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.answers.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def _count_emotions(rows: list[CheckinRow]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        vals = r.answers.get("emotions")
        if isinstance(vals, (list, tuple)):
            c.update(v for v in vals if v)
        elif isinstance(vals, str) and vals:
            c.update([vals])
    return c


def _count_triggers(rows: list[CheckinRow]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        t = r.answers.get("trigger_type")
        if isinstance(t, str) and t not in _EMPTY_TRIGGERS:
            c.update([t])
    return c


def _habit_day_counts(rows: list[CheckinRow], key: str) -> Counter:
    """Считает число ДНЕЙ с отметкой по каждой привычке (дедуп по дате).

    Если за один день несколько habits-чек-инов — день считается один раз.
    """
    seen: dict[str, set[str]] = {}
    for r in rows:
        if r.slot != "habits":
            continue
        vals = r.answers.get(key)
        if isinstance(vals, (list, tuple)):
            for name in vals:
                if name:
                    seen.setdefault(name, set()).add(r.date)
    return Counter({name: len(dates) for name, dates in seen.items()})


def build_day_stats(date: str, rows: list[CheckinRow]) -> DayStats:
    """rows — все завершённые чек-ины за дату."""
    state_rows = [r for r in rows if r.slot in _PLAN_SLOTS or r.slot == "situational"]
    ds = DayStats(date=date)
    ds.slots_done = [s for s in _PLAN_SLOTS if any(r.slot == s for r in rows)]
    ds.situational_count = sum(1 for r in rows if r.slot == "situational")
    ds.note_count = sum(1 for r in rows if r.slot == "note")
    ds.valence_avg = _avg(_collect_metric(state_rows, "valence"))
    ds.arousal_avg = _avg(_collect_metric(state_rows, "arousal"))
    ds.anxiety_avg = _avg(_collect_metric(state_rows, "anxiety"))
    ds.self_criticism_avg = _avg(_collect_metric(state_rows, "self_criticism"))
    ds.emotions = _count_emotions(state_rows).most_common()
    ds.triggers = _count_triggers(state_rows).most_common()
    # привычки за день — просто список отмеченного, без счётчиков
    for r in rows:
        if r.slot == "habits":
            ds.habits_done += [x for x in (r.answers.get("habits_done") or []) if x]
            ds.habits_slip += [x for x in (r.answers.get("habits_slip") or []) if x]
    return ds


def build_period_stats(start: str, end: str, rows: list[CheckinRow]) -> PeriodStats:
    """rows — все завершённые чек-ины за период [start, end]."""
    state_rows = [r for r in rows if r.slot in _PLAN_SLOTS or r.slot == "situational"]
    ps = PeriodStats(start=start, end=end)
    ps.total_marks = len(state_rows)
    ps.days_with_marks = len({r.date for r in state_rows})
    ps.total_days = _days_between(start, end)
    ps.valence_avg = _avg(_collect_metric(state_rows, "valence"))
    ps.arousal_avg = _avg(_collect_metric(state_rows, "arousal"))
    ps.anxiety_avg = _avg(_collect_metric(state_rows, "anxiety"))
    ps.self_criticism_avg = _avg(_collect_metric(state_rows, "self_criticism"))
    ps.emotions = _count_emotions(state_rows).most_common(5)
    ps.triggers = _count_triggers(state_rows).most_common(5)
    ps.situational_count = sum(1 for r in rows if r.slot == "situational")
    ps.note_count = sum(1 for r in rows if r.slot == "note")
    ps.habits_done = _habit_day_counts(rows, "habits_done").most_common()
    ps.habits_slip = _habit_day_counts(rows, "habits_slip").most_common()
    ps.habit_days = len({r.date for r in rows if r.slot == "habits"})
    return ps


def _days_between(start: str, end: str) -> int:
    try:
        a = date_cls.fromisoformat(start)
        b = date_cls.fromisoformat(end)
    except ValueError:
        return 0
    return (b - a).days + 1 if b >= a else 0


def daterange(start: date_cls, end: date_cls):
    """Итератор дат [start, end] включительно."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
