"""Файл задач `Задачи.md` — рендер из БД и парсинг ручных правок.

Формат (один файл-чеклист):

    ---
    type: tasks
    updated: 2026-06-25T12:00:00
    ---
    # Задачи

    ## Активные
    - [ ] Купить молоко ^a1b2c3
    - [ ] Позвонить врачу ^d4e5f6

    ## Выполнено
    - [x] Сдать отчёт ^0099aa

`^uid` — block-ref, стабильный id задачи (маппинг бот↔Obsidian↔Todoist). Парсинг
нужен, чтобы ловить ручные правки прямо в Obsidian (тоггл чекбокса, новый пункт,
правка текста). Запись — атомарная (через .tmp + os.replace) под per-file Lock.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.obsidian.atomic import atomic_write

log = logging.getLogger("assistant.obsidian.tasks")

# - [ ] текст ^uid   |   - [x] текст ^uid   (uid опционален — ручной новый пункт)
_TASK_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(.*?)(?:\s+\^([0-9A-Za-z_-]+))?\s*$")

_HEADER_ACTIVE = "## Активные"
_HEADER_DONE = "## Выполнено"


@dataclass
class ParsedTask:
    uid: str | None
    content: str
    done: bool


def render_tasks(tasks: list, now_iso: str | None = None) -> str:
    """tasks — список объектов с полями .uid .content .done (TaskRow), неудалённые."""
    now_iso = now_iso or datetime.now().isoformat(timespec="seconds")
    active = [t for t in tasks if not t.done]
    done = [t for t in tasks if t.done]

    lines = ["---", "type: tasks", f"updated: {now_iso}", "---", "", "# Задачи", ""]
    lines.append(_HEADER_ACTIVE)
    if active:
        lines += [f"- [ ] {t.content} ^{t.uid}" for t in active]
    else:
        lines.append("_пусто_")
    lines += ["", _HEADER_DONE]
    if done:
        lines += [f"- [x] {t.content} ^{t.uid}" for t in done]
    else:
        lines.append("_пусто_")
    return "\n".join(lines).rstrip() + "\n"


def parse_tasks(text: str) -> list[ParsedTask]:
    """Извлекает все строки-задачи из текста файла (в любой секции)."""
    out: list[ParsedTask] = []
    for line in text.splitlines():
        m = _TASK_RE.match(line)
        if not m:
            continue
        mark, content, uid = m.group(1), m.group(2).strip(), m.group(3)
        if not content:
            continue
        out.append(ParsedTask(uid=uid, content=content, done=mark.lower() == "x"))
    return out


class TasksDoc:
    """Атомарная запись/чтение файла задач (per-file Lock)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def write(self, tasks: list, now_iso: str | None = None) -> None:
        content = render_tasks(tasks, now_iso)
        async with self._lock:
            await asyncio.to_thread(atomic_write, self._path, content)
        log.info("tasks file written: %d задач", len(tasks))

    async def read_parsed(self) -> list[ParsedTask] | None:
        """Парсит файл задач. None — файла ещё нет (нечего подтягивать)."""
        async with self._lock:
            return await asyncio.to_thread(self._read_parsed_sync)

    def _read_parsed_sync(self) -> list[ParsedTask] | None:
        if not self._path.exists():
            return None
        text = self._path.read_text(encoding="utf-8")
        return parse_tasks(text)


_ARCHIVE_HEADER = (
    "---\ntype: tasks-archive\n---\n\n# Архив задач\n\n"
    "История очищенных выполненных задач. Пополняется автоматически "
    "при «🧹 Очистить выполненные» — активный список остаётся чистым, "
    "а сделанное никуда не пропадает.\n"
)


def _fmt_completed(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _archive_lines(tasks: list, day: str) -> list[str]:
    """Строки-задачи для архива (с отметкой времени выполнения)."""
    return [f"- [x] {t.content} ^{t.uid} (выполнено {_fmt_completed(t.completed_at)})"
            for t in tasks]


class TasksArchiveDoc:
    """Append-only история очищенных задач (`Архив задач.md`).

    Группирует записи под заголовком `## YYYY-MM-DD`. Даты монотонны (всегда
    дописываем «сегодня»), поэтому заголовок текущего дня, если уже есть, —
    последний в файле, и новые строки безопасно добавляются в конец.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def append(self, tasks: list, day: str | None = None) -> None:
        if not tasks:
            return
        day = day or datetime.now().strftime("%Y-%m-%d")
        async with self._lock:
            await asyncio.to_thread(self._append_sync, tasks, day)
        log.info("tasks archive appended: %d задач (%s)", len(tasks), day)

    def _append_sync(self, tasks: list, day: str) -> None:
        text = self._path.read_text(encoding="utf-8") if self._path.exists() else _ARCHIVE_HEADER
        body = text.rstrip("\n")
        heading = f"## {day}"
        parts = [body]
        if heading not in text:
            parts.append("")  # пустая строка перед новым заголовком дня
            parts.append(heading)
        parts.extend(_archive_lines(tasks, day))
        content = "\n".join(parts).rstrip("\n") + "\n"
        atomic_write(self._path, content)
