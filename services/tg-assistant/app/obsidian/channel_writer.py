"""Запись сообщений из Telegram-топиков в Obsidian.

Каждый топик → отдельная папка в Obsidian (маппинг в config.toml [channel.topics]).
Каждое сообщение → append в дневной файл топика: {vault}/{topic_folder}/{YYYY-MM-DD}.md.

Используем тот же атомарный паттерн что и основной writer:
- read-modify-write свежей версии файла;
- запись через .tmp + os.replace (атомарно на одной FS) — Яндекс.Диск никогда
  не видит полу-записанный файл;
- asyncio.Lock per-file (ключ = folder+date) против гонок;
- весь блокирующий I/O — через asyncio.to_thread.

Содержимое сообщений НИКОГДА не логируется (только события: message_id, topic_id).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

log = logging.getLogger("assistant.obsidian.channel_writer")

# Машинно-распознаваемый маркер начала блока сообщения. По нему edit находит
# и заменяет нужный блок. Формат: "<!-- msg:{message_id} -->".
_MSG_MARKER_PREFIX = "<!-- msg:"
_MSG_MARKER_SUFFIX = " -->"

# извлечение времени HH:MM из заголовка блока "## HH:MM · sender"
_BLOCK_TIME_RE = re.compile(r"^##\s+(\d{1,2}):(\d{2})\b")
_MARKER_ID_RE = re.compile(r"<!--\s*msg:(\d+)\s*-->")


def _msg_marker(message_id: int) -> str:
    return f"{_MSG_MARKER_PREFIX}{message_id}{_MSG_MARKER_SUFFIX}"


@dataclass
class ChannelNote:
    """Данные одного сообщения для записи в дневной файл топика."""

    date: str            # YYYY-MM-DD (локальная дата сообщения)
    hhmm: str            # HH:MM
    topic_id: int | None
    topic_folder: str    # obsidian subpath, напр. "03 — Идеи и мысли/Идеи"
    message_id: int
    sender_name: str
    text: str | None = None
    caption: str | None = None
    media_type: str | None = None
    media_file_id: str | None = None
    topic_name: str | None = None  # отображаемое имя топика (опц.)


class ChannelWriter:
    def __init__(self, settings: Settings) -> None:
        self._vault_root: Path = settings.vault.vault_path
        # Lock на каждый файл (folder + date)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock_for(self, key: str) -> asyncio.Lock:
        return self._locks[key]

    def file_path(self, topic_folder: str, date: str) -> Path:
        return self._vault_root / topic_folder / f"{date}.md"

    async def write_message(self, note: ChannelNote) -> str:
        """Атомарно дописывает блок сообщения в дневной файл топика.

        Возвращает строковый путь к файлу (для записи в obsidian_path).
        Бросает исключение при ошибке I/O — вызывающий код ловит и помечает error.
        """
        key = f"{note.topic_folder}|{note.date}"
        async with self._lock_for(key):
            path = await asyncio.to_thread(self._read_modify_write, note)
        log.info(
            "channel write ok: topic_id=%s message_id=%s",
            note.topic_id,
            note.message_id,
        )
        return str(path)

    async def replace_message(self, note: ChannelNote) -> str | None:
        """Заменяет существующий блок сообщения (по marker) на новый.

        Возвращает путь к файлу если блок найден и заменён, иначе None
        (вызывающий код может решить дописать как новое).
        """
        key = f"{note.topic_folder}|{note.date}"
        async with self._lock_for(key):
            path = await asyncio.to_thread(self._replace_block, note)
        if path is not None:
            log.info(
                "channel edit ok: topic_id=%s message_id=%s",
                note.topic_id,
                note.message_id,
            )
        return str(path) if path is not None else None

    async def has_message(self, topic_folder: str, date: str, message_id: int) -> bool:
        """Есть ли уже блок этого сообщения в дневном файле топика (по marker)."""
        path = self.file_path(topic_folder, date)
        return await asyncio.to_thread(self._marker_present, path, message_id)

    # ---- блокирующие операции (выполняются в потоке) ----

    def _marker_present(self, path: Path, message_id: int) -> bool:
        if not path.exists():
            return False
        return _msg_marker(message_id) in self._read(path)

    def _read_modify_write(self, note: ChannelNote) -> Path:
        path = self.file_path(note.topic_folder, note.date)
        existing = self._read(path)

        if not existing.strip():
            content = self._render_new_file(note)
        elif _msg_marker(note.message_id) in existing:
            # уже есть в файле — идемпотентность (защита от гонки auto-parse/реакция)
            return path
        else:
            content = self._insert_ordered(existing, note)

        self._atomic_write(path, content)
        return path

    def _insert_ordered(self, existing: str, note: ChannelNote) -> str:
        """Вставляет блок на хронологическое место по времени/ID сообщения.

        Нужно для ручной пере-реакции на пропущенное старое сообщение: оно
        встаёт на своё место по дате/времени самого сообщения из ТГ, а не в конец.
        """
        prefix, blocks = self._split_blocks(existing)
        new_block = self._render_block(note)
        new_key = self._sort_key(note.hhmm, note.message_id)
        blocks.append((new_key, new_block))
        blocks.sort(key=lambda b: b[0])
        body = "\n\n".join(text.rstrip() for _, text in blocks)
        prefix = prefix.rstrip()
        if prefix:
            return prefix + "\n\n" + body + "\n"
        return body + "\n"

    @staticmethod
    def _sort_key(hhmm: str | None, message_id: int) -> tuple[int, int]:
        minutes = 99999
        if hhmm:
            m = _BLOCK_TIME_RE.match(f"## {hhmm} · _")
            if m:
                minutes = int(m.group(1)) * 60 + int(m.group(2))
        return (minutes, message_id)

    @classmethod
    def _split_blocks(cls, existing: str) -> tuple[str, list[tuple[tuple[int, int], str]]]:
        """Разбивает файл на (prefix, [(sort_key, block_text)]).

        prefix — frontmatter + заголовок (всё до первого маркера сообщения).
        Каждый блок — от строки-маркера до следующего маркера (или конца файла).
        """
        lines = existing.splitlines()
        marker_idx = [i for i, ln in enumerate(lines) if ln.strip().startswith(_MSG_MARKER_PREFIX)]
        if not marker_idx:
            return existing, []
        prefix = "\n".join(lines[: marker_idx[0]])
        blocks: list[tuple[tuple[int, int], str]] = []
        bounds = marker_idx + [len(lines)]
        for k in range(len(marker_idx)):
            seg = lines[bounds[k] : bounds[k + 1]]
            text = "\n".join(seg)
            blocks.append((cls._block_key(seg), text))
        return prefix, blocks

    @classmethod
    def _block_key(cls, seg: list[str]) -> tuple[int, int]:
        msg_id = 0
        minutes = 99999
        for ln in seg:
            mid = _MARKER_ID_RE.search(ln)
            if mid:
                msg_id = int(mid.group(1))
            tm = _BLOCK_TIME_RE.match(ln.strip())
            if tm:
                minutes = int(tm.group(1)) * 60 + int(tm.group(2))
        return (minutes, msg_id)

    def _replace_block(self, note: ChannelNote) -> Path | None:
        path = self.file_path(note.topic_folder, note.date)
        if not path.exists():
            return None
        existing = self._read(path)
        marker = _msg_marker(note.message_id)
        if marker not in existing:
            return None

        lines = existing.splitlines()
        start = None
        for i, line in enumerate(lines):
            if line.strip() == marker:
                start = i
                break
        if start is None:
            return None

        # блок тянется до следующего marker или до конца файла
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip().startswith(_MSG_MARKER_PREFIX):
                end = j
                break

        new_block = self._render_block(note).splitlines()
        new_lines = lines[:start] + new_block + lines[end:]
        content = "\n".join(new_lines).rstrip() + "\n"
        self._atomic_write(path, content)
        return path

    # ---- рендер ----

    def _render_new_file(self, note: ChannelNote) -> str:
        topic_label = note.topic_name or (
            str(note.topic_id) if note.topic_id is not None else "General"
        )
        fm_lines = [
            "---",
            f"date: {note.date}",
            "type: telegram-log",
        ]
        if note.topic_id is not None:
            fm_lines.append(f"topic_id: {note.topic_id}")
        fm_lines.append(f'topic_folder: "{note.topic_folder}"')
        fm_lines.append("tags: [telegram-log]")
        fm_lines.append("---")
        title = f"# Telegram · {topic_label} · {note.date}"
        block = self._render_block(note)
        return "\n".join(fm_lines) + "\n\n" + title + "\n\n" + block + "\n"

    def _render_block(self, note: ChannelNote) -> str:
        marker = _msg_marker(note.message_id)
        lines = [marker, f"## {note.hhmm} · {note.sender_name}"]
        content = note.text or note.caption
        if content:
            lines.append(content.strip())
        if note.media_type:
            file_ref = (
                f" · file_id: `{note.media_file_id}`" if note.media_file_id else ""
            )
            lines.append(f"**[{note.media_type}]**{file_ref}")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # ---- I/O ----

    @staticmethod
    def _read(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Запись через .tmp в той же папке + os.replace (атомарно на одной FS)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
