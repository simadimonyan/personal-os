"""Тесты канал-мониторинга: config, middleware, writer, repo."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from aiogram.types import (
    Chat,
    Message,
    MessageReactionUpdated,
    ReactionTypeEmoji,
    Update,
    User,
)

from app.config import (
    ChannelSettings,
    LoggingSettings,
    OutboxSettings,
    ScheduleSettings,
    SchedulerSettings,
    Settings,
    StorageSettings,
    TopicMapping,
    VaultSettings,
)
from app.handlers._owner import OwnerOnlyMiddleware
from app.obsidian.channel_writer import ChannelNote, ChannelWriter
from app.storage.db import Database
from app.storage.repositories import Repositories

OWNER = 7
CHANNEL = -100500


def _settings(vault: Path, enabled: bool = True) -> Settings:
    return Settings(
        bot_token="x",
        owner_id=OWNER,
        vault=VaultSettings(
            vault_path=vault, diary_subpath="d", attachments_subpath="a"
        ),
        storage=StorageSettings(db_path=Path("x.db")),
        schedule=ScheduleSettings(
            morning_start="10:30",
            morning_end="11:00",
            day_start="15:00",
            day_end="16:00",
            evening_start="22:30",
            evening_end="23:00",
        ),
        outbox=OutboxSettings(),
        scheduler=SchedulerSettings(),
        logging=LoggingSettings(),
        channel=ChannelSettings(
            enabled=enabled,
            channel_id=CHANNEL,
            topic_mappings=TopicMapping(mappings={"12345": "Идеи/A"}),
        ),
    )


# ---------------- TopicMapping ----------------

def test_topic_mapping_resolution() -> None:
    tm = TopicMapping(mappings={"12345": "A/B"})
    assert tm.get_folder(12345, "def") == "A/B"
    assert tm.get_folder(None, "def") == "def"
    assert tm.get_folder(999, "def") == "def"


# ---------------- Middleware ----------------

async def _run_mw(mw: OwnerOnlyMiddleware, update: Update) -> bool:
    called = {"v": False}

    async def handler(_e, _d):  # noqa: ANN001
        called["v"] = True

    await mw(handler, update, {})
    return called["v"]


@pytest.mark.asyncio
async def test_mw_owner_dm_passes() -> None:
    mw = OwnerOnlyMiddleware(OWNER, channel_id=CHANNEL)
    chat = Chat(id=OWNER, type="private")
    user = User(id=OWNER, is_bot=False, first_name="O")
    m = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="hi")
    assert await _run_mw(mw, Update(update_id=1, message=m)) is True


@pytest.mark.asyncio
async def test_mw_channel_message_passes() -> None:
    mw = OwnerOnlyMiddleware(OWNER, channel_id=CHANNEL)
    chat = Chat(id=CHANNEL, type="supergroup", is_forum=True)
    user = User(id=42, is_bot=False, first_name="S")
    m = Message(message_id=2, date=datetime.now(), chat=chat, from_user=user, text="x")
    assert await _run_mw(mw, Update(update_id=2, message=m)) is True


@pytest.mark.asyncio
async def test_mw_channel_reaction_passes() -> None:
    mw = OwnerOnlyMiddleware(OWNER, channel_id=CHANNEL)
    chat = Chat(id=CHANNEL, type="supergroup", is_forum=True)
    owner = User(id=OWNER, is_bot=False, first_name="O")
    ru = MessageReactionUpdated(
        chat=chat,
        message_id=3,
        user=owner,
        date=datetime.now(),
        old_reaction=[],
        new_reaction=[ReactionTypeEmoji(emoji="👁")],
    )
    assert await _run_mw(mw, Update(update_id=3, message_reaction=ru)) is True


@pytest.mark.asyncio
async def test_mw_other_group_blocked() -> None:
    mw = OwnerOnlyMiddleware(OWNER, channel_id=CHANNEL)
    chat = Chat(id=-999, type="supergroup")
    user = User(id=42, is_bot=False, first_name="S")
    m = Message(message_id=4, date=datetime.now(), chat=chat, from_user=user, text="x")
    assert await _run_mw(mw, Update(update_id=4, message=m)) is False


@pytest.mark.asyncio
async def test_mw_disabled_channel_blocks() -> None:
    # channel_id=0 => канал выключен, старое поведение
    mw = OwnerOnlyMiddleware(OWNER, channel_id=0)
    chat = Chat(id=CHANNEL, type="supergroup", is_forum=True)
    user = User(id=42, is_bot=False, first_name="S")
    m = Message(message_id=5, date=datetime.now(), chat=chat, from_user=user, text="x")
    assert await _run_mw(mw, Update(update_id=5, message=m)) is False


# ---------------- ChannelWriter ----------------

@pytest.mark.asyncio
async def test_writer_new_append_edit() -> None:
    vault = Path(tempfile.mkdtemp())
    w = ChannelWriter(_settings(vault))

    n1 = ChannelNote(
        date="2026-06-09", hhmm="10:00", topic_id=12345,
        topic_folder="Идеи/A", message_id=100, sender_name="Alice",
        text="первая",
    )
    path = await w.write_message(n1)
    assert Path(path).exists()

    n2 = ChannelNote(
        date="2026-06-09", hhmm="10:05", topic_id=12345,
        topic_folder="Идеи/A", message_id=101, sender_name="Bob",
        caption="cap", media_type="photo", media_file_id="F1",
    )
    await w.write_message(n2)

    content = Path(path).read_text(encoding="utf-8")
    assert "type: telegram-log" in content
    assert "<!-- msg:100 -->" in content
    assert "<!-- msg:101 -->" in content
    assert "**[photo]** · file_id: `F1`" in content

    # edit 100, 101 должно выжить
    n1e = ChannelNote(
        date="2026-06-09", hhmm="10:00", topic_id=12345,
        topic_folder="Идеи/A", message_id=100, sender_name="Alice",
        text="ИЗМЕНЕНО",
    )
    rp = await w.replace_message(n1e)
    assert rp is not None
    content2 = Path(path).read_text(encoding="utf-8")
    assert "ИЗМЕНЕНО" in content2
    assert "первая" not in content2
    assert "cap" in content2
    assert content2.count("<!-- msg:100 -->") == 1
    assert content2.count("<!-- msg:101 -->") == 1


@pytest.mark.asyncio
async def test_writer_replace_missing_returns_none() -> None:
    vault = Path(tempfile.mkdtemp())
    w = ChannelWriter(_settings(vault))
    n = ChannelNote(
        date="2026-06-09", hhmm="11:00", topic_id=12345,
        topic_folder="Идеи/A", message_id=999, sender_name="X", text="nope",
    )
    assert await w.replace_message(n) is None


# ---------------- Repo ----------------

@pytest.mark.asyncio
async def test_channel_repo_idempotent_and_status() -> None:
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "t.db")
    await db.connect()
    repos = Repositories.build(db)
    cm = repos.channel_messages

    rid1 = await cm.upsert(CHANNEL, 1, 42, "hi", None, None, None, 7, "A", "2026-06-09T10:00:00")
    rid2 = await cm.upsert(CHANNEL, 1, 42, "changed", None, None, None, 7, "A", "2026-06-09T10:01:00")
    assert rid1 == rid2

    assert await cm.is_parsed(CHANNEL, 42) is False
    await cm.mark_parsed(CHANNEL, 42, "/v/x.md")
    assert await cm.is_parsed(CHANNEL, 42) is True

    await cm.upsert(CHANNEL, 1, 43, "e", None, None, None, 7, "B", "2026-06-09T10:02:00")
    await cm.mark_error(CHANNEL, 43, "boom")
    row = await cm.get(CHANNEL, 43)
    assert row["parsed"] == -1
    assert row["parse_error"] == "boom"

    pending = await cm.get_pending(CHANNEL)
    assert pending == []

    await db.close()
