"""Ротация формулировок (ADR-7): не повторять подряд.

Состояние last_index хранится в rotation_state (SQLite), а не random каждый раз.
next_index = (last_index + 1) % len(pool) — детерминированный цикл,
гарантированно не повторяет предыдущую формулировку.
"""

from __future__ import annotations

from app.storage.repositories import RotationRepo


class Rotator:
    def __init__(self, repo: RotationRepo) -> None:
        self._repo = repo

    async def next_index(self, pool_key: str, pool_size: int) -> int:
        """Возвращает индекс следующей формулировки и фиксирует его в БД."""
        if pool_size <= 0:
            return 0
        last = await self._repo.get_last_index(pool_key)
        nxt = (last + 1) % pool_size
        await self._repo.set_last_index(pool_key, nxt)
        return nxt

    async def next_item(self, pool_key: str, pool: list[str]) -> str:
        idx = await self.next_index(pool_key, len(pool))
        return pool[idx]
