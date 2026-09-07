"""Вычисление флагов риска (§1.3, §6 MASTER-PLAN) на основе истории SQLite.

Флаги НЕ показываются пользователю и НЕ диагностируют (§6, ADR-8).
Они влияют только на тон/направление следующего промпта.

Пороги — стартовые (черновые, §7 плана), калибруются психологом после 2-4 недель.
Здесь они вынесены в константы вверху файла для лёгкой настройки.

Чистые функции над списком завершённых чек-инов (CheckinRow), без I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---- пороги (стартовые, §6) ----
APATHY_MIN_DAYS = 4          # apathy подряд
APATHY_AROUSAL_MAX = 3
JEALOUSY_PER_DAY = 2         # ревность ≥2 раз за день
ISOLATION_MIN_DAYS = 3       # human_contact = нет подряд
PERFORM_FELT_MAX = 3         # felt_vs_analyzed ≤3

# маркеры самоатаки (§6 flag_self_attack) — бинарный детект, НЕ скоринг текста
SELF_ATTACK_MARKERS = (
    "ненавижу себя",
    "противно",
    "тупой",
    "тупая",
    "жалкий",
    "жалкая",
    "ничтожество",
    "никчём",
    "отвратителен",
    "отвратительн",
)

# имена флагов
FLAG_APATHY = "flag_apathy"
FLAG_JEALOUSY = "flag_jealousy"
FLAG_SELF_ATTACK = "flag_self_attack"
FLAG_ISOLATION = "flag_isolation"
FLAG_PERFORM = "flag_perform"


@dataclass
class FlagInput:
    """Нормализованный вход одного чек-ина для флаговой логики."""

    date: str
    slot: str
    answers: dict[str, Any]
    free_text: str | None = None


def _has(values: Any, target: str) -> bool:
    if values is None:
        return False
    if isinstance(values, (list, tuple)):
        return target in values
    return values == target


def detect_self_attack(text: str | None) -> bool:
    """Бинарный детект маркеров самонаправленной агрессии в свободном тексте."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in SELF_ATTACK_MARKERS)


def compute_flags(current: FlagInput, history: list[FlagInput]) -> list[str]:
    """Вычисляет флаги по текущему чек-ину + истории (новые первыми).

    history — завершённые чек-ины ДО текущего, отсортированы новые→старые.
    """
    flags: list[str] = []

    # --- flag_self_attack: по текущему тексту (или body_word) ---
    text_fields = [current.free_text, current.answers.get("body_word"), current.answers.get("trigger_note")]
    if any(detect_self_attack(t) for t in text_fields):
        flags.append(FLAG_SELF_ATTACK)

    # --- flag_jealousy: ревность ≥2 раз за сегодня (включая текущий) ---
    today = current.date
    jealousy_count = 0
    if _has_jealousy(current):
        jealousy_count += 1
    for h in history:
        if h.date != today:
            break  # история отсортирована, дальше другие дни
        if _has_jealousy(h):
            jealousy_count += 1
    if jealousy_count >= JEALOUSY_PER_DAY:
        flags.append(FLAG_JEALOUSY)

    # --- flag_apathy: apathy ≥ N дней подряд И arousal ≤ 3 ---
    if _apathy_streak(current, history) >= APATHY_MIN_DAYS:
        flags.append(FLAG_APATHY)

    # --- flag_isolation: human_contact=нет ≥3 дней подряд ---
    if _isolation_streak(current, history) >= ISOLATION_MIN_DAYS:
        flags.append(FLAG_ISOLATION)

    # --- flag_perform: felt_vs_analyzed ≤3 (рост длины текста наблюдается отдельно) ---
    fva = current.answers.get("felt_vs_analyzed")
    if fva is not None and fva <= PERFORM_FELT_MAX:
        flags.append(FLAG_PERFORM)

    return flags


def _has_jealousy(c: FlagInput) -> bool:
    return _has(c.answers.get("trigger_type"), "ревность_сигнал") or _has(
        c.answers.get("emotions"), "envy"
    )


def _day_marks_apathy(c: FlagInput) -> bool | None:
    """True/False если по чек-ину можно судить об апатии этого дня, None если данных нет."""
    em = c.answers.get("emotions")
    ar = c.answers.get("arousal")
    if em is None and ar is None:
        return None
    apathy = _has(em, "apathy")
    low_arousal = ar is not None and ar <= APATHY_AROUSAL_MAX
    return apathy and low_arousal


def _apathy_streak(current: FlagInput, history: list[FlagInput]) -> int:
    """Сколько последних дней подряд (включая сегодня) маркируют апатию."""
    by_day = _group_by_day([current] + history)
    streak = 0
    for day in sorted(by_day.keys(), reverse=True):
        day_flag = any(_day_marks_apathy(c) for c in by_day[day])
        if day_flag:
            streak += 1
        else:
            # если в дне есть данные но не апатия — обрыв; если данных нет — тоже обрыв
            break
    return streak


def _isolation_streak(current: FlagInput, history: list[FlagInput]) -> int:
    """Сколько последних дней подряд human_contact = нет (по вечерним чек-инам)."""
    by_day = _group_by_day([current] + history)
    streak = 0
    for day in sorted(by_day.keys(), reverse=True):
        contacts = [
            c.answers.get("human_contact")
            for c in by_day[day]
            if c.answers.get("human_contact") is not None
        ]
        if not contacts:
            break  # нет данных за день — обрыв (консервативно)
        if all(c == "нет" for c in contacts):
            streak += 1
        else:
            break
    return streak


def _group_by_day(items: list[FlagInput]) -> dict[str, list[FlagInput]]:
    out: dict[str, list[FlagInput]] = {}
    for it in items:
        out.setdefault(it.date, []).append(it)
    return out


# --- реакции бота на флаги (§6): только корректировка тона следующего промпта ---
FLAG_RESPONSES: dict[str, str] = {
    FLAG_APATHY: (
        "Сегодня только одно: встань, глоток воды, посмотри в окно."
    ),
    FLAG_JEALOUSY: (
        "Где это в теле? Подыши 4-7-8. Это сигнал, не факт."
    ),
    FLAG_SELF_ATTACK: (
        "Заметил, что бьёшь себя. Стоп. Где злость на самом деле — точно ли она про тебя?"
    ),
    FLAG_ISOLATION: (
        "Несколько дней без живого контакта. Может, написать человеку, а не мне?"
    ),
    FLAG_PERFORM: (
        "Сегодня без анализа. Только: тело — одно слово. Всё."
    ),
}


def response_for_flags(flags: list[str]) -> str | None:
    """Возвращает текст мягкой реакции для первого сработавшего приоритетного флага.

    Приоритет: самоатака > апатия > ревность > изоляция > перформанс.
    """
    priority = [FLAG_SELF_ATTACK, FLAG_APATHY, FLAG_JEALOUSY, FLAG_ISOLATION, FLAG_PERFORM]
    for f in priority:
        if f in flags:
            return FLAG_RESPONSES[f]
    return None
