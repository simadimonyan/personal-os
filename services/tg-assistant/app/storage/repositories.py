"""CRUD-репозитории для всех таблиц.

Все запросы — параметризованные (никакой конкатенации SQL).
Время хранится в ISO8601 (UTC-naive локальное — single-user, одна машина).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.storage.db import Database


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ============================================================================
# checkins
# ============================================================================

@dataclass
class CheckinRow:
    id: int
    date: str
    slot: str
    status: str
    started_at: str
    finished_at: str | None
    answers: dict[str, Any]
    flags: list[str]
    written: bool


class CheckinRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, date: str, slot: str) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO checkins (date, slot, status, started_at) "
            "VALUES (?, ?, 'in_progress', ?)",
            (date, slot, _now_iso()),
        )
        await self._db.conn.commit()
        return cur.lastrowid

    async def update_answers(self, checkin_id: int, answers: dict[str, Any]) -> None:
        await self._db.conn.execute(
            "UPDATE checkins SET answers_json = ? WHERE id = ?",
            (json.dumps(answers, ensure_ascii=False), checkin_id),
        )
        await self._db.conn.commit()

    async def finish(self, checkin_id: int, flags: list[str]) -> None:
        await self._db.conn.execute(
            "UPDATE checkins SET status = 'done', finished_at = ?, flags_json = ? "
            "WHERE id = ?",
            (_now_iso(), json.dumps(flags, ensure_ascii=False), checkin_id),
        )
        await self._db.conn.commit()

    async def abandon(self, checkin_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE checkins SET status = 'abandoned', finished_at = ? WHERE id = ?",
            (_now_iso(), checkin_id),
        )
        await self._db.conn.commit()

    async def mark_written(self, checkin_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE checkins SET written = 1 WHERE id = ?", (checkin_id,)
        )
        await self._db.conn.commit()

    async def get(self, checkin_id: int) -> CheckinRow | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM checkins WHERE id = ?", (checkin_id,)
        )
        row = await cur.fetchone()
        return self._to_row(row) if row else None

    async def history(self, slot: str | None = None, limit: int = 60) -> list[CheckinRow]:
        """Завершённые чек-ины, новые первыми. Используется domain/flags."""
        if slot:
            cur = await self._db.conn.execute(
                "SELECT * FROM checkins WHERE status = 'done' AND slot = ? "
                "ORDER BY date DESC, started_at DESC LIMIT ?",
                (slot, limit),
            )
        else:
            cur = await self._db.conn.execute(
                "SELECT * FROM checkins WHERE status = 'done' "
                "ORDER BY date DESC, started_at DESC LIMIT ?",
                (limit,),
            )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def for_date(self, date: str) -> list[CheckinRow]:
        """Все завершённые чек-ины за дату (для статуса «Сегодня»), старые первыми."""
        cur = await self._db.conn.execute(
            "SELECT * FROM checkins WHERE date = ? AND status = 'done' "
            "ORDER BY started_at ASC",
            (date,),
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def has_done(self, date: str, slot: str) -> bool:
        """Есть ли уже завершённый чек-ин данного слота за дату.

        Используется планировщиком, чтобы не напоминать про слот, который
        пользователь уже отметил сегодня (§2: никаких лишних пингов).
        """
        cur = await self._db.conn.execute(
            "SELECT 1 FROM checkins WHERE date = ? AND slot = ? AND status = 'done' LIMIT 1",
            (date, slot),
        )
        return await cur.fetchone() is not None

    async def last_for_date(self, date: str) -> CheckinRow | None:
        """Последний чек-ин за дату (для edit_last)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM checkins WHERE date = ? AND status = 'done' "
            "ORDER BY started_at DESC LIMIT 1",
            (date,),
        )
        row = await cur.fetchone()
        return self._to_row(row) if row else None

    # ---- аналитика (heatmap + статистика) ----

    # Слоты-«состояния», которые считаются за «отметку» дня для heatmap-градации.
    # Заметки (note) — не состояние, не учитываем в интенсивности.
    _STATE_SLOTS = ("morning", "day", "evening", "situational")

    async def counts_by_date(self, start: str, end: str) -> dict[str, int]:
        """Число отмеченных состояний по датам в [start, end] (для heatmap-градации).

        Считаются завершённые чек-ины слотов morning/day/evening/situational.
        Возвращает {YYYY-MM-DD: count} только для дат, где count > 0.
        """
        placeholders = ",".join("?" for _ in self._STATE_SLOTS)
        cur = await self._db.conn.execute(
            f"SELECT date, COUNT(*) AS cnt FROM checkins "
            f"WHERE status = 'done' AND slot IN ({placeholders}) "
            f"AND date >= ? AND date <= ? GROUP BY date",
            (*self._STATE_SLOTS, start, end),
        )
        return {r["date"]: r["cnt"] for r in await cur.fetchall()}

    async def for_period(self, start: str, end: str) -> list[CheckinRow]:
        """Все завершённые чек-ины за диапазон дат [start, end] (для агрегатов статистики)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM checkins WHERE status = 'done' "
            "AND date >= ? AND date <= ? ORDER BY date ASC, started_at ASC",
            (start, end),
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    @staticmethod
    def _to_row(row: Any) -> CheckinRow:
        return CheckinRow(
            id=row["id"],
            date=row["date"],
            slot=row["slot"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            answers=json.loads(row["answers_json"]),
            flags=json.loads(row["flags_json"]),
            written=bool(row["written"]),
        )


# ============================================================================
# schedule
# ============================================================================

@dataclass
class ScheduleRow:
    slot: str
    window_start: str
    window_end: str
    enabled: bool


class ScheduleRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def all(self) -> list[ScheduleRow]:
        cur = await self._db.conn.execute(
            "SELECT slot, window_start, window_end, enabled FROM schedule"
        )
        return [
            ScheduleRow(r["slot"], r["window_start"], r["window_end"], bool(r["enabled"]))
            for r in await cur.fetchall()
        ]

    async def get(self, slot: str) -> ScheduleRow | None:
        cur = await self._db.conn.execute(
            "SELECT slot, window_start, window_end, enabled FROM schedule WHERE slot = ?",
            (slot,),
        )
        r = await cur.fetchone()
        if not r:
            return None
        return ScheduleRow(r["slot"], r["window_start"], r["window_end"], bool(r["enabled"]))

    async def update_window(self, slot: str, start: str, end: str) -> None:
        await self._db.conn.execute(
            "UPDATE schedule SET window_start = ?, window_end = ? WHERE slot = ?",
            (start, end, slot),
        )
        await self._db.conn.commit()

    async def set_enabled(self, slot: str, enabled: bool) -> None:
        await self._db.conn.execute(
            "UPDATE schedule SET enabled = ? WHERE slot = ?",
            (1 if enabled else 0, slot),
        )
        await self._db.conn.commit()


# ============================================================================
# settings
# ============================================================================

class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: str | None = None) -> str | None:
        cur = await self._db.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        r = await cur.fetchone()
        return r["value"] if r else default

    async def set(self, key: str, value: str) -> None:
        await self._db.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.conn.commit()

    async def all(self) -> dict[str, str]:
        cur = await self._db.conn.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in await cur.fetchall()}


# ============================================================================
# outbox
# ============================================================================

@dataclass
class OutboxRow:
    id: int
    checkin_id: int
    payload: dict[str, Any]
    attempts: int
    last_error: str | None
    next_attempt_at: str | None
    created_at: str
    done: bool


class OutboxRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(self, checkin_id: int, payload: dict[str, Any]) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO outbox (checkin_id, payload_json, created_at, next_attempt_at) "
            "VALUES (?, ?, ?, ?)",
            (checkin_id, json.dumps(payload, ensure_ascii=False), _now_iso(), _now_iso()),
        )
        await self._db.conn.commit()
        return cur.lastrowid

    async def due(self, limit: int = 20) -> list[OutboxRow]:
        """Невыполненные записи, у которых наступило время попытки."""
        now = _now_iso()
        cur = await self._db.conn.execute(
            "SELECT * FROM outbox WHERE done = 0 "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id ASC LIMIT ?",
            (now, limit),
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def mark_done(self, outbox_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE outbox SET done = 1 WHERE id = ?", (outbox_id,)
        )
        await self._db.conn.commit()

    async def mark_failed(self, outbox_id: int, error: str, next_attempt_at: str) -> None:
        await self._db.conn.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ?, "
            "next_attempt_at = ? WHERE id = ?",
            (error[:1000], next_attempt_at, outbox_id),
        )
        await self._db.conn.commit()

    @staticmethod
    def _to_row(row: Any) -> OutboxRow:
        return OutboxRow(
            id=row["id"],
            checkin_id=row["checkin_id"],
            payload=json.loads(row["payload_json"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            next_attempt_at=row["next_attempt_at"],
            created_at=row["created_at"],
            done=bool(row["done"]),
        )


# ============================================================================
# rotation_state
# ============================================================================

class RotationRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_last_index(self, pool_key: str) -> int:
        cur = await self._db.conn.execute(
            "SELECT last_index FROM rotation_state WHERE pool_key = ?", (pool_key,)
        )
        r = await cur.fetchone()
        return r["last_index"] if r else -1

    async def set_last_index(self, pool_key: str, index: int) -> None:
        await self._db.conn.execute(
            "INSERT INTO rotation_state (pool_key, last_index, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(pool_key) DO UPDATE SET last_index = excluded.last_index, "
            "updated_at = excluded.updated_at",
            (pool_key, index, _now_iso()),
        )
        await self._db.conn.commit()


# ============================================================================
# channel_messages (мониторинг forum-группы, миграция 002)
# ============================================================================

class ChannelMessageRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self,
        channel_id: int,
        topic_id: int | None,
        message_id: int,
        text: str | None,
        caption: str | None,
        media_type: str | None,
        media_file_id: str | None,
        from_user_id: int | None,
        sender_name: str | None,
        sent_at: str,
    ) -> int:
        """INSERT OR IGNORE по UNIQUE(channel_id, message_id). Возвращает row id.

        Идемпотентно: повторный апдейт того же сообщения не создаёт дубль и не
        сбрасывает статус parsed (важно для ретриггеров реакцией).
        """
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO channel_messages "
            "(channel_id, topic_id, message_id, text, caption, media_type, "
            " media_file_id, from_user_id, sender_name, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                channel_id, topic_id, message_id, text, caption, media_type,
                media_file_id, from_user_id, sender_name, sent_at,
            ),
        )
        await self._db.conn.commit()
        cur = await self._db.conn.execute(
            "SELECT id FROM channel_messages WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        row = await cur.fetchone()
        return row["id"] if row else 0

    async def update_content(
        self,
        channel_id: int,
        message_id: int,
        text: str | None,
        caption: str | None,
    ) -> None:
        """Обновляет текст/caption (для отредактированных сообщений)."""
        await self._db.conn.execute(
            "UPDATE channel_messages SET text = ?, caption = ? "
            "WHERE channel_id = ? AND message_id = ?",
            (text, caption, channel_id, message_id),
        )
        await self._db.conn.commit()

    async def mark_parsed(
        self, channel_id: int, message_id: int, obsidian_path: str
    ) -> None:
        await self._db.conn.execute(
            "UPDATE channel_messages SET parsed = 1, parsed_at = ?, "
            "obsidian_path = ?, parse_error = NULL "
            "WHERE channel_id = ? AND message_id = ?",
            (_now_iso(), obsidian_path, channel_id, message_id),
        )
        await self._db.conn.commit()

    async def mark_error(
        self, channel_id: int, message_id: int, error: str
    ) -> None:
        await self._db.conn.execute(
            "UPDATE channel_messages SET parsed = -1, parse_error = ? "
            "WHERE channel_id = ? AND message_id = ?",
            (error[:1000], channel_id, message_id),
        )
        await self._db.conn.commit()

    async def get(self, channel_id: int, message_id: int) -> dict[str, Any] | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM channel_messages WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def is_parsed(self, channel_id: int, message_id: int) -> bool:
        cur = await self._db.conn.execute(
            "SELECT parsed FROM channel_messages "
            "WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        row = await cur.fetchone()
        return bool(row) and row["parsed"] == 1

    async def get_pending(self, channel_id: int) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM channel_messages WHERE channel_id = ? AND parsed = 0 "
            "ORDER BY sent_at ASC",
            (channel_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def recent_topics(
        self, channel_id: int, limit: int = 200
    ) -> list[tuple[int | None, str]]:
        """Уникальные (topic_id, последний sender_name) из недавних сообщений.

        Для /topics — помогает узнать topic_id для настройки маппинга.
        """
        cur = await self._db.conn.execute(
            "SELECT topic_id, MAX(sent_at) AS last_at, COUNT(*) AS cnt "
            "FROM channel_messages WHERE channel_id = ? "
            "GROUP BY topic_id ORDER BY last_at DESC LIMIT ?",
            (channel_id, limit),
        )
        return [(r["topic_id"], f"{r['cnt']} сообщ., посл. {r['last_at']}")
                for r in await cur.fetchall()]


# ============================================================================
# section_backups (отмена правки последней записи, миграция 003)
# ============================================================================

class EditBackupRepo:
    """Бэкап последней секции дневного файла перед правкой — для «↩️ Отменить правку»."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, date: str, section_text: str) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO section_backups (date, section_text, created_at) VALUES (?, ?, ?)",
            (date, section_text, _now_iso()),
        )
        await self._db.conn.commit()
        return cur.lastrowid

    async def pop_last(self, date: str) -> str | None:
        """Возвращает текст последнего неиспользованного бэкапа за дату и помечает used."""
        cur = await self._db.conn.execute(
            "SELECT id, section_text FROM section_backups "
            "WHERE date = ? AND used = 0 ORDER BY id DESC LIMIT 1",
            (date,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        await self._db.conn.execute(
            "UPDATE section_backups SET used = 1 WHERE id = ?", (row["id"],)
        )
        await self._db.conn.commit()
        return row["section_text"]

    async def has_backup(self, date: str) -> bool:
        cur = await self._db.conn.execute(
            "SELECT 1 FROM section_backups WHERE date = ? AND used = 0 LIMIT 1", (date,)
        )
        return await cur.fetchone() is not None


# ============================================================================
# tasks (таск-менеджер бот↔Obsidian↔Todoist, миграция 004)
# ============================================================================

@dataclass
class TaskRow:
    id: int
    uid: str
    content: str
    done: bool
    todoist_id: str | None
    source: str
    created_at: str
    updated_at: str
    completed_at: str | None
    deleted: bool
    dirty_obsidian: bool
    dirty_todoist: bool
    archived: bool = False
    archived_at: str | None = None
    priority: int = 1                  # 1..4 (4 = p1, высший) — как в Todoist
    due_date: str | None = None        # YYYY-MM-DD
    due_datetime: str | None = None    # ISO8601 с временем
    due_string: str | None = None      # человеческая строка ("завтра 18:00")
    project_id: str | None = None
    section_id: str | None = None
    labels: list[str] = field(default_factory=list)


class TaskRepo:
    """CRUD задач. Бот — координатор; флаги dirty_* отмечают, что не дотолкнуто наружу."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        uid: str,
        content: str,
        source: str = "bot",
        done: bool = False,
        todoist_id: str | None = None,
        dirty_obsidian: bool = True,
        dirty_todoist: bool = True,
        priority: int = 1,
        due_date: str | None = None,
        due_datetime: str | None = None,
        due_string: str | None = None,
        project_id: str | None = None,
        section_id: str | None = None,
        labels: list[str] | None = None,
    ) -> TaskRow:
        now = _now_iso()
        completed = now if done else None
        await self._db.conn.execute(
            "INSERT INTO tasks (uid, content, done, todoist_id, source, created_at, "
            "updated_at, completed_at, dirty_obsidian, dirty_todoist, priority, "
            "due_date, due_datetime, due_string, project_id, section_id, labels) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, content, 1 if done else 0, todoist_id, source, now, now, completed,
             1 if dirty_obsidian else 0, 1 if dirty_todoist else 0, priority,
             due_date, due_datetime, due_string, project_id, section_id,
             json.dumps(labels or [], ensure_ascii=False)),
        )
        await self._db.conn.commit()
        row = await self.get_by_uid(uid)
        assert row is not None
        return row

    async def get_by_uid(self, uid: str) -> TaskRow | None:
        cur = await self._db.conn.execute("SELECT * FROM tasks WHERE uid = ?", (uid,))
        row = await cur.fetchone()
        return self._to_row(row) if row else None

    async def get_by_todoist_id(self, todoist_id: str) -> TaskRow | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE todoist_id = ? AND deleted = 0", (todoist_id,)
        )
        row = await cur.fetchone()
        return self._to_row(row) if row else None

    async def list_active(self) -> list[TaskRow]:
        """Невыполненные неудалённые задачи (для списка в боте)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE deleted = 0 AND archived = 0 AND done = 0 "
            "ORDER BY created_at ASC"
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def list_done(self) -> list[TaskRow]:
        """Выполненные, ещё не заархивированные задачи (кандидаты на «Очистить»)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE deleted = 0 AND archived = 0 AND done = 1 "
            "ORDER BY completed_at ASC, created_at ASC"
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def list_for_obsidian(self) -> list[TaskRow]:
        """Активные + выполненные (но не заархивированные) задачи для рендера файла."""
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE deleted = 0 AND archived = 0 "
            "ORDER BY done ASC, created_at ASC"
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def list_with_todoist_id(self) -> list[TaskRow]:
        """Активные задачи с todoist_id (для сверки с активным списком Todoist)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE deleted = 0 AND archived = 0 "
            "AND todoist_id IS NOT NULL"
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def pending_todoist(self) -> list[TaskRow]:
        """Задачи, которые надо дотолкнуть в Todoist (вкл. удалённые с todoist_id — для удаления)."""
        cur = await self._db.conn.execute(
            "SELECT * FROM tasks WHERE dirty_todoist = 1 ORDER BY id ASC"
        )
        return [self._to_row(r) for r in await cur.fetchall()]

    async def set_done(self, uid: str, done: bool, dirty_todoist: bool = True) -> None:
        now = _now_iso()
        completed = now if done else None
        await self._db.conn.execute(
            "UPDATE tasks SET done = ?, completed_at = ?, updated_at = ?, "
            "dirty_obsidian = 1, dirty_todoist = ? WHERE uid = ?",
            (1 if done else 0, completed, now, 1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def update_content(self, uid: str, content: str, dirty_todoist: bool = True) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET content = ?, updated_at = ?, dirty_obsidian = 1, "
            "dirty_todoist = ? WHERE uid = ?",
            (content, _now_iso(), 1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def set_priority(self, uid: str, priority: int, dirty_todoist: bool = True) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET priority = ?, updated_at = ?, dirty_obsidian = 1, "
            "dirty_todoist = ? WHERE uid = ?",
            (priority, _now_iso(), 1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def set_due(
        self,
        uid: str,
        due_date: str | None,
        due_datetime: str | None,
        due_string: str | None,
        dirty_todoist: bool = True,
    ) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET due_date = ?, due_datetime = ?, due_string = ?, "
            "updated_at = ?, dirty_obsidian = 1, dirty_todoist = ? WHERE uid = ?",
            (due_date, due_datetime, due_string, _now_iso(),
             1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def set_project(
        self, uid: str, project_id: str | None, section_id: str | None = None,
        dirty_todoist: bool = True,
    ) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET project_id = ?, section_id = ?, updated_at = ?, "
            "dirty_obsidian = 1, dirty_todoist = ? WHERE uid = ?",
            (project_id, section_id, _now_iso(), 1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def set_labels(self, uid: str, labels: list[str], dirty_todoist: bool = True) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET labels = ?, updated_at = ?, dirty_obsidian = 1, "
            "dirty_todoist = ? WHERE uid = ?",
            (json.dumps(labels, ensure_ascii=False), _now_iso(),
             1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def soft_delete(self, uid: str, dirty_todoist: bool = True) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET deleted = 1, updated_at = ?, dirty_obsidian = 1, "
            "dirty_todoist = ? WHERE uid = ?",
            (_now_iso(), 1 if dirty_todoist else 0, uid),
        )
        await self._db.conn.commit()

    async def mark_archived(self, uids: list[str]) -> None:
        """Убирает задачи из активного статуса (бот + Задачи.md), сохраняя запись в БД.
        Todoist не трогаем (выполненная задача уже закрыта там) → dirty_todoist=0.
        dirty_obsidian=1 — чтобы активный файл перерисовался уже без них."""
        if not uids:
            return
        now = _now_iso()
        qmarks = ",".join("?" for _ in uids)
        await self._db.conn.execute(
            f"UPDATE tasks SET archived = 1, archived_at = ?, updated_at = ?, "
            f"dirty_obsidian = 1, dirty_todoist = 0 WHERE uid IN ({qmarks})",
            (now, now, *uids),
        )
        await self._db.conn.commit()

    async def set_todoist_id(self, uid: str, todoist_id: str) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET todoist_id = ? WHERE uid = ?", (todoist_id, uid)
        )
        await self._db.conn.commit()

    async def clear_dirty_todoist(self, uid: str) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET dirty_todoist = 0 WHERE uid = ?", (uid,)
        )
        await self._db.conn.commit()

    async def clear_dirty_obsidian_all(self) -> None:
        await self._db.conn.execute("UPDATE tasks SET dirty_obsidian = 0")
        await self._db.conn.commit()

    async def any_dirty_obsidian(self) -> bool:
        cur = await self._db.conn.execute(
            "SELECT 1 FROM tasks WHERE dirty_obsidian = 1 LIMIT 1"
        )
        return await cur.fetchone() is not None

    # ---- проекты Todoist (кэш имён для отображения и фильтра) ----

    async def upsert_projects(self, projects: list[tuple[str, str, bool]]) -> None:
        """projects — список (id, name, is_inbox)."""
        now = _now_iso()
        for pid, name, is_inbox in projects:
            await self._db.conn.execute(
                "INSERT INTO todoist_projects (id, name, is_inbox, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name = excluded.name, is_inbox = excluded.is_inbox, updated_at = excluded.updated_at",
                (pid, name, 1 if is_inbox else 0, now),
            )
        await self._db.conn.commit()

    async def project_names(self) -> dict[str, str]:
        cur = await self._db.conn.execute("SELECT id, name FROM todoist_projects")
        return {r["id"]: r["name"] for r in await cur.fetchall()}

    async def inbox_project_id(self) -> str | None:
        cur = await self._db.conn.execute(
            "SELECT id FROM todoist_projects WHERE is_inbox = 1 LIMIT 1"
        )
        row = await cur.fetchone()
        return row["id"] if row else None

    @staticmethod
    def _to_row(row: Any) -> TaskRow:
        return TaskRow(
            id=row["id"],
            uid=row["uid"],
            content=row["content"],
            done=bool(row["done"]),
            todoist_id=row["todoist_id"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            deleted=bool(row["deleted"]),
            dirty_obsidian=bool(row["dirty_obsidian"]),
            dirty_todoist=bool(row["dirty_todoist"]),
            archived=bool(row["archived"]),
            archived_at=row["archived_at"],
            priority=int(row["priority"]) if row["priority"] is not None else 1,
            due_date=row["due_date"],
            due_datetime=row["due_datetime"],
            due_string=row["due_string"],
            project_id=row["project_id"],
            section_id=row["section_id"],
            labels=json.loads(row["labels"]) if row["labels"] else [],
        )


@dataclass
class Repositories:
    """Контейнер всех репозиториев — удобно прокидывать в bot workflow_data."""

    checkins: CheckinRepo
    schedule: ScheduleRepo
    settings: SettingsRepo
    outbox: OutboxRepo
    rotation: RotationRepo
    channel_messages: ChannelMessageRepo
    edit_backups: EditBackupRepo
    tasks: TaskRepo

    @classmethod
    def build(cls, db: Database) -> Repositories:
        return cls(
            checkins=CheckinRepo(db),
            schedule=ScheduleRepo(db),
            settings=SettingsRepo(db),
            outbox=OutboxRepo(db),
            rotation=RotationRepo(db),
            channel_messages=ChannelMessageRepo(db),
            edit_backups=EditBackupRepo(db),
            tasks=TaskRepo(db),
        )
