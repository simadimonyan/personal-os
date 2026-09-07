"""Интеграция 3-сторонней синхронизации задач: бот ↔ Obsidian ↔ (fake) Todoist."""

from __future__ import annotations

import pytest

from app.domain.task_sync import TaskSync
from app.integrations.todoist import TodoistProject, TodoistTask
from app.obsidian.tasks_doc import TasksArchiveDoc, TasksDoc, parse_tasks
from app.storage.db import Database
from app.storage.repositories import Repositories


class FakeTodoist:
    """Имитация Todoist v1: list_tasks отдаёт только активные; хранит атрибуты."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.projects: list[TodoistProject] = [
            TodoistProject(id="inbox", name="Inbox", is_inbox=True),
            TodoistProject(id="work", name="Работа", is_inbox=False),
        ]
        self._counter = 0

    async def list_tasks(self) -> list[TodoistTask]:
        return [
            TodoistTask(
                id=tid, content=t["content"], is_completed=False,
                project_id=t.get("project_id"), priority=t.get("priority", 1),
                due_date=t.get("due_date"), due_datetime=t.get("due_datetime"),
                due_string=t.get("due_string"), labels=list(t.get("labels", [])),
            )
            for tid, t in self.store.items() if t["active"]
        ]

    async def list_projects(self) -> list[TodoistProject]:
        return list(self.projects)

    async def create_task(self, content: str, *, project_id=None, priority=None,
                          due_string=None, labels=None) -> str:
        self._counter += 1
        tid = f"td{self._counter}"
        self.store[tid] = {
            "content": content, "active": True, "project_id": project_id,
            "priority": priority or 1, "due_string": due_string,
            "due_date": None, "due_datetime": None, "labels": list(labels or []),
        }
        return tid

    async def update_content(self, task_id: str, content: str) -> None:
        self.store[task_id]["content"] = content

    async def update_priority(self, task_id: str, priority: int) -> None:
        self.store[task_id]["priority"] = priority

    async def set_task_due(self, task_id: str, *, due_date=None, due_datetime=None,
                           due_string=None) -> None:
        s = self.store[task_id]
        s["due_date"], s["due_datetime"], s["due_string"] = due_date, due_datetime, due_string

    async def update_labels(self, task_id: str, labels: list[str]) -> None:
        self.store[task_id]["labels"] = list(labels)

    async def move_task(self, task_id: str, project_id: str) -> None:
        self.store[task_id]["project_id"] = project_id

    async def close(self, task_id: str) -> None:
        self.store[task_id]["active"] = False

    async def reopen(self, task_id: str) -> None:
        self.store[task_id]["active"] = True

    async def delete(self, task_id: str) -> None:
        self.store.pop(task_id, None)


@pytest.fixture
async def setup(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    repos = Repositories.build(db)
    doc = TasksDoc(tmp_path / "Задачи.md")
    archive = TasksArchiveDoc(tmp_path / "Архив задач.md")
    fake = FakeTodoist()
    sync = TaskSync(repos.tasks, doc, fake, archive=archive)
    yield repos, doc, fake, sync, tmp_path
    await db.close()


async def test_add_pushes_to_obsidian_and_todoist(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Купить молоко")

    # Obsidian
    text = (tmp_path / "Задачи.md").read_text(encoding="utf-8")
    assert "Купить молоко" in text and f"^{row.uid}" in text
    # Todoist
    assert len(fake.store) == 1
    # бот записал todoist_id
    updated = await repos.tasks.get_by_uid(row.uid)
    assert updated.todoist_id is not None


async def test_toggle_done_closes_in_todoist(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Позвонить врачу")
    assert len(await fake.list_tasks()) == 1

    await sync.set_done(row.uid, True)
    assert len(await fake.list_tasks()) == 0  # закрыта в Todoist
    text = (tmp_path / "Задачи.md").read_text(encoding="utf-8")
    assert f"- [x] Позвонить врачу ^{row.uid}" in text


async def test_pull_new_task_from_todoist(setup):
    repos, doc, fake, sync, tmp_path = setup
    # задача создана напрямую в Todoist (внешняя)
    await fake.create_task("Из Todoist")
    await sync.sync()

    active = await repos.tasks.list_active()
    contents = {t.content for t in active}
    assert "Из Todoist" in contents
    # и появилась в Obsidian
    text = (tmp_path / "Задачи.md").read_text(encoding="utf-8")
    assert "Из Todoist" in text


async def test_pull_obsidian_manual_toggle(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Помыть посуду")

    # пользователь руками отметил [x] в Obsidian
    path = tmp_path / "Задачи.md"
    text = path.read_text(encoding="utf-8").replace(
        f"- [ ] Помыть посуду ^{row.uid}", f"- [x] Помыть посуду ^{row.uid}"
    )
    path.write_text(text, encoding="utf-8")

    await sync.sync()
    updated = await repos.tasks.get_by_uid(row.uid)
    assert updated.done is True
    assert len(await fake.list_tasks()) == 0  # тоггл проброшен в Todoist


async def test_delete_removes_everywhere(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Временная")
    await sync.delete(row.uid)

    assert len(fake.store) == 0  # удалена в Todoist
    text = (tmp_path / "Задачи.md").read_text(encoding="utf-8")
    parsed = [p for p in parse_tasks(text) if p.uid == row.uid]
    assert parsed == []  # исчезла из Obsidian


async def test_clear_done_archives_to_obsidian_keeps_history(setup):
    repos, doc, fake, sync, tmp_path = setup
    active_row = await sync.add_task("Активная остаётся")
    done_row = await sync.add_task("Сделанная уходит в архив")
    await sync.set_done(done_row.uid, True)

    n = await sync.clear_done()
    assert n == 1

    # активный файл: выполненной больше нет, активная на месте
    active_text = (tmp_path / "Задачи.md").read_text(encoding="utf-8")
    assert f"^{done_row.uid}" not in active_text
    assert f"^{active_row.uid}" in active_text

    # история сохранена в Архив задач.md
    archive_text = (tmp_path / "Архив задач.md").read_text(encoding="utf-8")
    assert "Сделанная уходит в архив" in archive_text
    assert f"^{done_row.uid}" in archive_text

    # запись в БД не удалена — помечена archived, исключена из активных выборок
    row = await repos.tasks.get_by_uid(done_row.uid)
    assert row is not None and row.archived is True and row.deleted is False
    assert done_row.uid not in {t.uid for t in await repos.tasks.list_for_obsidian()}
    assert await repos.tasks.list_done() == []

    # повторная очистка — нечего чистить
    assert await sync.clear_done() == 0


async def test_set_priority_and_due_push_to_todoist(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Важная")
    tid = (await repos.tasks.get_by_uid(row.uid)).todoist_id

    await sync.set_priority(row.uid, 4)
    await sync.set_due(row.uid, "2026-07-01", None, "1 июля")

    assert fake.store[tid]["priority"] == 4
    assert fake.store[tid]["due_date"] == "2026-07-01"
    db = await repos.tasks.get_by_uid(row.uid)
    assert db.priority == 4 and db.due_date == "2026-07-01"


async def test_change_project_moves_in_todoist(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Перенести")
    tid = (await repos.tasks.get_by_uid(row.uid)).todoist_id

    await sync.change_project(row.uid, "work")
    assert fake.store[tid]["project_id"] == "work"
    assert (await repos.tasks.get_by_uid(row.uid)).project_id == "work"


async def test_pull_adopts_priority_due_project_labels(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Изменят в Todoist")
    tid = (await repos.tasks.get_by_uid(row.uid)).todoist_id

    # правки приходят со стороны Todoist
    fake.store[tid].update(priority=3, due_date="2026-08-09", labels=["дом"], project_id="work")
    await sync.sync()

    db = await repos.tasks.get_by_uid(row.uid)
    assert db.priority == 3 and db.due_date == "2026-08-09"
    assert db.labels == ["дом"] and db.project_id == "work"
    # имена проектов закэшировались
    assert "Работа" in (await repos.tasks.project_names()).values()


async def test_complete_in_todoist_marks_done_locally(setup):
    repos, doc, fake, sync, tmp_path = setup
    row = await sync.add_task("Закроется в Todoist")
    tid = (await repos.tasks.get_by_uid(row.uid)).todoist_id

    # «завершили» в Todoist → пропала из активного списка
    await fake.close(tid)
    await sync.sync()

    updated = await repos.tasks.get_by_uid(row.uid)
    assert updated.done is True
