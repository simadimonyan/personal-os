"""OWNER_ID фильтр на ВСЕ апдейты (ADR-11, §5 правил).

Single-user бот: отвечает только владельцу, чужие апдейты молча игнорируются.
Реализовано как outer middleware на Dispatcher — блокирует до роутинга.

Расширение (канал-мониторинг): помимо DM от owner пропускаются апдейты из
настроенной forum-группы (channel_id) — обычные сообщения, их правки и события
реакций (MessageReactionUpdated). Всё остальное по-прежнему блокируется.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

log = logging.getLogger("assistant.owner")


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_id: int, channel_id: int = 0) -> None:
        self._owner_id = owner_id
        self._channel_id = channel_id  # 0 = канал не настроен

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            # Пропустить если это DM от owner (старое поведение)
            if self._is_owner_dm(event):
                return await handler(event, data)
            # Пропустить если это апдейт из настроенного channel_id
            if self._channel_id and self._is_from_channel(event):
                return await handler(event, data)
            # Остальное блокируем
            user_id = self._extract_user_id(event)
            if user_id is not None:
                log.warning("ignored update from non-owner user_id=%s", user_id)
            return None
        # не-Update объекты (на всякий случай) — старая логика по user_id
        user_id = self._extract_user_id(event)
        if user_id is not None and user_id != self._owner_id:
            log.warning("ignored event from non-owner user_id=%s", user_id)
            return None
        return await handler(event, data)

    def _is_owner_dm(self, event: Update) -> bool:
        # DM = chat_type == "private" И from_user.id == owner_id
        msg = event.message or event.edited_message or event.callback_query
        if msg is None:
            return False
        chat = getattr(msg, "chat", None) or getattr(
            getattr(msg, "message", None), "chat", None
        )
        if chat is not None and chat.type == "private":
            user_id = self._extract_user_id(event)
            return user_id == self._owner_id
        return False

    def _is_from_channel(self, event: Update) -> bool:
        # Проверяем что апдейт из нашего channel_id
        if event.message and event.message.chat.id == self._channel_id:
            return True
        if event.edited_message and event.edited_message.chat.id == self._channel_id:
            return True
        if event.message_reaction and event.message_reaction.chat.id == self._channel_id:
            return True
        return False

    @staticmethod
    def _extract_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                return event.message.from_user.id
            if event.callback_query and event.callback_query.from_user:
                return event.callback_query.from_user.id
            if event.edited_message and event.edited_message.from_user:
                return event.edited_message.from_user.id
            if event.message_reaction and event.message_reaction.user:
                return event.message_reaction.user.id
            return None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            return event.from_user.id
        return None
