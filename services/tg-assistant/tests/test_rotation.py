"""Тест ротации формулировок: не повторять подряд (ADR-7)."""

from __future__ import annotations

import pytest

from app.domain.rotation import Rotator
from app.storage.db import Database
from app.storage.repositories import RotationRepo


@pytest.fixture
async def rotator(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    yield Rotator(RotationRepo(db))
    await db.close()


@pytest.mark.asyncio
async def test_rotation_no_immediate_repeat(rotator):
    pool = ["a", "b", "c"]
    seen = []
    for _ in range(6):
        seen.append(await rotator.next_item("pool_x", pool))
    # ни один соседний элемент не повторяется
    for i in range(1, len(seen)):
        assert seen[i] != seen[i - 1]


@pytest.mark.asyncio
async def test_rotation_cycles_through_all(rotator):
    pool = ["a", "b", "c"]
    first_three = [await rotator.next_item("pool_y", pool) for _ in range(3)]
    assert set(first_three) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_rotation_independent_pools(rotator):
    a1 = await rotator.next_item("pa", ["x", "y"])
    b1 = await rotator.next_item("pb", ["m", "n"])
    a2 = await rotator.next_item("pa", ["x", "y"])
    assert a1 != a2  # пул pa крутится независимо
    assert b1 in ("m", "n")


@pytest.mark.asyncio
async def test_rotation_single_item_pool(rotator):
    # пул из одного — повтор неизбежен, но не падает
    r1 = await rotator.next_item("solo", ["only"])
    r2 = await rotator.next_item("solo", ["only"])
    assert r1 == r2 == "only"
