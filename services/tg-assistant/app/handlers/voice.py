"""Голосовые: сохранить аудиофайл + плейсхолдер (БЕЗ транскрипции, §0.1, ADR-9).

Бот скачивает .ogg в vault/attachments и дописывает в дневник дня секцию
с плейсхолдером `![[voice_HHMMSS.ogg]]` и пометкой «(голосовое, не расшифровано)».
Данные не теряются; расшифровать можно позже (точка расширения transcriber.py).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import Message

from app.config import Settings
from app.handlers._service import AppContext
from app.obsidian import note_body
from app.obsidian.paths import VaultPaths
from app.transcription.transcriber import Transcriber

log = logging.getLogger("assistant.voice")
router = Router(name="voice")


@router.message(F.voice | F.audio)
async def on_voice(
    message: Message,
    bot: Bot,
    ctx: AppContext,
    settings: Settings,
    transcriber: Transcriber,
) -> None:
    voice = message.voice or message.audio
    if voice is None:
        return

    hhmmss = datetime.now().strftime("%H%M%S")
    today = date_cls.today().isoformat()
    paths = VaultPaths(settings)
    target = paths.voice_file(hhmmss, ext="ogg")

    # убедиться, что папка attachments есть
    import asyncio

    await asyncio.to_thread(paths.ensure_dirs)

    try:
        file = await bot.get_file(voice.file_id)
        await bot.download_file(file.file_path, destination=str(target))
        log.info("voice saved: %s", target.name)
    except Exception:  # noqa: BLE001
        log.exception("voice download failed")
        await message.answer("Не получилось сохранить голосовое. Попробуй ещё раз.")
        return

    # точка расширения: сейчас NullTranscriber -> None
    transcript = await transcriber.transcribe(target)

    placeholder = f"![[{target.name}]]"
    note = placeholder + "\n# (голосовое, не расшифровано)"
    if transcript:
        note = placeholder + "\n" + transcript

    section = note_body.render_section("note", datetime.now().strftime("%H:%M"), {}, free_text=note)
    # checkins-строка нужна для валидного checkin_id в outbox (FK NOT NULL)
    note_id = await ctx.repos.checkins.create(today, "note")
    await ctx.repos.checkins.finish(note_id, [])
    await ctx.repos.outbox.enqueue(
        note_id,
        {"date": today, "checkin_id": note_id, "raw_section": section, "raw_patch": {}},
    )
    await message.answer("Сохранил голосовое в дневник. Расшифровка — позже, если понадобится.")
