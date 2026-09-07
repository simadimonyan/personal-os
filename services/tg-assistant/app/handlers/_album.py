"""Сборка Telegram-альбомов (media group) в один апдейт.

Когда пользователь отправляет несколько фото/файлов одним сообщением, Telegram
доставляет их как N отдельных Message с общим media_group_id, и подпись (caption)
лежит лишь на одном из них. Без сборки бот плодит N заметок, теряет подпись и из-за
гонки за одинаковым именем файла перезатирает картинки.

Этот outer-middleware на dp.message буферизует сообщения одной группы и ОДИН раз
вызывает хендлер, складывая в data["album"] весь список (отсортированный по
message_id). Остальные сообщения группы проглатываются (хендлер для них не зовётся).
Одиночные сообщения проходят насквозь без задержки.

Окно ожидания «скользящее» (rolling debounce): таймер сбрасывается на каждое новое
фото группы, и буфер сбрасывается только когда альбом «затих» на целый latency.
Так фото большого/длинного альбома, приходящие с задержками в несколько секунд, не
разрезаются на несколько заметок (фикс дублей по числу фото).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    def __init__(self, latency: float = 1.5) -> None:
        self._latency = latency
        self._albums: dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        group_id = event.media_group_id
        if group_id is None:
            return await handler(event, data)

        # setdefault+append без await между ними — атомарно для других корутин.
        bucket = self._albums.setdefault(group_id, [])
        bucket.append(event)
        if len(bucket) > 1:
            # Не первое сообщение группы — просто копим, хендлер не зовём.
            return None

        # Первое сообщение становится «сборщиком»: ждём, пока альбом затихнет.
        last_seen = -1
        while last_seen != len(bucket):
            last_seen = len(bucket)
            await asyncio.sleep(self._latency)
        album = self._albums.pop(group_id, [event])
        album.sort(key=lambda m: m.message_id)
        data["album"] = album
        return await handler(event, data)
