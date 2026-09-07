"""3-сторонняя синхронизация задач: бот (SQLite-хаб) ↔ Obsidian ↔ Todoist.

Принципы:
- Бот (TaskRepo) — координатор/источник истины при конфликтах.
- Локальные неотправленные изменения (dirty_todoist) приоритетнее удалённого
  состояния, пока не будут запушены (иначе свежий тоггл «откатился» бы remote-ом).
- push() идемпотентен: перерисовывает Obsidian-файл целиком из БД и дотолкивает
  dirty-задачи в Todoist; сетевые ошибки оставляют задачу dirty до след. синка.
- sync() = pull_obsidian() → pull_todoist() → push().

Хендлеры зовут add_task/set_done/delete (мутация + немедленный push) для мгновенной
отзывчивости; фоновый job зовёт sync() периодически (подхватывает внешние правки).
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from app.integrations.todoist import TodoistClient, TodoistError
from app.obsidian.tasks_doc import TasksArchiveDoc, TasksDoc
from app.storage.repositories import TaskRepo, TaskRow

log = logging.getLogger("assistant.task_sync")


def new_uid() -> str:
    """Короткий стабильный id задачи (^uid в Obsidian)."""
    return secrets.token_hex(3)


class TaskSync:
    def __init__(
        self,
        tasks: TaskRepo,
        doc: TasksDoc,
        todoist: TodoistClient | None = None,
        archive: TasksArchiveDoc | None = None,
    ) -> None:
        self._tasks = tasks
        self._doc = doc
        self._todoist = todoist
        self._archive = archive
        self._lock = asyncio.Lock()

    # ---- операции от бота (мутация + немедленный push) ----

    async def add_task(self, content: str, source: str = "bot", done: bool = False) -> TaskRow:
        row = await self._tasks.add(new_uid(), content.strip(), source=source, done=done)
        await self.push()
        return row

    async def set_done(self, uid: str, done: bool) -> None:
        await self._tasks.set_done(uid, done)
        await self.push()

    async def delete(self, uid: str) -> None:
        await self._tasks.soft_delete(uid)
        await self.push()

    async def update_content(self, uid: str, content: str) -> None:
        await self._tasks.update_content(uid, content)
        await self.push()

    async def set_priority(self, uid: str, priority: int) -> None:
        await self._tasks.set_priority(uid, priority)
        await self.push()

    async def set_due(self, uid: str, due_date: str | None, due_datetime: str | None,
                      due_string: str | None) -> None:
        await self._tasks.set_due(uid, due_date, due_datetime, due_string)
        await self.push()

    async def clear_due(self, uid: str) -> None:
        """Снять срок — явное действие (generic push срок не обнуляет)."""
        await self._tasks.set_due(uid, None, None, None, dirty_todoist=False)
        async with self._lock:
            row = await self._tasks.get_by_uid(uid)
            if self._todoist is not None and row and row.todoist_id:
                try:
                    await self._todoist.set_task_due(row.todoist_id)  # пустой → снять
                except TodoistError as exc:
                    log.warning("todoist clear_due failed uid=%s: %s", uid, exc)
            await self._push_locked()

    async def set_labels(self, uid: str, labels: list[str]) -> None:
        """Выставить метки. Пустой список обрабатываем немедленно (generic push
        пустые метки не пушит, чтобы не стирать вслепую)."""
        if labels:
            await self._tasks.set_labels(uid, labels)
            await self.push()
            return
        await self._tasks.set_labels(uid, [], dirty_todoist=False)
        async with self._lock:
            row = await self._tasks.get_by_uid(uid)
            if self._todoist is not None and row and row.todoist_id:
                try:
                    await self._todoist.update_labels(row.todoist_id, [])
                except TodoistError as exc:
                    log.warning("todoist clear labels failed uid=%s: %s", uid, exc)
            await self._push_locked()

    async def change_project(self, uid: str, project_id: str) -> None:
        """Перенести задачу в другой проект (Todoist move — отдельным вызовом)."""
        await self._tasks.set_project(uid, project_id, dirty_todoist=False)
        async with self._lock:
            row = await self._tasks.get_by_uid(uid)
            if self._todoist is not None and row and row.todoist_id:
                try:
                    await self._todoist.move_task(row.todoist_id, project_id)
                except TodoistError as exc:
                    log.warning("todoist move failed uid=%s: %s", uid, exc)
            await self._push_locked()

    async def clear_done(self) -> int:
        """«Очистить выполненные»: убрать сделанные задачи из активного статуса
        (бот + Задачи.md), но записать их в историю Obsidian (Архив задач.md).
        Запись в БД сохраняется (archived=1). Возвращает число очищенных."""
        async with self._lock:
            done = await self._tasks.list_done()
            if not done:
                return 0
            if self._archive is not None:
                try:
                    await self._archive.append(done)
                except OSError as exc:
                    # не теряем историю: без успешной записи в архив не архивируем
                    log.warning("tasks archive write failed, abort clear: %s", exc)
                    return 0
            await self._tasks.mark_archived([t.uid for t in done])
            await self._push_locked()  # перерисовать активный файл уже без них
            return len(done)

    # ---- полный синк ----

    async def sync(self) -> None:
        async with self._lock:
            await self._pull_obsidian()
            await self._pull_todoist()
            await self._push_locked()

    async def push(self) -> None:
        async with self._lock:
            await self._push_locked()

    # ---- внутреннее (под self._lock) ----

    async def _push_locked(self) -> None:
        # 1) Obsidian — перерисовать файл из БД (canonical), снять dirty_obsidian
        if await self._tasks.any_dirty_obsidian():
            rows = await self._tasks.list_for_obsidian()
            try:
                await self._doc.write(rows)
                await self._tasks.clear_dirty_obsidian_all()
            except OSError as exc:  # файл занят синхронизацией — попробуем позже
                log.warning("tasks obsidian write failed, will retry: %s", exc)

        # 2) Todoist — дотолкнуть dirty-задачи
        if self._todoist is None:
            return
        for row in await self._tasks.pending_todoist():
            try:
                await self._push_one_todoist(row)
                await self._tasks.clear_dirty_todoist(row.uid)
            except TodoistError as exc:
                log.warning("todoist push failed for uid=%s, will retry: %s", row.uid, exc)

    async def _push_one_todoist(self, row: TaskRow) -> None:
        assert self._todoist is not None
        t = self._todoist
        if row.deleted:
            if row.todoist_id:
                await t.delete(row.todoist_id)
            return
        if not row.todoist_id:
            tid = await t.create_task(
                row.content,
                project_id=row.project_id,
                priority=row.priority,
                due_string=row.due_string,
                labels=row.labels,
            )
            await self._tasks.set_todoist_id(row.uid, tid)
            if row.due_date or row.due_datetime:
                await t.set_task_due(tid, due_date=row.due_date, due_datetime=row.due_datetime)
            if row.done:
                await t.close(tid)
            return
        # существует — синхронизируем текст, приоритет, состояние.
        # Срок/метки только ВЫСТАВЛЯЕМ (если заданы), но не обнуляем «вслепую» — чтобы
        # тоггл done из бота не стёр срок, выставленный прямо в Todoist. Снятие срока/
        # меток — отдельными явными действиями (clear_due / set_labels=[] немедленно).
        await t.update_content(row.todoist_id, row.content)
        await t.update_priority(row.todoist_id, row.priority)
        if row.due_datetime or row.due_date or row.due_string:
            await t.set_task_due(row.todoist_id, due_date=row.due_date,
                                 due_datetime=row.due_datetime, due_string=row.due_string)
        if row.labels:
            await t.update_labels(row.todoist_id, row.labels)
        if row.done:
            await t.close(row.todoist_id)
        else:
            await t.reopen(row.todoist_id)

    async def _pull_obsidian(self) -> None:
        parsed = await self._doc.read_parsed()
        if parsed is None:
            return  # файла ещё нет — нечего подтягивать
        seen: set[str] = set()
        for p in parsed:
            if p.uid is None:
                # новый пункт, добавленный руками в Obsidian → завести задачу
                await self._tasks.add(new_uid(), p.content, source="obsidian", done=p.done)
                continue
            seen.add(p.uid)
            row = await self._tasks.get_by_uid(p.uid)
            if row is None or row.deleted:
                continue  # неизвестный/удалённый uid — не воскрешаем
            if p.done != row.done:
                await self._tasks.set_done(p.uid, p.done)
            if p.content != row.content:
                await self._tasks.update_content(p.uid, p.content)
        # удаление в Obsidian: задача была отрендерена (есть uid), но исчезла из файла
        for row in await self._tasks.list_for_obsidian():
            if row.uid not in seen:
                await self._tasks.soft_delete(row.uid)

    async def _pull_todoist(self) -> None:
        if self._todoist is None:
            return
        try:
            remote = await self._todoist.list_tasks()
        except TodoistError as exc:
            log.warning("todoist pull failed, skip: %s", exc)
            return
        # обновить кэш проектов (имена + inbox) — best-effort
        try:
            projects = await self._todoist.list_projects()
            await self._tasks.upsert_projects([(p.id, p.name, p.is_inbox) for p in projects])
        except TodoistError as exc:
            log.warning("todoist projects pull failed, skip: %s", exc)

        remote_by_id = {t.id: t for t in remote}
        local = await self._tasks.list_with_todoist_id()
        local_ids = {row.todoist_id for row in local}

        # новые задачи, созданные в Todoist
        for t in remote:
            if t.id not in local_ids:
                await self._tasks.add(
                    new_uid(), t.content, source="todoist", done=False,
                    todoist_id=t.id, dirty_obsidian=True, dirty_todoist=False,
                    priority=t.priority, due_date=t.due_date, due_datetime=t.due_datetime,
                    due_string=t.due_string, project_id=t.project_id,
                    section_id=t.section_id, labels=t.labels,
                )

        # изменения существующих (адоптируем remote только если нет pending локальных правок)
        for row in local:
            if row.dirty_todoist:
                continue  # локальное изменение ещё не запушено — оно приоритетнее
            rt = remote_by_id.get(row.todoist_id)
            if rt is None:
                # исчезла из активного списка Todoist → закрыта/удалена там → done
                if not row.done:
                    await self._tasks.set_done(row.uid, True, dirty_todoist=False)
                continue
            if row.done:  # снова активна в Todoist → переоткрыть локально
                await self._tasks.set_done(row.uid, False, dirty_todoist=False)
            if rt.content != row.content:
                await self._tasks.update_content(row.uid, rt.content, dirty_todoist=False)
            if rt.priority != row.priority:
                await self._tasks.set_priority(row.uid, rt.priority, dirty_todoist=False)
            if (rt.due_date, rt.due_datetime, rt.due_string) != \
                    (row.due_date, row.due_datetime, row.due_string):
                await self._tasks.set_due(row.uid, rt.due_date, rt.due_datetime,
                                          rt.due_string, dirty_todoist=False)
            if rt.project_id != row.project_id or rt.section_id != row.section_id:
                await self._tasks.set_project(row.uid, rt.project_id, rt.section_id,
                                              dirty_todoist=False)
            if rt.labels != row.labels:
                await self._tasks.set_labels(row.uid, rt.labels, dirty_todoist=False)
