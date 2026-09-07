"""Настраиваемые привычки (хорошие и вредные) — определения живут в settings KV.

Пользователь сам заводит свои привычки командой /habit_add. Список хранится
одной JSON-строкой в settings под ключом `habits`. Ежедневный трекинг — это
отдельный слот чек-ина `habits`: отмечаешь, что сегодня удалось (хорошую —
сделал, вредную — удержался). В ЕЖЕДНЕВНОМ виде счётчиков нет (философия бота:
без стриков, без баллов, пропуск ≠ провал). Счётчики/динамика — только в
аналитике (📈), где считаются постфактум по завершённым чек-инам.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.storage.repositories import Repositories

_KEY = "habits"

GOOD = "good"
BAD = "bad"


@dataclass(frozen=True)
class Habit:
    id: str
    name: str
    kind: str  # good | bad

    @property
    def is_good(self) -> bool:
        return self.kind == GOOD


def _new_id() -> str:
    return uuid.uuid4().hex[:6]


async def list_habits(repos: Repositories) -> list[Habit]:
    raw = await repos.settings.get(_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[Habit] = []
    for h in data:
        try:
            out.append(Habit(id=h["id"], name=h["name"], kind=h["kind"]))
        except (KeyError, TypeError):
            continue
    return out


async def _save(repos: Repositories, habits: list[Habit]) -> None:
    payload = [{"id": h.id, "name": h.name, "kind": h.kind} for h in habits]
    await repos.settings.set(_KEY, json.dumps(payload, ensure_ascii=False))


async def add_habit(repos: Repositories, kind: str, name: str) -> Habit:
    """Добавляет привычку. kind ∈ {good, bad}. Дубли по имени игнорируются (вернёт существующую)."""
    name = name.strip()
    kind = GOOD if kind not in (GOOD, BAD) else kind
    habits = await list_habits(repos)
    for h in habits:
        if h.name.casefold() == name.casefold() and h.kind == kind:
            return h
    habit = Habit(id=_new_id(), name=name, kind=kind)
    habits.append(habit)
    await _save(repos, habits)
    return habit


async def remove_habit(repos: Repositories, ident: str) -> Habit | None:
    """Удаляет привычку по id, по 1-based номеру в списке или по точному имени."""
    habits = await list_habits(repos)
    target: Habit | None = None
    # по номеру
    if ident.isdigit():
        idx = int(ident) - 1
        if 0 <= idx < len(habits):
            target = habits[idx]
    if target is None:
        for h in habits:
            if h.id == ident or h.name.casefold() == ident.casefold():
                target = h
                break
    if target is None:
        return None
    await _save(repos, [h for h in habits if h.id != target.id])
    return target


def split_by_kind(habits: list[Habit]) -> tuple[list[Habit], list[Habit]]:
    """Возвращает (хорошие, вредные) с сохранением порядка."""
    good = [h for h in habits if h.is_good]
    bad = [h for h in habits if not h.is_good]
    return good, bad
