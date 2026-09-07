"""Рандомизация времени в пределах окна слота (§2 MASTER-PLAN).

Окно вида 10:30–11:00: бот шлёт пинг в случайную минуту внутри окна, чтобы
напоминание не было механически-одинаковым (и не превращалось в «будильник-задачу»).

APScheduler регистрирует cron-job на window_start; сама job при срабатывании
дополнительно ждёт случайную задержку в пределах окна перед отправкой.
"""

from __future__ import annotations

import random


def parse_hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def window_minutes(window_start: str, window_end: str) -> int:
    """Длина окна в минутах (>=0)."""
    sh, sm = parse_hhmm(window_start)
    eh, em = parse_hhmm(window_end)
    start = sh * 60 + sm
    end = eh * 60 + em
    return max(0, end - start)


def random_delay_seconds(window_start: str, window_end: str) -> int:
    """Случайная задержка в секундах в пределах окна [0, length]."""
    minutes = window_minutes(window_start, window_end)
    if minutes <= 0:
        return 0
    return random.randint(0, minutes * 60)
