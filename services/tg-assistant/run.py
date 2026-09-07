#!/usr/bin/env python3
"""ENTRYPOINT: запуск бота + APScheduler + outbox-worker в одном event loop (ADR-1).

launchd держит этот процесс живым (KeepAlive). Внутри:
- aiogram long-polling (получение апдейтов от Telegram);
- APScheduler (плановые пинги morning/day/evening);
- OutboxWorker (надёжная доставка записей в Obsidian с ретраями).

Грейсфул-шатдаун: при SIGTERM/SIGINT останавливаем polling, worker, scheduler,
закрываем БД и бот-сессию.
"""

from __future__ import annotations

import asyncio

from app.bot import build_bot, build_dispatcher, inject_scheduler
from app.config import get_settings
from app.domain.rotation import Rotator
from app.domain.task_sync import TaskSync
from app.handlers._service import AppContext
from app.integrations.todoist import TodoistClient
from app.logging_setup import setup_logging
from app.obsidian.outbox import OutboxWorker
from app.obsidian.paths import VaultPaths
from app.obsidian.tasks_doc import TasksArchiveDoc, TasksDoc
from app.obsidian.writer import ObsidianWriter
from app.scheduler.jobs import JobRunner
from app.scheduler.scheduler import CheckinScheduler
from app.storage.db import Database
from app.storage.repositories import Repositories
from app.transcription.transcriber import NullTranscriber


async def main() -> None:
    settings = get_settings()
    log = setup_logging(settings)
    log.info("starting assistant (owner=%s, tz=%s)", settings.owner_id, settings.timezone)

    # --- storage ---
    db = Database(settings.db_file)
    await db.connect()
    repos = Repositories.build(db)

    # --- obsidian ---
    paths = VaultPaths(settings)
    await asyncio.to_thread(paths.ensure_dirs)
    writer = ObsidianWriter(paths)

    # --- domain ctx ---
    rotator = Rotator(repos.rotation)
    ctx = AppContext(repos=repos, rotator=rotator)

    # --- task manager (бот↔Obsidian↔Todoist) ---
    http_session = None
    todoist_client = None
    if settings.todoist.enabled and settings.todoist.token:
        import aiohttp

        http_session = aiohttp.ClientSession()
        todoist_client = TodoistClient(
            http_session, settings.todoist.token, settings.todoist.project_id
        )
        log.info("todoist integration enabled (project=%s)",
                 settings.todoist.project_id or "Inbox")
    tasks_doc = TasksDoc(paths.tasks_file)
    tasks_archive = TasksArchiveDoc(paths.tasks_file.with_name("Архив задач.md"))
    task_sync = TaskSync(repos.tasks, tasks_doc, todoist_client, archive=tasks_archive)

    # --- bot / dispatcher ---
    transcriber = NullTranscriber()
    bot = build_bot(settings)
    dp = build_dispatcher(settings, ctx, transcriber, task_sync=task_sync)

    # --- scheduler ---
    runner = JobRunner(bot=bot, settings=settings, repos=repos, ctx=ctx,
                       dispatcher=dp, task_sync=task_sync)
    scheduler = CheckinScheduler(settings, repos, runner)
    await scheduler.setup()
    inject_scheduler(dp, scheduler)

    # --- outbox worker ---
    worker = OutboxWorker(settings, repos.outbox, repos.checkins, writer)

    # --- запуск ---
    scheduler.start()
    worker.start()

    try:
        log.info("entering long-polling")
        await dp.start_polling(
            bot,
            handle_signals=True,
            allowed_updates=[
                "message",
                "edited_message",
                "callback_query",
                "message_reaction",
                "message_reaction_count",
            ],
        )
    finally:
        log.info("shutting down")
        scheduler.shutdown()
        await worker.stop()
        await bot.session.close()
        if http_session is not None:
            await http_session.close()
        await db.close()
        log.info("stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
