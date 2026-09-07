"""Сборка Bot, Dispatcher, регистрация роутеров, middleware, зависимостей.

Зависимости (repos, ctx, settings, transcriber, scheduler) прокидываются в
хендлеры через dispatcher workflow_data — aiogram внедряет их по имени аргумента.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import Settings
from app.handlers import (
    agency,
    cancel,
    channel_parser,
    checkin_day,
    checkin_evening,
    checkin_morning,
    commands,
    deed,
    edit_last,
    habits,
    hh,
    notes,
    review,
    situational,
    stats,
    tasks,
    today,
    voice,
)
from app.handlers._album import AlbumMiddleware
from app.handlers._owner import OwnerOnlyMiddleware
from app.handlers._service import AppContext
from app.transcription.transcriber import Transcriber

log = logging.getLogger("assistant.bot")


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),  # без HTML-парсинга, тексты чистые
    )


def build_dispatcher(
    settings: Settings,
    ctx: AppContext,
    transcriber: Transcriber,
    task_sync=None,  # noqa: ANN001 — TaskSync, опционален (если таск-менеджер сконфигурирован)
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # OWNER_ID фильтр на ВСЕ апдейты (outer middleware).
    # Если канал-мониторинг включён — middleware также пропускает апдейты из channel_id.
    channel_id = settings.channel.channel_id if settings.channel.enabled else 0
    owner_mw = OwnerOnlyMiddleware(settings.owner_id, channel_id=channel_id)
    dp.update.outer_middleware(owner_mw)

    # Сборка альбомов (несколько фото/файлов одним сообщением) в один апдейт —
    # иначе бот плодит заметки, теряет подпись и перезатирает картинки. См. _album.py.
    dp.message.outer_middleware(AlbumMiddleware())

    # зависимости для всех хендлеров
    dp.workflow_data.update(
        settings=settings,
        repos=ctx.repos,
        ctx=ctx,
        transcriber=transcriber,
        task_sync=task_sync,
    )

    # порядок роутеров: команды/меню первыми, затем сценарии.
    # voice/notes/edit ловят произвольный текст — ставим после FSM-роутеров,
    # т.к. те фильтруются по конкретным состояниям и не перехватят чужое.
    # commands + today первыми: их reply-кнопки (включая 📊 Сегодня) должны
    # срабатывать в любой момент, даже когда идёт чек-ин (иначе state-text-хендлер
    # «Своё» проглотит нажатие кнопки как свободный текст).
    # cancel — первым: ловит «❌ Отмена» (callback) и /cancel в любом состоянии
    dp.include_router(cancel.router)
    dp.include_router(commands.router)
    dp.include_router(today.router)
    dp.include_router(stats.router)
    dp.include_router(tasks.router)
    dp.include_router(hh.router)
    dp.include_router(checkin_morning.router)
    dp.include_router(checkin_day.router)
    dp.include_router(checkin_evening.router)
    dp.include_router(situational.router)
    dp.include_router(agency.router)
    dp.include_router(deed.router)
    dp.include_router(habits.router)
    dp.include_router(review.router)
    dp.include_router(edit_last.router)
    dp.include_router(notes.router)
    dp.include_router(voice.router)

    # канал-мониторинг: роутер строится через build_router(settings),
    # т.к. фильтры (chat.id == channel_id) зависят от настроек.
    if settings.channel.enabled:
        dp.include_router(channel_parser.build_router(settings))
        log.info("channel monitoring enabled: channel_id=%s", settings.channel.channel_id)

    log.info("dispatcher built with %d routers", len(dp.sub_routers))
    return dp


def inject_scheduler(dp: Dispatcher, scheduler) -> None:  # noqa: ANN001
    """Добавляет scheduler в workflow_data (нужно для /reload_schedule).

    Вызывается из run.py после создания scheduler (циклическая зависимость:
    scheduler нуждается в bot, команды нуждаются в scheduler).
    """
    dp.workflow_data.update(scheduler=scheduler)
