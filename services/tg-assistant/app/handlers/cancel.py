"""Глобальная отмена ввода (требование «кнопка Отмена при входе в режим ввода»).

Любой FSM-ввод (заметка, правка, задача, чек-ины утро/день/вечер, ситуативка)
показывает кнопку «❌ Отмена». Этот роутер ловит её callback в ЛЮБОМ состоянии
(StateFilter("*")), чистит FSM и подтверждает выход — ничего не записывая.

Подключается ПЕРВЫМ в диспетчере, чтобы выигрывать у пошаговых обработчиков:
коллизий нет (фильтр строго по ActionCB name == "cancel"), но порядок надёжнее.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.common import ActionCB

log = logging.getLogger("assistant.cancel")
router = Router(name="cancel")

_CANCELLED_TEXT = "❌ Отменено."
_NOTHING_TEXT = "Нечего отменять."


@router.callback_query(StateFilter("*"), ActionCB.filter(F.name == "cancel"))
async def on_cancel_cb(cb: CallbackQuery, state: FSMContext) -> None:
    cur = await state.get_state()
    await state.clear()
    if cb.message is not None:
        try:
            await cb.message.edit_text(_CANCELLED_TEXT if cur else _NOTHING_TEXT)
        except Exception:  # сообщение без текста/устарело — отвечаем новым
            await cb.message.answer(_CANCELLED_TEXT if cur else _NOTHING_TEXT)
    if cur:
        log.info("fsm cancelled from state=%s", cur)
    await cb.answer("Отменено" if cur else "Нечего отменять")


@router.message(StateFilter("*"), Command("cancel"))
async def on_cancel_cmd(message: Message, state: FSMContext) -> None:
    cur = await state.get_state()
    await state.clear()
    await message.answer(_CANCELLED_TEXT if cur else _NOTHING_TEXT)
