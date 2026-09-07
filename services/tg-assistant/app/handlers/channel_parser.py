"""Мониторинг forum-группы: парсинг сообщений из топиков в Obsidian.

Три типа событий:
  A. Новое сообщение в топике  → авто-парсинг + реакция parsed_reaction (✅).
  B. Отредактированное сообщение → перезапись блока в Obsidian.
  C. Реакция-триггер owner (👁)  → ручной парсинг пропущенного сообщения (>24ч).

Роутер строится через build_router(settings), т.к. фильтры зависят от channel_id
из настроек (он недоступен на уровне модуля).

КРИТИЧЕСКИЕ ПРАВИЛА:
- весь файловый I/O — через ChannelWriter (asyncio.to_thread внутри);
- содержимое сообщений НИКОГДА не логируется (только message_id, topic_id, status);
- при ошибке парсинга бот НЕ падает — статус -1 в БД, продолжаем;
- set_message_reaction требует прав администратора — ловим Forbidden/BadRequest.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message, MessageReactionUpdated, ReactionTypeEmoji

from app.channel.topic_names import TopicNameResolver
from app.config import Settings
from app.obsidian.channel_writer import ChannelNote, ChannelWriter
from app.storage.repositories import Repositories

log = logging.getLogger("assistant.channel")


# --- извлечение метаданных сообщения (без логирования содержимого) ---

def _detect_media(message: Message) -> tuple[str | None, str | None]:
    """Возвращает (media_type, file_id) для первого найденного типа медиа."""
    if message.photo:
        # самый крупный размер — последний в списке
        return "photo", message.photo[-1].file_id
    if message.document:
        return "document", message.document.file_id
    if message.video:
        return "video", message.video.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.animation:
        return "animation", message.animation.file_id
    return None, None


def _sender_name(message: Message) -> str:
    u = message.from_user
    if u is None:
        # сообщение от имени канала / sender_chat
        if message.sender_chat is not None:
            return message.sender_chat.title or "channel"
        return "unknown"
    name = u.full_name or u.username or str(u.id)
    return name


def _local_date_hhmm(message: Message) -> tuple[str, str]:
    """Дата/время сообщения. message.date — aware UTC datetime; берём локальное."""
    dt = message.date
    if dt is None:
        dt = datetime.now()
    else:
        # привести к локальной зоне процесса (single-user, одна машина)
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


async def _build_note(
    message: Message, settings: Settings, resolver: TopicNameResolver
) -> ChannelNote:
    topic_id = message.message_thread_id
    folder = await resolver.folder_for(topic_id)
    date, hhmm = _local_date_hhmm(message)
    media_type, media_file_id = _detect_media(message)
    return ChannelNote(
        date=date,
        hhmm=hhmm,
        topic_id=topic_id,
        topic_folder=folder,
        message_id=message.message_id,
        sender_name=_sender_name(message),
        text=message.text,
        caption=message.caption,
        media_type=media_type,
        media_file_id=media_file_id,
    )


async def _set_reaction(bot: Bot, chat_id: int, message_id: int, emoji: str) -> None:
    """Ставит реакцию-эмодзи. Ловит отсутствие прав / неподдерживаемый эмодзи."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except TelegramForbiddenError:
        log.warning(
            "no rights to set reaction (bot must be admin): message_id=%s", message_id
        )
    except TelegramBadRequest as exc:
        log.warning(
            "reaction rejected (emoji unsupported?): message_id=%s err=%s",
            message_id, exc,
        )


async def _probe_topic_id(bot: Bot, channel_id: int, message_id: int) -> int | None:
    """Определить topic_id отправив ответ-зонд на сообщение и прочитав message_thread_id из ответа.
    Telegram автоматически ставит thread топика в reply — без MTProto и внешних инструментов."""
    try:
        probe = await bot.send_message(
            chat_id=channel_id,
            text="​",  # zero-width space — невидимо
            reply_to_message_id=message_id,
            disable_notification=True,
        )
        topic_id = probe.message_thread_id
        await bot.delete_message(channel_id, probe.message_id)
        return topic_id
    except Exception as exc:  # noqa: BLE001
        log.warning("topic probe failed: msg=%s err=%r", message_id, exc)
        return None


def build_router(settings: Settings) -> Router:
    channel_id = settings.channel.channel_id
    parsed_reaction = settings.channel.parsed_reaction
    trigger_reaction = settings.channel.trigger_reaction
    owner_id = settings.owner_id

    router = Router(name="channel_parser")
    writer = ChannelWriter(settings)
    resolver = TopicNameResolver(settings)

    # ---- A. Новое сообщение в топике ----
    @router.message(F.chat.id == channel_id)
    async def handle_channel_message(
        message: Message,
        bot: Bot,
        repos: Repositories,
    ) -> None:
        # пропускаем служебные апдейты форума (создание/закрытие топиков и т.п.)
        if message.text is None and message.caption is None and not _has_media(message):
            return
        log.info("channel message: id=%s thread_id=%s", message.message_id, message.message_thread_id)
        await _parse_and_write(message, bot, repos, settings, writer, resolver, parsed_reaction)

    # ---- B. Отредактированное сообщение ----
    @router.edited_message(F.chat.id == channel_id)
    async def handle_edited_channel_message(
        message: Message,
        repos: Repositories,
    ) -> None:
        existing = await repos.channel_messages.get(channel_id, message.message_id)
        if existing is None:
            # не видели раньше — ничего перезаписывать; пусть остаётся как есть
            return
        note = await _build_note(message, settings, resolver)
        # папка/дата берём из старой записи если они менялись маловероятно;
        # используем сохранённый obsidian_path-расчёт через note (folder детерминирован)
        try:
            await repos.channel_messages.update_content(
                channel_id, message.message_id, message.text, message.caption
            )
            path = await writer.replace_message(note)
            if path is None:
                # блок не нашёлся (старый файл/формат) — дописываем заново
                path = await writer.write_message(note)
            await repos.channel_messages.mark_parsed(
                channel_id, message.message_id, path
            )
        except Exception as exc:  # noqa: BLE001 — не валим бота
            log.warning(
                "edit parse failed: message_id=%s err=%r", message.message_id, exc
            )
            await repos.channel_messages.mark_error(
                channel_id, message.message_id, repr(exc)
            )

    # ---- C. Реакция-триггер owner (ручной парсинг пропущенных) ----
    @router.message_reaction()
    async def handle_reaction_trigger(
        reaction: MessageReactionUpdated,
        bot: Bot,
        repos: Repositories,
    ) -> None:
        if reaction.chat.id != channel_id:
            return
        # реакцию должен поставить owner
        if reaction.user is None or reaction.user.id != owner_id:
            return
        # любая новая реакция от владельца — триггер
        if not reaction.new_reaction:
            return
        log.info("reaction raw: msg=%s extra=%s", reaction.message_id, reaction.model_extra)

        message_id = reaction.message_id

        # ВАЖНО: не доверяем флагу parsed в БД. Проверяем РЕАЛЬНОЕ наличие заметки в
        # Obsidian; если её нет — пере-реакция сохраняет сообщение на своё место по
        # дате/времени самого сообщения из ТГ. Чтобы узнать оригинальную дату и
        # содержимое — форвардим себе в DM, читаем forward_origin, затем удаляем.
        try:
            forwarded = await bot.forward_message(
                chat_id=owner_id,
                from_chat_id=channel_id,
                message_id=message_id,
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning(
                "reaction trigger: cannot forward message_id=%s err=%s",
                message_id, exc,
            )
            await repos.channel_messages.upsert(
                channel_id=channel_id,
                topic_id=None,
                message_id=message_id,
                text=None, caption=None, media_type=None, media_file_id=None,
                from_user_id=None, sender_name=None,
                sent_at=datetime.now().isoformat(timespec="seconds"),
            )
            await repos.channel_messages.mark_error(
                channel_id, message_id, f"forward failed: {exc!r}"
            )
            return

        try:
            # topic_id: из DB если auto-parse уже видел, иначе через reply-зонд (Bot API)
            existing = await repos.channel_messages.get(channel_id, message_id)
            topic_id = existing["topic_id"] if existing and existing.get("topic_id") is not None else None
            if topic_id is None:
                topic_id = await _probe_topic_id(bot, channel_id, message_id)
            folder = await resolver.folder_for(topic_id)
            # дата/время САМОГО сообщения из ТГ (forward_origin), а не времени форварда
            date, hhmm = _origin_date_hhmm(forwarded)

            # уже есть в Obsidian (реальная проверка по файлу) — ничего не дописываем
            if await writer.has_message(folder, date, message_id):
                log.info(
                    "reaction trigger: message_id=%s already in obsidian (%s/%s), skip",
                    message_id, folder, date,
                )
                await repos.channel_messages.mark_parsed(
                    channel_id, message_id, str(writer.file_path(folder, date))
                )
                return

            media_type, media_file_id = _detect_media(forwarded)
            sender = _forward_origin_sender(forwarded)
            note = ChannelNote(
                date=date,
                hhmm=hhmm,
                topic_id=topic_id,
                topic_folder=folder,
                message_id=message_id,
                sender_name=sender,
                text=forwarded.text,
                caption=forwarded.caption,
                media_type=media_type,
                media_file_id=media_file_id,
            )
            row_id = await repos.channel_messages.upsert(
                channel_id=channel_id,
                topic_id=topic_id,
                message_id=message_id,
                text=forwarded.text,
                caption=forwarded.caption,
                media_type=media_type,
                media_file_id=media_file_id,
                from_user_id=None,
                sender_name=sender,
                sent_at=datetime.now().isoformat(timespec="seconds"),
            )
            path = await writer.write_message(note)
            await repos.channel_messages.mark_parsed(channel_id, message_id, path)
            log.info(
                "reaction trigger saved (ordered): message_id=%s topic_id=%s date=%s row=%s",
                message_id, topic_id, date, row_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "reaction trigger parse failed: message_id=%s err=%r",
                message_id, exc,
            )
            await repos.channel_messages.upsert(
                channel_id=channel_id,
                topic_id=None,
                message_id=message_id,
                text=None, caption=None, media_type=None, media_file_id=None,
                from_user_id=None, sender_name=None,
                sent_at=datetime.now().isoformat(timespec="seconds"),
            )
            await repos.channel_messages.mark_error(channel_id, message_id, repr(exc))
        finally:
            # подчищаем форвард из DM в любом случае
            try:
                await bot.delete_message(owner_id, forwarded.message_id)
            except (TelegramForbiddenError, TelegramBadRequest):
                pass

        # ставим parsed_reaction если успешно
        if await repos.channel_messages.is_parsed(channel_id, message_id):
            await _set_reaction(bot, channel_id, message_id, parsed_reaction)

    # ---- D. Debug: /topics в группе ----
    @router.message(F.chat.id == channel_id, F.text.startswith("/topics"))
    async def handle_topics_debug(
        message: Message,
        repos: Repositories,
    ) -> None:
        if message.from_user is None or message.from_user.id != owner_id:
            return
        topics = await repos.channel_messages.recent_topics(channel_id)
        if not topics:
            await message.reply(
                "Пока нет данных о топиках. Напиши что-нибудь в топиках — "
                "бот их запомнит, потом снова /topics."
            )
            return
        lines = ["Топики (topic_id → активность):"]
        for topic_id, info in topics:
            tid = "General" if topic_id is None else str(topic_id)
            lines.append(f"• `{tid}` — {info}")
        lines.append("")
        lines.append("Для маппинга добавь в config.toml секцию [channel.topics].")
        await message.reply("\n".join(lines))

    return router


def _has_media(message: Message) -> bool:
    media_type, _ = _detect_media(message)
    return media_type is not None


def _forward_origin_sender(forwarded: Message) -> str:
    """Имя автора оригинала из forward_origin (aiogram 3.x)."""
    origin = getattr(forwarded, "forward_origin", None)
    if origin is not None:
        # MessageOriginUser / MessageOriginHiddenUser / MessageOriginChat / MessageOriginChannel
        user = getattr(origin, "sender_user", None)
        if user is not None:
            return user.full_name or user.username or str(user.id)
        name = getattr(origin, "sender_user_name", None)
        if name:
            return name
        chat = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)
        if chat is not None:
            return chat.title or "channel"
    return _sender_name(forwarded)


def _origin_date_hhmm(forwarded: Message) -> tuple[str, str]:
    """Дата/время САМОГО сообщения из ТГ (из forward_origin), а не времени форварда.

    forward_origin.date — aware UTC datetime оригинала; приводим к локальной зоне.
    Фолбэк — дата форварда, если origin недоступен (скрытый автор и т.п.).
    """
    origin = getattr(forwarded, "forward_origin", None)
    dt = getattr(origin, "date", None) if origin is not None else None
    if dt is None:
        dt = forwarded.date or datetime.now()
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


async def _parse_and_write(
    message: Message,
    bot: Bot,
    repos: Repositories,
    settings: Settings,
    writer: ChannelWriter,
    resolver: TopicNameResolver,
    parsed_reaction: str,
) -> None:
    """Общий путь авто-парсинга нового сообщения (status pending → done/error)."""
    channel_id = settings.channel.channel_id
    note = await _build_note(message, settings, resolver)
    media_type, media_file_id = _detect_media(message)

    # 1. сохранить в channel_messages (pending). INSERT OR IGNORE — идемпотентно.
    await repos.channel_messages.upsert(
        channel_id=channel_id,
        topic_id=note.topic_id,
        message_id=message.message_id,
        text=message.text,
        caption=message.caption,
        media_type=media_type,
        media_file_id=media_file_id,
        from_user_id=message.from_user.id if message.from_user else None,
        sender_name=note.sender_name,
        sent_at=datetime.now().isoformat(timespec="seconds"),
    )

    # уже распарсено (например, ретриггер) — не дублируем запись/реакцию
    if await repos.channel_messages.is_parsed(channel_id, message.message_id):
        return

    try:
        # 2. записать в Obsidian
        path = await writer.write_message(note)
        # 3. status=done
        await repos.channel_messages.mark_parsed(
            channel_id, message.message_id, path
        )
        log.info(
            "channel message parsed: message_id=%s topic_id=%s",
            message.message_id, note.topic_id,
        )
    except Exception as exc:  # noqa: BLE001 — бот не должен падать
        await repos.channel_messages.mark_error(
            channel_id, message.message_id, repr(exc)
        )
        log.warning(
            "channel parse failed: message_id=%s topic_id=%s err=%r",
            message.message_id, note.topic_id, exc,
        )
        return

    # 4. реакция parsed_reaction (только при успехе)
    await _set_reaction(bot, channel_id, message.message_id, parsed_reaction)
