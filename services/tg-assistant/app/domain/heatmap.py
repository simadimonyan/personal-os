"""Градация heatmap состояний «как на GitHub».

Уровень дня = число отмеченных состояний (чек-инов morning/day/evening/situational)
за дату → 0..4 → оттенок зелёного. Единственный источник правила градации — здесь,
чтобы легко менять (рендер и тесты ссылаются на эти функции).
"""

from __future__ import annotations

# 5 уровней как у GitHub-контрибьюшенов (RGB). Уровень 0 — пустая клетка.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (235, 237, 240),  # 0 — нет отметок
    (155, 233, 168),  # 1
    (64, 196, 99),    # 2
    (48, 161, 78),    # 3
    (33, 110, 57),    # 4+
)

MAX_LEVEL = len(PALETTE) - 1


def level_for_count(count: int) -> int:
    """Число отметок за день → уровень градации 0..4 (4 = «4 и больше»)."""
    if count <= 0:
        return 0
    return min(count, MAX_LEVEL)


def color_for_count(count: int) -> tuple[int, int, int]:
    return PALETTE[level_for_count(count)]
