"""Бэкап/откат последней секции (отмена правки)."""

from __future__ import annotations

import pytest

from app.storage.db import Database
from app.storage.repositories import Repositories


@pytest.fixture
async def repos(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    yield Repositories.build(db)
    await db.close()


async def test_save_and_pop(repos):
    date = "2026-06-25"
    assert await repos.edit_backups.has_backup(date) is False
    await repos.edit_backups.save(date, "## ☀️ Утро · 10:00\nстарый текст")
    assert await repos.edit_backups.has_backup(date) is True

    restored = await repos.edit_backups.pop_last(date)
    assert restored == "## ☀️ Утро · 10:00\nстарый текст"
    # после pop помечен used — повторно не отдаём
    assert await repos.edit_backups.pop_last(date) is None
    assert await repos.edit_backups.has_backup(date) is False


async def test_pop_returns_latest(repos):
    date = "2026-06-25"
    await repos.edit_backups.save(date, "первая версия")
    await repos.edit_backups.save(date, "вторая версия")
    assert await repos.edit_backups.pop_last(date) == "вторая версия"
    assert await repos.edit_backups.pop_last(date) == "первая версия"
