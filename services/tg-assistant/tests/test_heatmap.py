"""Градация heatmap состояний + дымовой тест PNG-рендера."""

from __future__ import annotations

from app.domain.heatmap import MAX_LEVEL, color_for_count, level_for_count
from app.render.heatmap_png import render_month_heatmap


def test_level_clamps():
    assert level_for_count(0) == 0
    assert level_for_count(1) == 1
    assert level_for_count(4) == MAX_LEVEL
    assert level_for_count(10) == MAX_LEVEL  # «4 и больше»


def test_empty_is_lightest():
    assert color_for_count(0) != color_for_count(1)
    assert color_for_count(3) != color_for_count(4)


def test_render_month_returns_png_bytes():
    counts = {"2026-06-01": 1, "2026-06-14": 3, "2026-06-25": 4}
    png = render_month_heatmap(2026, 6, counts)
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # сигнатура PNG


def test_render_empty_month_ok():
    png = render_month_heatmap(2026, 2, {})  # февраль без отметок
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
