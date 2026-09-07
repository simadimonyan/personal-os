"""AsyncIOScheduler: единый тик-job проверки окон напоминаний (§1.2).

Раньше регистрировались cron-jobs на window_start каждого слота. На ноутбуке,
который спит, cron-минута почти всегда пропускалась (грейс 30 мин) → пинг
терялся на весь день. Теперь — один interval-job `tick` каждые N секунд:
он читает расписание из SQLite вживую и сам решает, пора ли пинговать слот
(см. JobRunner.tick). Это переживает сон/перезапуск и догоняет окно при
ближайшем пробуждении.

Поскольку tick читает расписание из БД на каждом тике, /set_time применяется
сразу — отдельная перерегистрация job больше не нужна (reload_all — no-op).
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.scheduler.jobs import JobRunner
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.scheduler")

_TICK_JOB_ID = "checkin_tick"
_SYNC_JOB_ID = "task_sync"


class CheckinScheduler:
    def __init__(self, settings: Settings, repos: Repositories, runner: JobRunner) -> None:
        self._settings = settings
        self._repos = repos
        self._runner = runner
        self._scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def setup(self) -> None:
        """Регистрирует tick-job напоминаний и (если включён Todoist) sync-job задач."""
        interval = self._settings.scheduler.tick_interval_seconds
        self._scheduler.add_job(
            self._runner.tick,
            trigger=IntervalTrigger(seconds=interval),
            id=_TICK_JOB_ID,
            replace_existing=True,
            next_run_time=datetime.now(),  # проверить текущее окно сразу при старте
            coalesce=self._settings.scheduler.coalesce,
            misfire_grace_time=self._settings.scheduler.misfire_grace_time_seconds,
            max_instances=1,
        )
        log.info("scheduler configured: tick every %ss", interval)

        # Фоновый синк задач (бот↔Obsidian↔Todoist). Регистрируется, если у runner
        # есть task_sync (т.е. таск-менеджер сконфигурирован).
        if getattr(self._runner, "task_sync", None) is not None:
            sync_interval = self._settings.todoist.poll_interval_seconds
            self._scheduler.add_job(
                self._runner.sync_tasks,
                trigger=IntervalTrigger(seconds=sync_interval),
                id=_SYNC_JOB_ID,
                replace_existing=True,
                next_run_time=datetime.now(),  # подтянуть внешние правки сразу при старте
                coalesce=True,
                max_instances=1,
            )
            log.info("scheduler configured: task sync every %ss", sync_interval)

    async def reschedule_job(self, slot: str) -> None:
        """No-op: tick читает расписание из БД вживую, перерегистрация не нужна."""
        log.info("reschedule_job(%s): live tick reads schedule from DB, nothing to do", slot)

    async def reload_all(self) -> None:
        """No-op: см. reschedule_job. Оставлено для совместимости с /reload_schedule."""
        log.info("reload_all: live tick reads schedule from DB, nothing to do")

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler started")

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler shut down")
