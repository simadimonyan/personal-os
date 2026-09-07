"""Тонкий async-клиент Todoist API v1 (aiohttp).

Только то, что нужно таск-менеджеру: список активных задач, создать, закрыть,
переоткрыть, обновить текст, удалить. Сетевые/HTTP-ошибки оборачиваются в
TodoistError — вызывающий код (task_sync) ловит и оставляет задачу dirty до
следующего синка (надёжность как у outbox, бот не падает).

ВАЖНО: старый REST v2 (/rest/v2) выведен из строя (HTTP 410 Gone). Используем
новый унифицированный API /api/v1. Отличия от v2, которые здесь учтены:
  • GET /tasks отдаёт {"results": [...], "next_cursor": ...} — нужна пагинация;
  • признак выполненности — поле `checked` (а не `is_completed`).

Документация: https://developer.todoist.com/
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("assistant.todoist")

_BASE = "https://api.todoist.com/api/v1"


class TodoistError(RuntimeError):
    """Любая ошибка обращения к Todoist API (сеть/HTTP/парсинг)."""


@dataclass
class TodoistTask:
    id: str
    content: str
    is_completed: bool
    project_id: str | None = None
    section_id: str | None = None
    priority: int = 1                  # 1..4 (4 = p1, высший)
    due_date: str | None = None        # YYYY-MM-DD
    due_datetime: str | None = None    # ISO8601 c временем
    due_string: str | None = None      # человеческая строка ("завтра 18:00")
    labels: list[str] = field(default_factory=list)


@dataclass
class TodoistProject:
    id: str
    name: str
    is_inbox: bool = False


def _parse_due(due: dict | None) -> tuple[str | None, str | None, str | None]:
    """Возвращает (due_date, due_datetime, due_string) из объекта due Todoist.
    В v1 точная дата/время лежит в due['date'] (может быть и просто датой,
    и полным datetime); строка — в due['string']."""
    if not due:
        return None, None, None
    raw = due.get("date") or due.get("datetime")
    due_string = due.get("string")
    if not raw:
        return None, None, due_string
    if "T" in raw:
        return raw.split("T", 1)[0], raw, due_string
    return raw, None, due_string


class TodoistClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        project_id: str = "",
        timeout: float = 20.0,
    ) -> None:
        self._session = session
        self._token = token
        self._project_id = project_id or ""
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def list_tasks(self) -> list[TodoistTask]:
        """Активные (незакрытые) задачи. v1 /tasks отдаёт {"results": [...],
        "next_cursor": ...} — проходим все страницы. На всякий случай отфильтровываем
        выполненные (checked), чтобы list_tasks возвращал только активные, как раньше."""
        out: list[TodoistTask] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"limit": "200"}
            if self._project_id:
                params["project_id"] = self._project_id
            if cursor:
                params["cursor"] = cursor
            data = await self._request("GET", "/tasks", params=params)
            if not isinstance(data, dict) or "results" not in data:
                raise TodoistError(f"unexpected /tasks payload: {type(data)}")
            for t in data["results"]:
                if t.get("checked") or t.get("is_completed") or t.get("is_deleted"):
                    continue
                due_date, due_dt, due_str = _parse_due(t.get("due"))
                out.append(
                    TodoistTask(
                        id=str(t["id"]),
                        content=t.get("content", ""),
                        is_completed=bool(t.get("checked", False)),
                        project_id=str(t["project_id"]) if t.get("project_id") else None,
                        section_id=str(t["section_id"]) if t.get("section_id") else None,
                        priority=int(t.get("priority", 1) or 1),
                        due_date=due_date,
                        due_datetime=due_dt,
                        due_string=due_str,
                        labels=list(t.get("labels") or []),
                    )
                )
            cursor = data.get("next_cursor")
            if not cursor:
                return out

    async def list_projects(self) -> list[TodoistProject]:
        """Все проекты (для имён и фильтра по проектам). v1 /projects — пагинирован."""
        out: list[TodoistProject] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"limit": "200"}
            if cursor:
                params["cursor"] = cursor
            data = await self._request("GET", "/projects", params=params)
            results = data["results"] if isinstance(data, dict) and "results" in data else data
            if not isinstance(results, list):
                raise TodoistError(f"unexpected /projects payload: {type(data)}")
            for p in results:
                out.append(TodoistProject(
                    id=str(p["id"]),
                    name=p.get("name", ""),
                    is_inbox=bool(p.get("is_inbox_project") or p.get("inbox_project")),
                ))
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor:
                return out

    async def create_task(
        self,
        content: str,
        *,
        project_id: str | None = None,
        priority: int | None = None,
        due_string: str | None = None,
        labels: list[str] | None = None,
    ) -> str:
        body: dict = {"content": content}
        pid = project_id or self._project_id
        if pid:
            body["project_id"] = pid
        if priority and priority != 1:
            body["priority"] = priority
        if due_string:
            body["due_string"] = due_string
        if labels:
            body["labels"] = labels
        data = await self._request("POST", "/tasks", json=body)
        if not isinstance(data, dict) or "id" not in data:
            raise TodoistError(f"create_task: no id in response: {data}")
        return str(data["id"])

    async def update_content(self, task_id: str, content: str) -> None:
        await self._request("POST", f"/tasks/{task_id}", json={"content": content})

    async def update_priority(self, task_id: str, priority: int) -> None:
        await self._request("POST", f"/tasks/{task_id}", json={"priority": priority})

    async def set_task_due(
        self,
        task_id: str,
        *,
        due_date: str | None = None,
        due_datetime: str | None = None,
        due_string: str | None = None,
    ) -> None:
        """Срок задачи. Структурные date/datetime приоритетнее строки (детерминированно,
        без переинтерпретации повторов). Всё пусто → снять срок ('no date')."""
        if due_datetime:
            payload = {"due_datetime": due_datetime}
        elif due_date:
            payload = {"due_date": due_date}
        elif due_string:
            payload = {"due_string": due_string}
        else:
            payload = {"due_string": "no date"}
        await self._request("POST", f"/tasks/{task_id}", json=payload)

    async def update_labels(self, task_id: str, labels: list[str]) -> None:
        await self._request("POST", f"/tasks/{task_id}", json={"labels": labels})

    async def move_task(self, task_id: str, project_id: str) -> None:
        """Перенос задачи в другой проект. v1: POST /tasks/{id}/move {project_id}."""
        await self._request("POST", f"/tasks/{task_id}/move",
                            json={"project_id": project_id}, expect_empty=True)

    async def close(self, task_id: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/close", expect_empty=True)

    async def reopen(self, task_id: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/reopen", expect_empty=True)

    async def delete(self, task_id: str) -> None:
        await self._request("DELETE", f"/tasks/{task_id}", expect_empty=True,
                            allow_404=True)

    # ---- низкоуровневый запрос ----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        expect_empty: bool = False,
        allow_404: bool = False,
    ):
        url = f"{_BASE}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, params=params, json=json,
                timeout=self._timeout,
            ) as resp:
                if allow_404 and resp.status == 404:
                    return None
                if resp.status >= 400:
                    text = await resp.text()
                    raise TodoistError(f"{method} {path} -> {resp.status}: {text[:300]}")
                if expect_empty or resp.status == 204:
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # таймаут aiohttp = asyncio.TimeoutError → тоже заворачиваем в TodoistError,
            # иначе он улетит мимо обработчиков task_sync и уронит весь sync-джоб.
            kind = "timeout" if isinstance(exc, asyncio.TimeoutError) else "network error"
            raise TodoistError(f"{method} {path} {kind}: {exc}") from exc
