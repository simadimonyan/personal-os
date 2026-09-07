"""Текстовые заметки-«мысли» (требование 7 + задача «Мысль … с тегами»).

Свободный текст пользователя →
  1) отдельный файл «Мысль YYYY-MM-DD <unixts>.md» в папке идей с системой тегов
     (авто-тег #мысль + любые #хэштеги из текста);
  2) короткая секция «📝 Заметка · HH:MM» в дневном файле состояний (чтобы заметка
     была видна и в дне);
  3) запись в checkins (slot=note) — для статуса «Сегодня».

Текст заметки НЕ логируется (приватность, §4.1).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date as date_cls
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.handlers._service import AppContext
from app.keyboards import menu
from app.keyboards.common import cancel_kb
from app.obsidian import note_body
from app.obsidian.paths import VaultPaths
from app.prompts import confirmations
from app.states.checkin_states import NoteStates

log = logging.getLogger("assistant.notes")
router = Router(name="notes")

# #хэштег: буквы/цифры/подчёркивание, латиница и кириллица
_HASHTAG_RE = re.compile(r"#([0-9A-Za-zА-Яа-яЁё_]+)")
_BASE_TAG = "мысль"
# символы, недопустимые в имени файла / в wiki-ссылке Obsidian ([[...]])
_BAD_FILENAME_RE = re.compile(r"[\\/\[\]#^|:*?\"<>]")
# вложения, которые заметка принимает как файлы (голос/аудио — отдельный voice-хендлер)
_MEDIA = F.photo | F.document | F.video | F.animation


def extract_tags(text: str) -> list[str]:
    """Авто-тег #мысль + распознанные #хэштеги из текста (без дублей, по порядку)."""
    tags: list[str] = [_BASE_TAG]
    for m in _HASHTAG_RE.findall(text):
        if m not in tags:
            tags.append(m)
    return tags


def _sanitize_filename(name: str) -> str:
    name = _BAD_FILENAME_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "файл"


async def _save_note(
    ctx: AppContext,
    *,
    text: str,
    tags: list[str],
    embeds: list[str],
    now: datetime,
    today: str,
) -> None:
    """Единая запись заметки: checkin(slot=note) + файл-«мысль» + секция дня.
    embeds — строки вида '![[имя файла]]' (вложения), идут перед текстом."""
    hhmm = now.strftime("%H:%M")
    filename = f"Мысль {today} {now.strftime('%H-%M-%S')}"
    body = "\n\n".join([*embeds, text]).strip() if text else "\n".join(embeds).strip()

    note_id = await ctx.repos.checkins.create(today, "note")
    await ctx.repos.checkins.update_answers(note_id, {"tags": tags, "text": text})
    await ctx.repos.checkins.finish(note_id, [])

    await ctx.repos.outbox.enqueue(
        note_id,
        {
            "date": today,
            "checkin_id": note_id,
            "thought_note": {
                "filename": filename,
                "frontmatter": {
                    "date": today,
                    "type": "thought",
                    "created": now.isoformat(timespec="seconds"),
                    "source": "telegram",
                    "tags": tags,
                },
                "body": body,
            },
        },
    )

    section = note_body.render_section("note", hhmm, {}, free_text=body)
    await ctx.repos.outbox.enqueue(
        note_id,
        {"date": today, "raw_section": section, "raw_patch": {}},
    )
    log.info("note queued for date=%s tags=%d embeds=%d", today, len(tags), len(embeds))


@router.message(F.text == menu.BTN_NOTE)
async def start_note(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Кинь мысль одним сообщением — сохраню отдельной заметкой.\n"
        "Можно добавить #теги прямо в текст.",
        reply_markup=cancel_kb(),
    )
    await state.set_state(NoteStates.waiting_text)


@router.callback_query(menu.MenuCB.filter(F.action == "note"))
async def cb_note(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await start_note(cb.message, state)


@router.message(NoteStates.waiting_text, F.text)
async def on_note_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    text = message.text.strip()
    now = datetime.now()
    await _save_note(ctx, text=text, tags=extract_tags(text), embeds=[],
                     now=now, today=date_cls.today().isoformat())
    await message.answer(confirmations.confirm_generic())
    await state.clear()


# ---- вложения: картинки, документы, видео → файл в Obsidian + заметка с эмбедом ----

async def _download_attachment(
    bot: Bot, message: Message, paths: VaultPaths
) -> tuple[str, str] | None:
    """Скачивает вложение в папку файлов Obsidian. Возвращает (имя_файла, тип)."""
    ts = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    if message.photo:
        file_id, raw_name, kind = message.photo[-1].file_id, f"Заметка {ts}.jpg", "картинку"
    elif message.document:
        doc = message.document
        file_id, raw_name, kind = doc.file_id, (doc.file_name or f"file {ts}"), "документ"
    elif message.video:
        v = message.video
        file_id, raw_name, kind = v.file_id, (v.file_name or f"Заметка {ts}.mp4"), "видео"
    elif message.animation:
        a = message.animation
        file_id, raw_name, kind = a.file_id, (a.file_name or f"Заметка {ts}.gif"), "гиф"
    else:
        return None

    target = paths.attachment_file(_sanitize_filename(raw_name))
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=str(target))
    log.info("attachment saved: %s", target.name)
    return target.name, kind


async def _media_note(
    messages: list[Message],
    state: FSMContext,
    bot: Bot,
    ctx: AppContext,
    settings: Settings,
) -> None:
    """Сохраняет одно или несколько вложений (альбом) в ОДНУ заметку.

    Скачивает все файлы последовательно (имена уникализируются через
    attachment_file '(N)'), собирает все эмбеды в один файл-«мысль», подпись берёт
    из того сообщения альбома, где она есть."""
    paths = VaultPaths(settings)
    await asyncio.to_thread(paths.ensure_dirs)

    embeds: list[str] = []
    kinds: list[str] = []
    caption = ""
    for msg in messages:
        if not caption and msg.caption:
            caption = msg.caption.strip()
        try:
            res = await _download_attachment(bot, msg, paths)
        except Exception:  # noqa: BLE001
            log.exception("attachment download failed")
            continue
        if res is None:
            continue
        fname, kind = res
        embeds.append(f"![[{fname}]]")
        kinds.append(kind)

    if not embeds:
        await messages[0].answer("Не получилось сохранить вложение. Попробуй ещё раз.")
        await state.clear()
        return

    now = datetime.now()
    tags = extract_tags(caption)
    if "вложение" not in tags:
        tags.append("вложение")
    await _save_note(ctx, text=caption, tags=tags, embeds=embeds,
                     now=now, today=date_cls.today().isoformat())

    if len(embeds) == 1:
        what = kinds[0]
    else:
        what = f"{len(embeds)} вложения" if len(embeds) < 5 else f"{len(embeds)} вложений"
    await messages[0].answer(
        f"Сохранил {what} в заметки и в Obsidian (09 — Шаблоны и ресурсы/Файлы)."
    )
    await state.clear()


@router.message(NoteStates.waiting_text, _MEDIA)
async def on_note_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    ctx: AppContext,
    settings: Settings,
    album: list[Message] | None = None,
) -> None:
    """Вложение(я), присланные в режиме заметки (после кнопки «📝 Заметка»)."""
    await _media_note(album or [message], state, bot, ctx, settings)


@router.message(StateFilter(None), _MEDIA)
async def on_idle_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    ctx: AppContext,
    settings: Settings,
    album: list[Message] | None = None,
) -> None:
    """Вложение(я), присланные просто так (вне сценариев) → заметка с файлами."""
    await _media_note(album or [message], state, bot, ctx, settings)
