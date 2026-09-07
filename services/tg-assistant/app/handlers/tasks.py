"""Таск-менеджер в Telegram (кнопка ✅ Задачи) — красивый текст + номерные кнопки.

Задачи выводятся ТЕКСТОМ со сквозной нумерацией и группировкой (как в Todoist:
по времени / проектам / приоритету / меткам). Под текстом — номерные кнопки
[1][2][3]…: тап по номеру открывает меню действий конкретной задачи (выполнить,
текст, срок, приоритет, проект, метки, удалить). Всё двусторонне синхронизируется
с Obsidian и Todoist (TaskSync).

При открытии списка дёргаем фоновый sync, чтобы свежие правки из Todoist
подтягивались сразу, а не через интервал.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain.task_sync import TaskSync
from app.keyboards import menu
from app.keyboards.common import cancel_kb
from app.render import tasks_view
from app.states.checkin_states import TaskStates
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.tasks")
router = Router(name="tasks")

_VIEWS = {
    "time": "📋 Все", "today": "📅 Сегодня", "upcoming": "📆 Предстоящее",
    "projects": "🗂 Проекты", "priority": "🔴 Приоритет", "labels": "🏷 Метки",
}
_PRIO = {"4": ("🔴", "p1"), "3": ("🟠", "p2"), "2": ("🔵", "p3"), "1": ("⚪", "p4")}


# ============================ рендер списка ============================

def _chunk(buttons: list[InlineKeyboardButton], per: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + per] for i in range(0, len(buttons), per)]


async def _render_list(repos: Repositories, view: str) -> tuple[str, InlineKeyboardMarkup]:
    rows = await repos.tasks.list_for_obsidian()       # активные + выполненные (не архив)
    active = [r for r in rows if not r.done]
    done_n = len([r for r in rows if r.done])
    names = await repos.tasks.project_names()
    text, order = tasks_view.render(view, active, names)

    kb: list[list[InlineKeyboardButton]] = []
    # номерные кнопки → меню задачи
    num_btns = [InlineKeyboardButton(text=str(i + 1), callback_data=f"to:{uid}")
                for i, uid in enumerate(order)]
    kb += _chunk(num_btns, 8)
    # переключатели вида (фильтры)
    kb.append([InlineKeyboardButton(text=("• " if view == v else "") + _VIEWS[v],
                                    callback_data=f"tv:{v}")
               for v in ("time", "today", "upcoming")])
    kb.append([InlineKeyboardButton(text=("• " if view == v else "") + _VIEWS[v],
                                    callback_data=f"tv:{v}")
               for v in ("projects", "priority", "labels")])
    bottom = [InlineKeyboardButton(text="➕ Добавить", callback_data="tadd")]
    if done_n:
        bottom.append(InlineKeyboardButton(text=f"🧹 Очистить ({done_n})",
                                           callback_data="tclear"))
    kb.append(bottom)
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _show(target: Message | CallbackQuery, repos: Repositories, view: str,
                *, edit: bool) -> None:
    text, kb = await _render_list(repos, view)
    msg = target.message if isinstance(target, CallbackQuery) else target
    if edit:
        try:
            await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            pass
    else:
        await msg.answer(text, reply_markup=kb, disable_web_page_preview=True)


async def _bg_sync_refresh(message: Message, repos: Repositories,
                           task_sync: TaskSync, view: str) -> None:
    """Фоновый синк после открытия списка → перерисовать свежими данными."""
    try:
        await task_sync.sync()
        text, kb = await _render_list(repos, view)
        await message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as exc:  # noqa: BLE001 — фон, не мешаем пользователю
        log.debug("bg sync refresh skipped: %s", exc)


# ============================ открытие списка ============================

async def _open_list(message: Message, state: FSMContext, repos: Repositories,
                     task_sync: TaskSync) -> None:
    await state.clear()
    await state.update_data(task_view="time")
    text, kb = await _render_list(repos, "time")
    sent = await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
    asyncio.create_task(_bg_sync_refresh(sent, repos, task_sync, "time"))


@router.message(F.text == menu.BTN_TASKS)
async def show_tasks(message: Message, state: FSMContext, repos: Repositories,
                     task_sync: TaskSync) -> None:
    await _open_list(message, state, repos, task_sync)


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, state: FSMContext, repos: Repositories,
                    task_sync: TaskSync) -> None:
    await _open_list(message, state, repos, task_sync)


@router.callback_query(F.data.startswith("tv:"))
async def switch_view(cb: CallbackQuery, state: FSMContext, repos: Repositories) -> None:
    view = cb.data.split(":", 1)[1]
    await state.update_data(task_view=view)
    await _show(cb, repos, view, edit=True)
    await cb.answer()


# ============================ меню задачи ============================

def _task_menu_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнить", callback_data=f"td:{uid}"),
         InlineKeyboardButton(text="✏️ Текст", callback_data=f"tedit:{uid}")],
        [InlineKeyboardButton(text="⏰ Срок", callback_data=f"tdue:{uid}"),
         InlineKeyboardButton(text="🔴 Приоритет", callback_data=f"tprio:{uid}")],
        [InlineKeyboardButton(text="🗂 Проект", callback_data=f"tproj:{uid}"),
         InlineKeyboardButton(text="🏷 Метки", callback_data=f"tlbl:{uid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"tdel:{uid}"),
         InlineKeyboardButton(text="← Назад", callback_data="tback")],
    ])


async def _task_caption(repos: Repositories, uid: str) -> str | None:
    row = await repos.tasks.get_by_uid(uid)
    if row is None or row.deleted or row.archived:
        return None
    names = await repos.tasks.project_names()
    parts = [f"{tasks_view.prio_emoji(row.priority)} {row.content}"]
    meta = []
    if row.due_date:
        meta.append(f"⏰ {row.due_string or row.due_date}")
    proj = names.get(row.project_id or "", "Inbox") if row.project_id else "Inbox"
    meta.append(f"🗂 {proj}")
    if row.labels:
        meta.append("🏷 " + ", ".join(row.labels))
    parts.append(" · ".join(meta))
    return "\n".join(parts)


@router.callback_query(F.data.startswith("to:"))
async def open_task(cb: CallbackQuery, repos: Repositories) -> None:
    uid = cb.data.split(":", 1)[1]
    cap = await _task_caption(repos, uid)
    if cap is None:
        await cb.answer("Задача недоступна")
        return
    try:
        await cb.message.edit_text(cap, reply_markup=_task_menu_kb(uid),
                                   disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


async def _back_to_list(cb: CallbackQuery, state: FSMContext, repos: Repositories) -> None:
    view = (await state.get_data()).get("task_view", "time")
    await _show(cb, repos, view, edit=True)


@router.callback_query(F.data == "tback")
async def task_back(cb: CallbackQuery, state: FSMContext, repos: Repositories) -> None:
    await _back_to_list(cb, state, repos)
    await cb.answer()


# ============================ действия ============================

@router.callback_query(F.data.startswith("td:"))
async def task_done(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                    task_sync: TaskSync) -> None:
    uid = cb.data.split(":", 1)[1]
    await task_sync.set_done(uid, True)
    await cb.answer("Выполнено ✅")
    await _back_to_list(cb, state, repos)


@router.callback_query(F.data.startswith("tdel:"))
async def task_delete(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                      task_sync: TaskSync) -> None:
    uid = cb.data.split(":", 1)[1]
    await task_sync.delete(uid)
    await cb.answer("Удалено 🗑")
    await _back_to_list(cb, state, repos)


# ---- приоритет ----

@router.callback_query(F.data.startswith("tprio:"))
async def task_prio_menu(cb: CallbackQuery, repos: Repositories) -> None:
    uid = cb.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e} {n}", callback_data=f"tprioset:{uid}:{p}")
         for p, (e, n) in _PRIO.items()],
        [InlineKeyboardButton(text="← Назад", callback_data=f"to:{uid}")],
    ])
    try:
        await cb.message.edit_text("Приоритет:", reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("tprioset:"))
async def task_prio_set(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                        task_sync: TaskSync) -> None:
    _, uid, p = cb.data.split(":")
    await task_sync.set_priority(uid, int(p))
    await cb.answer("Приоритет обновлён")
    await _back_to_list(cb, state, repos)


# ---- срок ----

def _weekend() -> date:
    today = date.today()
    return today + timedelta(days=(5 - today.weekday()) % 7)


@router.callback_query(F.data.startswith("tdue:"))
async def task_due_menu(cb: CallbackQuery, repos: Repositories) -> None:
    uid = cb.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data=f"tdueset:{uid}:today"),
         InlineKeyboardButton(text="Завтра", callback_data=f"tdueset:{uid}:tomorrow")],
        [InlineKeyboardButton(text="+3 дня", callback_data=f"tdueset:{uid}:plus3"),
         InlineKeyboardButton(text="Выходные", callback_data=f"tdueset:{uid}:weekend")],
        [InlineKeyboardButton(text="✏️ Ввести дату/время", callback_data=f"tdueinput:{uid}")],
        [InlineKeyboardButton(text="🚫 Без срока", callback_data=f"tdueset:{uid}:clear"),
         InlineKeyboardButton(text="← Назад", callback_data=f"to:{uid}")],
    ])
    try:
        await cb.message.edit_text("Срок:", reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("tdueset:"))
async def task_due_set(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                       task_sync: TaskSync) -> None:
    _, uid, kind = cb.data.split(":")
    presets = {
        "today": (date.today(), "сегодня"),
        "tomorrow": (date.today() + timedelta(days=1), "завтра"),
        "plus3": (date.today() + timedelta(days=3), "через 3 дня"),
        "weekend": (_weekend(), "на выходные"),
    }
    if kind == "clear":
        await task_sync.clear_due(uid)
        await cb.answer("Срок снят")
    else:
        d, human = presets[kind]
        await task_sync.set_due(uid, d.isoformat(), None, human)
        await cb.answer("Срок: " + human)
    await _back_to_list(cb, state, repos)


@router.callback_query(F.data.startswith("tdueinput:"))
async def task_due_input(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.data.split(":", 1)[1]
    await state.update_data(task_uid=uid)
    await state.set_state(TaskStates.due_input)
    await cb.message.answer(
        "Напиши срок: дату ГГГГ-ММ-ДД или словами («завтра 18:00», «в пятницу»).",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(TaskStates.due_input, F.text)
async def task_due_input_text(message: Message, state: FSMContext, repos: Repositories,
                              task_sync: TaskSync) -> None:
    data = await state.get_data()
    uid = data.get("task_uid")
    s = (message.text or "").strip()
    await state.set_state(None)
    if uid and s:
        # пробуем как ISO-дату; иначе — как естественную строку (Todoist распарсит)
        try:
            d = date.fromisoformat(s)
            await task_sync.set_due(uid, d.isoformat(), None, s)
        except ValueError:
            await task_sync.set_due(uid, None, None, s)
        await message.answer("Срок обновлён.")
    await _show(message, repos, data.get("task_view", "time"), edit=False)


# ---- проект ----

@router.callback_query(F.data.startswith("tproj:"))
async def task_proj_menu(cb: CallbackQuery, repos: Repositories) -> None:
    uid = cb.data.split(":", 1)[1]
    names = await repos.tasks.project_names()
    btns = [InlineKeyboardButton(text=name, callback_data=f"tprojset:{uid}:{pid}")
            for pid, name in sorted(names.items(), key=lambda x: x[1])]
    rows = _chunk(btns, 2)
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"to:{uid}")])
    try:
        await cb.message.edit_text("В какой проект:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("tprojset:"))
async def task_proj_set(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                        task_sync: TaskSync) -> None:
    _, uid, pid = cb.data.split(":")
    await task_sync.change_project(uid, pid)
    await cb.answer("Перенесено")
    await _back_to_list(cb, state, repos)


# ---- метки ----

@router.callback_query(F.data.startswith("tlbl:"))
async def task_labels_input(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.data.split(":", 1)[1]
    await state.update_data(task_uid=uid)
    await state.set_state(TaskStates.labels_input)
    await cb.message.answer(
        "Метки через запятую (например: дом, срочно). Пусто — снять все метки.",
        reply_markup=cancel_kb(),
    )
    await cb.answer()


@router.message(TaskStates.labels_input, F.text)
async def task_labels_text(message: Message, state: FSMContext, repos: Repositories,
                           task_sync: TaskSync) -> None:
    data = await state.get_data()
    uid = data.get("task_uid")
    labels = [s.strip() for s in (message.text or "").split(",") if s.strip()]
    await state.set_state(None)
    if uid:
        await task_sync.set_labels(uid, labels)
        await message.answer("Метки обновлены." if labels else "Метки сняты.")
    await _show(message, repos, data.get("task_view", "time"), edit=False)


# ---- правка текста ----

@router.callback_query(F.data.startswith("tedit:"))
async def task_edit_text(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.data.split(":", 1)[1]
    await state.update_data(task_uid=uid)
    await state.set_state(TaskStates.editing_text)
    await cb.message.answer("Новый текст задачи одним сообщением.", reply_markup=cancel_kb())
    await cb.answer()


@router.message(TaskStates.editing_text, F.text)
async def task_edit_text_save(message: Message, state: FSMContext, repos: Repositories,
                              task_sync: TaskSync) -> None:
    data = await state.get_data()
    uid = data.get("task_uid")
    content = (message.text or "").strip()
    await state.set_state(None)
    if uid and content:
        await task_sync.update_content(uid, content)
        await message.answer("Текст обновлён.")
    await _show(message, repos, data.get("task_view", "time"), edit=False)


# ---- добавление ----

@router.callback_query(F.data == "tadd")
async def add_task_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TaskStates.waiting_text)
    await cb.message.answer("Напиши текст задачи одним сообщением.", reply_markup=cancel_kb())
    await cb.answer()


@router.message(TaskStates.waiting_text, F.text)
async def add_task_text(message: Message, state: FSMContext, repos: Repositories,
                        task_sync: TaskSync) -> None:
    content = (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    if not content:
        await message.answer("Пустая задача — пропускаю.")
        return
    await task_sync.add_task(content, source="bot")
    await _show(message, repos, data.get("task_view", "time"), edit=False)


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext, repos: Repositories,
                  task_sync: TaskSync) -> None:
    content = (message.text or "").partition(" ")[2].strip()
    if not content:
        await message.answer("Формат: /add текст задачи")
        return
    await task_sync.add_task(content, source="bot")
    await _show(message, repos, "time", edit=False)


# ---- очистка выполненных (архив с историей) ----

@router.callback_query(F.data == "tclear")
async def clear_done_tasks(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                           task_sync: TaskSync) -> None:
    n = await task_sync.clear_done()
    await cb.answer(f"Очищено: {n}. История — в Obsidian." if n else "Нет выполненных")
    await _back_to_list(cb, state, repos)


@router.callback_query(menu.MenuCB.filter(F.action == "tasks"))
async def cb_tasks(cb: CallbackQuery, state: FSMContext, repos: Repositories,
                   task_sync: TaskSync) -> None:
    await cb.answer()
    await show_tasks(cb.message, state, repos, task_sync)
