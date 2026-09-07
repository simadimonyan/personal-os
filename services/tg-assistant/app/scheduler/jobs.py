"""Job-функции планировщика (§2 MASTER-PLAN).

Модель напоминаний — ТИК (а не cron-в-начале-окна), потому что бот живёт на
ноутбуке, который спит. Cron-минута окна (10:30 / 15:00 / 19:00) почти всегда
приходится на сон → пинг терялся на весь день. Тик каждые N минут проверяет
окна вживую и догоняет пропущенную из-за сна минуту при пробуждении.

На каждом тике для каждого слота (morning/day/evening):
1. слот включён в расписании? иначе пропустить;
2. «лёгкий день» (раз в неделю только утро, §2) → день/вечер пропустить;
3. слот уже отмечен сегодня (в т.ч. вручную)? → не напоминать (§2);
4. внутри окна [start, end+grace] и наступила случайная цель дня? → пинг;
5. пометить «пингнут сегодня», чтобы не дублировать.

Случайная «цель» внутри окна (jitter) хранится в settings (KV), чтобы пинг не
был механически-одинаковым, но переживал перезапуск процесса.

Job НЕ создаёт строку checkins сама — старт сценария делает start_*(),
который шлёт приветствие и заводит сессию.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date as date_cls
from datetime import datetime

from aiogram import Bot

from app.config import Settings
from app.handlers import checkin_day, checkin_evening, checkin_morning, review
from app.handlers._service import AppContext
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.jobs")

# Обзоры авторства (тик-модель, устойчива к сну ноутбука — как и слоты дня).
# Недельный: воскресенье (weekday=6) с 20:00; месячный: 1-е число с 12:00.
# Один раз в период (guard в settings). Без переспросов — рефлексия не должна нылить.
_WEEKLY_WEEKDAY = 6
_WEEKLY_AFTER_MIN = 20 * 60
_MONTHLY_DAY = 1
_MONTHLY_AFTER_MIN = 12 * 60

# слот -> функция старта сценария
_SLOT_STARTERS = {
    "morning": checkin_morning.start_morning,
    "day": checkin_day.start_day,
    "evening": checkin_evening.start_evening,
}
# слоты, которые пропускаются в «лёгкий день» (остаётся только утро)
_LIGHT_DAY_SKIP = {"day", "evening"}


def _hhmm_to_min(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


class JobRunner:
    """Держит зависимости для job-функций (передаётся в APScheduler как closure)."""

    def __init__(self, bot: Bot, settings: Settings, repos: Repositories, ctx: AppContext, dispatcher, task_sync=None) -> None:  # noqa: ANN001
        self.bot = bot
        self.settings = settings
        self.repos = repos
        self.ctx = ctx
        self.dispatcher = dispatcher
        self.task_sync = task_sync

    # ---- публичная точка входа планировщика ----

    async def sync_tasks(self) -> None:
        """Фоновый синк задач (бот↔Obsidian↔Todoist). Никогда не должен ронять процесс."""
        if self.task_sync is None:
            return
        try:
            await self.task_sync.sync()
        except Exception:  # noqa: BLE001
            log.exception("task sync failed")

    async def tick(self) -> None:
        """Периодический тик: проверяет все слоты и шлёт пинг, если пора."""
        try:
            now = datetime.now()
            today = now.date().isoformat()
            now_min = now.hour * 60 + now.minute
            light = await self._is_light_day()
            for slot in ("morning", "day", "evening"):
                try:
                    await self._maybe_ping(slot, today, now_min, light)
                except Exception:  # noqa: BLE001 — один слот не должен ронять тик
                    log.exception("tick slot=%s failed", slot)
            try:
                await self._maybe_review(now, now_min)
            except Exception:  # noqa: BLE001 — обзоры не должны ронять тик
                log.exception("review tick failed")
            await self._push_hh()
        except Exception:  # noqa: BLE001 — тик никогда не должен падать
            log.exception("tick failed")

    async def _push_hh(self) -> None:
        """Приглашения и отказы с hh — дешёвое чтение sqlite, браузер дёргает крон."""
        try:
            from app.handlers.hh import push_invitations
            n = await push_invitations(self.bot, self.settings.owner_id)
            if n:
                log.info("hh: отправлено уведомлений %d", n)
        except Exception:  # noqa: BLE001 — автоотклик не должен ронять чек-ины
            log.exception("hh push failed")

    # ---- обзоры авторства (недельный / месячный) ----

    async def _maybe_review(self, now: datetime, now_min: int) -> None:
        # недельный
        if now.weekday() == _WEEKLY_WEEKDAY and now_min >= _WEEKLY_AFTER_MIN:
            wk = review.week_key(now.date())
            if await self.repos.settings.get("weekly_review_done") != wk:
                await self.repos.settings.set("weekly_review_done", wk)  # guard на отправку
                await self._start_review(review.start_weekly)
                log.info("weekly review prompt sent (%s)", wk)
        # месячный
        if now.day == _MONTHLY_DAY and now_min >= _MONTHLY_AFTER_MIN:
            mk = review.month_key(now.date())
            if await self.repos.settings.get("monthly_review_done") != mk:
                await self.repos.settings.set("monthly_review_done", mk)
                await self._start_review(review.start_monthly)
                log.info("monthly review prompt sent (%s)", mk)

    async def _start_review(self, starter) -> None:  # noqa: ANN001
        seed = await self._anchor_message()
        state = await self._state_for(seed.chat.id, self.settings.owner_id)
        await starter(seed, state, self.ctx)

    # ---- логика одного слота ----

    async def _maybe_ping(self, slot: str, today: str, now_min: int, light: bool) -> None:
        row = await self.repos.schedule.get(slot)
        if row is None or not row.enabled:
            return
        if light and slot in _LIGHT_DAY_SKIP:
            return
        # уже отмечено сегодня — не напоминаем и прекращаем переспросы (§2)
        if await self.repos.checkins.has_done(today, slot):
            return

        start_min = _hhmm_to_min(row.window_start)
        end_min = _hhmm_to_min(row.window_end)

        state = await self._ping_state(slot, today, start_min, end_min)
        if now_min < state["target"]:
            return  # цель ещё не наступила

        if state["last_ping"] is not None:
            return  # уже пингнули сегодня — ровно один раз, без переспросов

        # Единственный пинг за день. Если ноутбук спал в момент цели — первый тик
        # после пробуждения (now_min >= target) догонит его один раз (новая дата
        # сбросит ping_state на завтра).
        await self._send_ping(slot)
        await self._set_ping_state(slot, today, state["target"], last_ping=now_min)
        log.info("%s reminder (target=%s now=%s)", slot, state["target"], now_min)

    async def _send_ping(self, slot: str) -> None:
        seed = await self._anchor_message()
        state = await self._state_for(seed.chat.id, self.settings.owner_id)
        await _SLOT_STARTERS[slot](seed, state, self.ctx)

    # ---- состояние пинга на сегодня (settings KV) ----

    def _ping_key(self, slot: str) -> str:
        return f"ping_{slot}"

    async def _ping_state(self, slot: str, today: str, start_min: int, end_min: int) -> dict:
        """Возвращает {target, last_ping} на сегодня; при новой дате генерит случайную цель.

        last_ping — минута последнего пинга (None = ещё не пинговали). Используется
        для переспроса каждые reask_interval минут до отметки/конца дня.
        """
        raw = await self.repos.settings.get(self._ping_key(slot))
        data = None
        if raw:
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = None
        if not data or data.get("date") != today:
            target = random.randint(start_min, end_min) if end_min > start_min else start_min
            data = {"date": today, "target": target, "last_ping": None}
            await self.repos.settings.set(self._ping_key(slot), json.dumps(data))
        else:
            data.setdefault("last_ping", None)  # миграция со старого формата {sent}
        return data

    async def _set_ping_state(self, slot: str, today: str, target: int, last_ping: int | None) -> None:
        await self.repos.settings.set(
            self._ping_key(slot),
            json.dumps({"date": today, "target": target, "last_ping": last_ping}),
        )

    # ---- вспомогательное ----

    async def _is_light_day(self) -> bool:
        raw = await self.repos.settings.get("light_day_weekday")
        if raw is None:
            return False
        try:
            wd = int(raw)
        except ValueError:
            return False
        return date_cls.today().weekday() == wd

    async def _anchor_message(self):
        """Сообщение-якорь чата для запуска FSM от имени планировщика.

        start_*(message, ...) шлёт приветствие через message.answer, поэтому ему
        нужен объект Message с привязкой к чату/боту. Отправляем невидимую затравку
        и сразу удаляем — пользователь видит только приветствие из start_*.
        """
        seed = await self.bot.send_message(self.settings.owner_id, "·")
        try:
            await self.bot.delete_message(seed.chat.id, seed.message_id)
        except Exception:  # noqa: BLE001 — не критично, если удалить не вышло
            pass
        return seed

    async def _state_for(self, chat_id: int, user_id: int):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(bot_id=self.bot.id, chat_id=chat_id, user_id=user_id)
        return FSMContext(storage=self.dispatcher.storage, key=key)
