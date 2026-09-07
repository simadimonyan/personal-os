"""Главное меню бота — inline-кнопки под сообщением (§3.4 MASTER-PLAN).

Было reply-меню (постоянная клавиатура снизу), стало inline. Плата за переход:
reply-клавиатура висела всегда, inline живёт при конкретном сообщении и уезжает
вверх с историей чата. Поэтому меню вызывается командой /menu, а «Накрыло»
продублировано командой /sos — чтобы вход в ситуативный чек-ин не зависел от
того, докрутил ли ты чат до нужного сообщения.

Тексты кнопок остались константами BTN_*: их всё ещё матчат текстовые хендлеры
(на случай, если у клиента закэширована старая reply-клавиатура).
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove


class MenuCB(CallbackData, prefix="menu"):
    action: str   # sos | morning | day | evening | agency | deed | note | edit | today | stats | tasks | hh


# Подписи кнопок (они же — триггеры текстовых хендлеров для обратной совместимости)
BTN_OVERWHELMED = "🆘 Накрыло"
BTN_MORNING = "☀️ Утро"
BTN_DAY = "🌤 День"
BTN_EVENING = "🌙 Вечер"
BTN_AGENCY = "🧭 Развилка"
BTN_DEED = "📌 Поступок"
BTN_HABITS = "🌱 Привычки"
BTN_NOTE = "📝 Заметка"
BTN_EDIT_LAST = "✏️ Поправить последнее"
BTN_TODAY = "📊 Сегодня"
BTN_ANALYTICS = "📈 Аналитика"
BTN_TASKS = "✅ Задачи"
BTN_HH = "💼 Работа"

# действие -> подпись; порядок задаёт раскладку в main_menu_kb()
ACTIONS = {
    "sos": BTN_OVERWHELMED,
    "morning": BTN_MORNING,
    "day": BTN_DAY,
    "evening": BTN_EVENING,
    "agency": BTN_AGENCY,
    "deed": BTN_DEED,
    "habits": BTN_HABITS,
    "note": BTN_NOTE,
    "edit": BTN_EDIT_LAST,
    "today": BTN_TODAY,
    "stats": BTN_ANALYTICS,
    "tasks": BTN_TASKS,
    "hh": BTN_HH,
}


def _b(action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=ACTIONS[action], callback_data=MenuCB(action=action).pack())


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b("sos")],
        [_b("morning"), _b("day"), _b("evening")],
        [_b("agency"), _b("deed")],
        [_b("habits")],
        [_b("note"), _b("edit")],
        [_b("today"), _b("stats")],
        [_b("tasks"), _b("hh")],
    ])


def drop_reply_kb() -> ReplyKeyboardRemove:
    """Убрать старую reply-клавиатуру с клиента — она кэшируется до первой отправки."""
    return ReplyKeyboardRemove()
