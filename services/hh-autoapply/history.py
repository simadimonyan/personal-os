#!/usr/bin/env python3
"""
История откликов hh-autoapply в SQLite (идея взята из reference-проекта,
где плоский список id заменён на БД со смыслом).

Зачем БД вместо applied.json: плоский список из 226 id отвечал только на вопрос
«трогали ли мы эту вакансию». Теперь храним ещё название, компанию, оценку
модели с обоснованием, отправленное письмо и статус — по этому можно понять,
что именно отсеивается, какие письма уходят и куда зовут на собеседование.

applied.json остаётся источником правды для миграции и продолжает писаться
для обратной совместимости (его читает mission-control).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "history.db"
LEGACY_APPLIED = BASE / "applied.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applied (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    url         TEXT,
    salary      TEXT,
    score       INTEGER,      -- оценка релевантности моделью 0–10 (NULL, если LLM не работал)
    reason      TEXT,         -- почему модель так решила
    letter      TEXT,         -- отправленное сопроводительное
    outcome     TEXT,         -- applied / already / skip / rejected_by_llm
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS negotiations (
    key         TEXT PRIMARY KEY,   -- компания|вакансия
    title       TEXT,
    company     TEXT,
    status      TEXT,               -- последний увиденный статус на hh
    url         TEXT,
    kind        TEXT,               -- invite / reject / pending
    announced   INTEGER DEFAULT 0,  -- 1 = про это изменение уже сказали в Telegram
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Индексы создаются ПОСЛЕ ALTER-миграций: на старой базе CREATE TABLE IF NOT EXISTS
# не добавит новых колонок, и индекс по ним свалит весь executescript.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_applied_outcome ON applied(outcome);
CREATE INDEX IF NOT EXISTS idx_negotiations_announced ON negotiations(announced, kind);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # догнать схему на базах, созданных до появления колонок kind/announced
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(negotiations)")}
    for col, ddl in (("kind", "TEXT"), ("announced", "INTEGER DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE negotiations ADD COLUMN {col} {ddl}")
    conn.executescript(INDEXES)
    # WAL — чтобы mission-control мог читать историю, пока идёт прогон откликов
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate_legacy(conn: sqlite3.Connection) -> int:
    """Перенести id из applied.json, которых ещё нет в БД. Возвращает число перенесённых."""
    if not LEGACY_APPLIED.exists():
        return 0
    try:
        ids = json.loads(LEGACY_APPLIED.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    known = {r["id"] for r in conn.execute("SELECT id FROM applied")}
    new = [i for i in map(str, ids) if i not in known]
    conn.executemany(
        "INSERT OR IGNORE INTO applied (id, outcome) VALUES (?, 'legacy')",
        [(i,) for i in new],
    )
    conn.commit()
    return len(new)


def touched_ids(conn: sqlite3.Connection) -> set[str]:
    """Все вакансии, которые мы уже так или иначе отработали — повторно не трогаем."""
    return {r["id"] for r in conn.execute("SELECT id FROM applied")}


def record(conn: sqlite3.Connection, vacancy: dict, *, outcome: str,
           score: int | None = None, reason: str = "", letter: str = "") -> None:
    conn.execute(
        """INSERT INTO applied (id, title, company, url, salary, score, reason, letter, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, company=excluded.company, url=excluded.url,
             salary=excluded.salary, score=excluded.score, reason=excluded.reason,
             letter=excluded.letter, outcome=excluded.outcome""",
        (str(vacancy.get("id")), vacancy.get("title"), vacancy.get("company"),
         vacancy.get("url"), vacancy.get("salary"), score, reason, letter, outcome),
    )
    conn.commit()


def sync_legacy_file(conn: sqlite3.Connection) -> None:
    """Отзеркалить id обратно в applied.json — его читает mission-control."""
    ids = sorted(touched_ids(conn))
    LEGACY_APPLIED.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT outcome, COUNT(*) c FROM applied GROUP BY outcome")
    return {r["outcome"] or "unknown": r["c"] for r in rows}


if __name__ == "__main__":
    with connect() as c:
        moved = migrate_legacy(c)
        print(f"история: перенесено из applied.json {moved}, всего в БД {len(touched_ids(c))}")
        print("по исходам:", stats(c))
