"""Тесты флагов риска (§6 MASTER-PLAN)."""

from __future__ import annotations

from app.domain.flags import (
    FLAG_APATHY,
    FLAG_ISOLATION,
    FLAG_JEALOUSY,
    FLAG_PERFORM,
    FLAG_SELF_ATTACK,
    FlagInput,
    compute_flags,
    detect_self_attack,
    response_for_flags,
)


def test_self_attack_detected():
    assert detect_self_attack("я такой жалкий и тупой")
    assert detect_self_attack("ненавижу себя")
    assert not detect_self_attack("сегодня было спокойно")


def test_flag_self_attack_from_free_text():
    current = FlagInput("2026-06-09", "situational", {}, free_text="опять тупой, ничего не могу")
    flags = compute_flags(current, [])
    assert FLAG_SELF_ATTACK in flags


def test_flag_jealousy_two_per_day():
    current = FlagInput("2026-06-09", "situational", {"trigger_type": "ревность_сигнал"})
    history = [
        FlagInput("2026-06-09", "day", {"trigger_type": "ревность_сигнал"}),
        FlagInput("2026-06-08", "day", {"trigger_type": "ревность_сигнал"}),  # другой день — не считается
    ]
    flags = compute_flags(current, history)
    assert FLAG_JEALOUSY in flags


def test_flag_jealousy_single_not_triggered():
    current = FlagInput("2026-06-09", "situational", {"trigger_type": "ревность_сигнал"})
    flags = compute_flags(current, [])
    assert FLAG_JEALOUSY not in flags


def test_flag_apathy_streak():
    current = FlagInput("2026-06-09", "morning", {"emotions": ["apathy"], "arousal": 2})
    history = [
        FlagInput("2026-06-08", "morning", {"emotions": ["apathy"], "arousal": 3}),
        FlagInput("2026-06-07", "morning", {"emotions": ["apathy"], "arousal": 1}),
        FlagInput("2026-06-06", "morning", {"emotions": ["apathy"], "arousal": 2}),
    ]
    flags = compute_flags(current, history)
    assert FLAG_APATHY in flags


def test_flag_apathy_breaks_on_high_arousal():
    current = FlagInput("2026-06-09", "morning", {"emotions": ["apathy"], "arousal": 2})
    history = [
        FlagInput("2026-06-08", "morning", {"emotions": ["apathy"], "arousal": 8}),  # обрыв
        FlagInput("2026-06-07", "morning", {"emotions": ["apathy"], "arousal": 1}),
        FlagInput("2026-06-06", "morning", {"emotions": ["apathy"], "arousal": 2}),
    ]
    flags = compute_flags(current, history)
    assert FLAG_APATHY not in flags


def test_flag_isolation_three_days():
    current = FlagInput("2026-06-09", "evening", {"human_contact": "нет"})
    history = [
        FlagInput("2026-06-08", "evening", {"human_contact": "нет"}),
        FlagInput("2026-06-07", "evening", {"human_contact": "нет"}),
    ]
    flags = compute_flags(current, history)
    assert FLAG_ISOLATION in flags


def test_flag_isolation_breaks_on_contact():
    current = FlagInput("2026-06-09", "evening", {"human_contact": "нет"})
    history = [
        FlagInput("2026-06-08", "evening", {"human_contact": "да_глубокий"}),  # обрыв
        FlagInput("2026-06-07", "evening", {"human_contact": "нет"}),
    ]
    flags = compute_flags(current, history)
    assert FLAG_ISOLATION not in flags


def test_flag_perform_low_felt():
    current = FlagInput("2026-06-09", "evening", {"felt_vs_analyzed": 2})
    flags = compute_flags(current, [])
    assert FLAG_PERFORM in flags


def test_flag_perform_not_triggered_high_felt():
    current = FlagInput("2026-06-09", "evening", {"felt_vs_analyzed": 8})
    flags = compute_flags(current, [])
    assert FLAG_PERFORM not in flags


def test_response_priority_self_attack_first():
    text = response_for_flags([FLAG_PERFORM, FLAG_SELF_ATTACK, FLAG_ISOLATION])
    assert "бьёшь себя" in text  # самоатака приоритетнее


def test_response_none_when_no_flags():
    assert response_for_flags([]) is None
