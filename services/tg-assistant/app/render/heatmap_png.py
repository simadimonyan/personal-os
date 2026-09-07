"""Рендер PNG-карты состояний «как на GitHub» (зелёный градиент по числу отметок).

Сетка месяца: столбцы — недели (Пн-based), 7 строк — дни недели. Цвет клетки —
из палитры app.domain.heatmap по числу отмеченных состояний за день.

Возвращает PNG в виде bytes (in-memory), чтобы отдать через bot.send_photo.
Pillow — единственная тяжёлая зависимость; весь рендер синхронный, вызывать через
asyncio.to_thread.
"""

from __future__ import annotations

import calendar
import io
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont

from app.domain.heatmap import PALETTE, color_for_count

# Геометрия
_CELL = 26
_GAP = 5
_PAD = 22
_TOP = 56          # место под заголовок
_LEFT = 38         # место под подписи дней недели
_BOTTOM = 46       # место под легенду
_BG = (255, 255, 255)
_FG = (60, 60, 60)
_MUTED = (140, 140, 140)

_WEEKDAYS_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_MONTHS_RU = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

# Кандидаты шрифтов с поддержкой кириллицы (macOS / Linux), фолбэк — bitmap.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_month_heatmap(year: int, month: int, counts: dict[str, int]) -> bytes:
    """counts — {YYYY-MM-DD: число отметок} (см. CheckinRepo.counts_by_date)."""
    first = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    last = date(year, month, days_in_month)

    # начало сетки — понедельник недели, в которой лежит 1-е число
    grid_start = first - timedelta(days=first.weekday())
    total_cols = ((last - grid_start).days) // 7 + 1

    width = _LEFT + total_cols * (_CELL + _GAP) + _PAD
    height = _TOP + 7 * (_CELL + _GAP) + _BOTTOM

    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(26)
    small_font = _font(15)
    cell_font = _font(12)

    # заголовок
    draw.text((_PAD, 16), f"{_MONTHS_RU[month - 1]} {year}", fill=_FG, font=title_font)

    # подписи дней недели слева (Пн..Вс)
    for row, label in enumerate(_WEEKDAYS_RU):
        y = _TOP + row * (_CELL + _GAP) + _CELL // 2
        draw.text((_PAD - 6, y - 8), label, fill=_MUTED, font=small_font)

    # клетки месяца
    today = date.today()
    d = first
    while d <= last:
        delta = (d - grid_start).days
        col, row = delta // 7, delta % 7
        x = _LEFT + col * (_CELL + _GAP)
        y = _TOP + row * (_CELL + _GAP)
        cnt = counts.get(d.isoformat(), 0)
        fill = color_for_count(cnt)
        outline = (180, 180, 180) if d == today else None
        _rounded(draw, x, y, _CELL, fill, outline)
        # номер дня бледно поверх клетки
        num_color = (90, 90, 90) if cnt == 0 else (255, 255, 255)
        draw.text((x + 5, y + 5), str(d.day), fill=num_color, font=cell_font)
        d += timedelta(days=1)

    # легенда «Меньше ▢▢▢ Больше»
    ly = _TOP + 7 * (_CELL + _GAP) + 12
    draw.text((_LEFT, ly), "Меньше", fill=_MUTED, font=small_font)
    lx = _LEFT + 64
    for level_color in PALETTE:
        _rounded(draw, lx, ly - 2, 18, level_color, None)
        lx += 22
    draw.text((lx + 4, ly), "Больше", fill=_MUTED, font=small_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rounded(draw: ImageDraw.ImageDraw, x: int, y: int, size: int,
             fill: tuple[int, int, int], outline: tuple[int, int, int] | None) -> None:
    draw.rounded_rectangle(
        [x, y, x + size, y + size], radius=5, fill=fill, outline=outline, width=2
    )
