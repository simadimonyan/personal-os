"""Инициализация aiosqlite-соединения и авто-миграции.

Single-user → одно долгоживущее соединение на процесс. WAL для устойчивости
к рестарту daemon. Миграции — простые нумерованные .sql, применяются по порядку,
версия отслеживается в таблице schema_migrations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger("assistant.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Database:
    """Обёртка над одним aiosqlite-соединением."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database не инициализирована: вызови connect()")
        return self._conn

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA busy_timeout=5000;")
        await self._conn.commit()
        await self._migrate()
        log.info("database connected: %s", self._db_path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version TEXT PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await self.conn.commit()

        cur = await self.conn.execute("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in await cur.fetchall()}

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem  # '001_init'
            if version in applied:
                continue
            log.info("applying migration %s", version)
            sql = sql_file.read_text(encoding="utf-8")
            await self.conn.executescript(sql)
            await self.conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
            await self.conn.commit()
