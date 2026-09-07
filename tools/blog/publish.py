#!/usr/bin/env python3
"""
Публикация статьи блога в разные соцсети — единый интерфейс, разные адаптеры.

Костяк: каждая площадка = класс с методом ``publish(article) -> (remote_url, status)``.
Telegram может опереться на существующий tg-blog-editor/канал @digit_code (пока
через draft-режим — реальную отправку включаем осознанно). Остальные площадки —
заглушки, возвращают статус 'draft', чтобы контур работал end-to-end до подключения
реальных API (VK, Дзен, Telegraph, X, LinkedIn).

Добавление реальной площадки = один класс + запись в PUBLISHERS.
"""
from __future__ import annotations

import os


class Publisher:
    name = "base"

    def publish(self, article: dict) -> tuple[str | None, str]:
        """Вернуть (remote_url, status). status ∈ {published, draft, failed}."""
        raise NotImplementedError


class StubPublisher(Publisher):
    """Заглушка: контур работает, но реально никуда не шлёт — статус draft.
    Заменяется реальным адаптером при подключении площадки."""

    def __init__(self, name: str):
        self.name = name

    def publish(self, article: dict) -> tuple[str | None, str]:
        return None, "draft"


class TelegramPublisher(Publisher):
    """Публикация в Telegram-канал (по умолчанию @digit_code).

    Костяк: пока НЕ шлёт автоматически — возвращает 'draft', чтобы случайно не
    выложить в канал. Реальная отправка включается снятием AVATAR-подобного
    предохранителя (env BLOG_TG_LIVE=1) и заводится через существующую
    инфраструктуру бота/канала (tg-blog-editor уже умеет постить с ✅)."""

    name = "telegram"

    def __init__(self, channel: str | None = None):
        self.channel = channel or os.environ.get("BLOG_TG_CHANNEL", "@digit_code")

    def publish(self, article: dict) -> tuple[str | None, str]:
        if os.environ.get("BLOG_TG_LIVE") != "1":
            # предохранитель: без явного разрешения — только черновик
            return None, "draft"
        # TODO: реальная отправка через ~/.claude/skills/telegram/driver.cjs
        #       send_message в self.channel, либо передать в очередь tg-blog-editor
        #       (human-in-the-loop ✅), чтобы сохранить ручное подтверждение.
        return None, "draft"


PUBLISHERS: dict[str, Publisher] = {
    "telegram": TelegramPublisher(),
    # реальные адаптеры добавляются здесь по мере подключения площадок:
    "vk": StubPublisher("vk"),
    "dzen": StubPublisher("dzen"),
    "telegraph": StubPublisher("telegraph"),
    "x": StubPublisher("x"),
    "linkedin": StubPublisher("linkedin"),
}


def get_publisher(network: str) -> Publisher:
    return PUBLISHERS.get(network, StubPublisher(network))
