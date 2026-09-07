"""Напоминание по слоту приходит ровно один раз в день — без переспросов."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.scheduler.jobs import JobRunner


class _FakeSchedule:
    async def get(self, slot):  # noqa: ANN001
        return SimpleNamespace(enabled=True, window_start="15:00", window_end="16:00")


class _FakeCheckins:
    def __init__(self) -> None:
        self.done = False

    async def has_done(self, date, slot):  # noqa: ANN001
        return self.done


class _FakeSettings:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, key, default=None):  # noqa: ANN001
        return self.kv.get(key, default)

    async def set(self, key, value):  # noqa: ANN001
        self.kv[key] = value


@pytest.fixture
def runner():
    checkins = _FakeCheckins()
    settings_repo = _FakeSettings()
    repos = SimpleNamespace(schedule=_FakeSchedule(), checkins=checkins, settings=settings_repo)
    stg = SimpleNamespace(scheduler=SimpleNamespace(reask_interval_minutes=30), owner_id=1)
    jr = JobRunner(bot=None, settings=stg, repos=repos, ctx=None, dispatcher=None)

    calls: list[bool] = []

    async def fake_send(slot):  # noqa: ANN001
        calls.append(slot)

    jr._send_ping = fake_send  # type: ignore[method-assign]
    # детерминированная цель окна (15:00 = 900 минут)
    settings_repo.kv["ping_day"] = json.dumps(
        {"date": "2026-06-25", "target": 900, "last_ping": None}
    )
    return jr, checkins, calls


async def test_pings_exactly_once(runner):
    jr, checkins, calls = runner
    today = "2026-06-25"

    await jr._maybe_ping("day", today, 905, False)   # первый (и единственный) пинг
    assert calls == ["day"]

    await jr._maybe_ping("day", today, 940, False)   # больше не переспрашиваем
    assert calls == ["day"]

    await jr._maybe_ping("day", today, 1000, False)  # окно прошло — молчим
    assert calls == ["day"]

    await jr._maybe_ping("day", today, 1400, False)  # поздний вечер — молчим
    assert calls == ["day"]


async def test_catch_up_after_sleep(runner):
    # ноутбук спал в момент цели (900) — первый тик после пробуждения пингует один раз
    jr, checkins, calls = runner
    await jr._maybe_ping("day", "2026-06-25", 1000, False)  # проснулись уже после окна
    assert calls == ["day"]
    await jr._maybe_ping("day", "2026-06-25", 1005, False)
    assert calls == ["day"]


async def test_no_ping_before_target(runner):
    jr, checkins, calls = runner
    await jr._maybe_ping("day", "2026-06-25", 880, False)  # 14:40 < 15:00
    assert calls == []
