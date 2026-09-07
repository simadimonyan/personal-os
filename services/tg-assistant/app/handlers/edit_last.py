"""Редактирование последней записи + отмена правки (требование 7).

Находит последнюю секцию (## ...) в дневном файле текущей даты, показывает
её текст, принимает новый текст и заменяет секцию (read-modify-write, та же
атомарная запись через writer.replace_last_section).

Перед заменой прежняя секция кладётся в section_backups → правку можно откатить
одной кнопкой «↩️ Отменить правку» (или командой /undo).

Замена выполняется напрямую через writer (не через outbox), т.к. это
интерактивная правка — пользователь ждёт результат. При ошибке сообщаем честно.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Settings
from app.keyboards import menu
from app.keyboards.common import cancel_kb
from app.obsidian import note_body
from app.obsidian.paths import VaultPaths
from app.obsidian.writer import ObsidianWriter
from app.states.checkin_states import EditStates
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.edit")
router = Router(name="edit_last")


def _undo_kb(date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Отменить правку", callback_data=f"undo_edit:{date}")
    ]])


@router.message(F.text == menu.BTN_EDIT_LAST)
async def start_edit(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    today = date_cls.today().isoformat()
    paths = VaultPaths(settings)
    path = paths.diary_file(today)

    current = await asyncio.to_thread(_read_last_section, path)
    if current is None:
        await message.answer("За сегодня ещё нет записей, которые можно поправить.")
        return

    await state.update_data(edit_date=today)
    preview = current if len(current) < 1500 else current[:1500] + "…"
    await message.answer(
        "Последняя запись:\n\n" + preview + "\n\nПришли новый текст — заменю эту секцию целиком.",
        reply_markup=cancel_kb(),
    )
    await state.set_state(EditStates.waiting_new_text)


@router.callback_query(menu.MenuCB.filter(F.action == "edit"))
async def cb_edit(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await cb.answer()
    await start_edit(cb.message, state, settings)


@router.message(EditStates.waiting_new_text, F.text)
async def on_new_text(
    message: Message, state: FSMContext, settings: Settings, repos: Repositories
) -> None:
    data = await state.get_data()
    today = data.get("edit_date", date_cls.today().isoformat())
    new_text = message.text.strip()

    paths = VaultPaths(settings)
    writer = ObsidianWriter(paths)

    # бэкап прежней секции ДО затирания — чтобы можно было откатить
    prev_section = await asyncio.to_thread(_read_last_section, paths.diary_file(today))
    if prev_section is not None:
        await repos.edit_backups.save(today, prev_section)

    # сохраняем заголовок секции, меняем только тело: новая секция = заголовок + текст
    header = await asyncio.to_thread(_read_last_header, paths.diary_file(today))
    if header is None:
        header = note_body.section_header("note", datetime.now().strftime("%H:%M"))
    new_section = header + "\n" + new_text

    try:
        ok = await writer.replace_last_section(today, new_section)
    except Exception:  # noqa: BLE001
        log.exception("edit_last write failed")
        await message.answer("Не получилось записать правку. Файл мог быть занят синхронизацией — попробуй ещё раз.")
        await state.clear()
        return

    if ok:
        await message.answer("Поправил.", reply_markup=_undo_kb(today))
    else:
        await message.answer("Не нашёл секцию для замены.")
    await state.clear()


@router.callback_query(F.data.startswith("undo_edit:"))
async def on_undo(cb: CallbackQuery, settings: Settings, repos: Repositories) -> None:
    date = cb.data.split(":", 1)[1]
    backup = await repos.edit_backups.pop_last(date)
    if backup is None:
        await cb.answer("Нечего отменять — бэкапа нет.", show_alert=False)
        return
    writer = ObsidianWriter(VaultPaths(settings))
    try:
        ok = await writer.replace_last_section(date, backup)
    except Exception:  # noqa: BLE001
        log.exception("undo edit write failed")
        await cb.answer("Не вышло откатить — файл занят. Попробуй ещё раз.", show_alert=True)
        return
    if ok:
        try:
            await cb.message.edit_text("↩️ Правка отменена — вернул прежний текст.")
        except Exception:  # noqa: BLE001
            pass
        await cb.answer("Отменено")
    else:
        await cb.answer("Не нашёл секцию для отката.", show_alert=True)


@router.message(Command("undo"))
async def cmd_undo(message: Message, settings: Settings, repos: Repositories) -> None:
    today = date_cls.today().isoformat()
    backup = await repos.edit_backups.pop_last(today)
    if backup is None:
        await message.answer("Нечего отменять — сегодня правок не было.")
        return
    writer = ObsidianWriter(VaultPaths(settings))
    try:
        ok = await writer.replace_last_section(today, backup)
    except Exception:  # noqa: BLE001
        log.exception("undo edit (cmd) write failed")
        await message.answer("Не вышло откатить — файл занят. Попробуй ещё раз.")
        return
    await message.answer("↩️ Правка отменена." if ok else "Не нашёл секцию для отката.")


# ---- блокирующие helper-ы (через to_thread) ----

def _read_sections(path) -> list[str] | None:  # noqa: ANN001
    if not path.exists():
        return None
    from app.obsidian import frontmatter as fm

    text = path.read_text(encoding="utf-8")
    _, body = fm.split_document(text)
    lines = body.splitlines()
    # индексы заголовков '## '
    idxs = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not idxs:
        return None
    sections = []
    for k, start in enumerate(idxs):
        end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
        sections.append("\n".join(lines[start:end]).rstrip())
    return sections


def _read_last_section(path) -> str | None:  # noqa: ANN001
    sections = _read_sections(path)
    return sections[-1] if sections else None


def _read_last_header(path) -> str | None:  # noqa: ANN001
    last = _read_last_section(path)
    if last is None:
        return None
    return last.splitlines()[0]
