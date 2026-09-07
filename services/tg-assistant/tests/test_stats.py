"""Агрегаты статистики состояний (день + период)."""

from __future__ import annotations

from app.domain.stats import build_day_stats, build_period_stats
from app.storage.repositories import CheckinRow


def _row(date: str, slot: str, answers: dict, cid: int = 1) -> CheckinRow:
    return CheckinRow(
        id=cid, date=date, slot=slot, status="done",
        started_at=f"{date}T10:00:00", finished_at=f"{date}T10:01:00",
        answers=answers, flags=[], written=True,
    )


def test_day_stats_basic():
    rows = [
        _row("2026-06-14", "morning", {"valence": 2, "arousal": 5, "emotions": ["calm"]}),
        _row("2026-06-14", "evening", {"valence": -1, "anxiety": 7, "emotions": ["anxiety", "calm"]}),
        _row("2026-06-14", "situational", {"trigger_type": "сравнение_с_другими", "intensity": 8}),
        _row("2026-06-14", "note", {"text": "мысль"}),
    ]
    ds = build_day_stats("2026-06-14", rows)
    assert ds.slots_done == ["morning", "evening"]
    assert ds.situational_count == 1
    assert ds.note_count == 1
    assert ds.valence_avg == 0.5          # (2 + -1) / 2
    assert ds.arousal_avg == 5.0
    emo = dict(ds.emotions)
    assert emo["calm"] == 2 and emo["anxiety"] == 1
    assert dict(ds.triggers)["сравнение_с_другими"] == 1


def test_day_stats_empty():
    ds = build_day_stats("2026-06-14", [])
    assert not ds.has_any


def test_day_stats_ignores_empty_triggers():
    rows = [_row("2026-06-14", "day", {"trigger_type": "не_было"})]
    ds = build_day_stats("2026-06-14", rows)
    assert ds.triggers == []


def test_period_stats_counts_days_and_marks():
    rows = [
        _row("2026-06-01", "morning", {"valence": 3}),
        _row("2026-06-01", "evening", {"valence": 1}),
        _row("2026-06-03", "morning", {"valence": -2}),
        _row("2026-06-05", "situational", {"emotions": ["anger"]}),
        _row("2026-06-05", "note", {"text": "x"}),  # заметка не входит в total_marks
    ]
    ps = build_period_stats("2026-06-01", "2026-06-30", rows)
    assert ps.total_marks == 4              # 3 плановых + 1 ситуативный, без note
    assert ps.days_with_marks == 3          # 01, 03, 05
    assert ps.total_days == 30
    assert ps.situational_count == 1
    assert ps.note_count == 1
    assert ps.valence_avg == round((3 + 1 - 2) / 3, 1)
