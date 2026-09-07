"""Резолвер «topic_id → имя топика → Obsidian-папка».

Зачем: имя forum-топика НЕ приходит в обычных сообщениях Telegram Bot API —
только в служебном событии создания топика. Поэтому имена тянем через GramJS
(MTProto) драйвером `tools/telegram/driver.cjs get_forum_topics` и кэшируем
`topic_id → title` в JSON. Папка непривязанного топика = `<topic_name_base>/<имя>`.

Приоритет в `folder_for`:
  1. явный маппинг из [channel.topics]  → его папка (ручной override);
  2. General (topic_id None)            → default_topic_folder;
  3. известно имя топика               → `<topic_name_base>/<sanitized имя>`;
  4. имя неизвестно                    → default_topic_folder + фоновый refresh.

КРИТИЧЕСКИЕ ПРАВИЛА:
- драйвер пишет логи в stdout вперемешку с JSON — парсим, находя `[ {`;
- refresh троттлится (min interval), фон через asyncio, не блокирует сообщение;
- имена топиков НЕ логируем как контент (только id и количество).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path

from app.config import PROJECT_ROOT, Settings

log = logging.getLogger("assistant.channel.topics")

# launchd даёт урезанный PATH — ищем node явно, с запасными путями.
_NODE_CANDIDATES = ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node")


def _node_bin() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    return next((p for p in _NODE_CANDIDATES if Path(p).exists()), None)

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_JSON_ARRAY = re.compile(r"\[\s*\{")


def sanitize_folder_name(name: str) -> str:
    """Имя топика → безопасный сегмент пути Obsidian."""
    cleaned = _FORBIDDEN.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Без названия"


def _parse_driver_json(raw: str) -> list[dict]:
    """Достать JSON-массив топиков из stdout драйвера (логи отрезаются)."""
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    m = _JSON_ARRAY.search(raw)
    if not m:
        return []
    start = m.start()
    depth = 0
    instr = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and instr:
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start : i + 1])
    return []


class TopicNameResolver:
    """Кэш `topic_id → title` + резолв в Obsidian-папку. Один на bot-процесс."""

    def __init__(self, settings: Settings) -> None:
        ch = settings.channel
        self._channel_id: int = ch.channel_id
        self._mappings = ch.topic_mappings
        self._default: str = ch.default_topic_folder
        self._name_base: str = ch.topic_name_base.rstrip("/")
        self._driver: Path = Path(ch.telegram_driver).expanduser()
        self._cache_path: Path = self._resolve_cache_path(ch.topic_names_cache)
        self._min_interval: int = ch.topic_refresh_interval_sec

        self._names: dict[int, str] = {}
        self._last_refresh: float = 0.0
        self._refreshing: bool = False
        self._load_cache()

    # ---------- публичное ----------

    async def folder_for(self, topic_id: int | None) -> str:
        """Вернуть Obsidian-папку для топика (см. приоритет в шапке модуля)."""
        # 1. явный ручной маппинг (override) — работает даже без имени
        if topic_id is not None:
            mapped = self._mappings.mappings.get(str(topic_id))
            if mapped:
                return mapped
        # 2. General / без топика
        if topic_id is None:
            return self._default
        # 3. известное имя
        name = self._names.get(topic_id)
        if name:
            return f"{self._name_base}/{sanitize_folder_name(name)}"
        # 4. неизвестно — фоновый refresh, пока кладём в дефолт
        self._schedule_refresh()
        return self._default

    async def refresh(self, *, force: bool = False) -> None:
        """Подтянуть имена топиков через GramJS-драйвер и обновить кэш."""
        now = time.monotonic()
        if not force and (self._refreshing or now - self._last_refresh < self._min_interval):
            return
        self._refreshing = True
        try:
            topics = await self._fetch_topics()
            if topics:
                self._names = {int(t["id"]): str(t["title"]) for t in topics if t.get("title")}
                self._save_cache()
                log.info("topic names refreshed: %d topics", len(self._names))
            self._last_refresh = time.monotonic()
        except Exception as exc:  # драйвер/сеть упали — не валим бота
            log.warning("topic names refresh failed: %r", exc)
        finally:
            self._refreshing = False

    # ---------- внутреннее ----------

    def _schedule_refresh(self) -> None:
        if self._refreshing or time.monotonic() - self._last_refresh < self._min_interval:
            return
        try:
            asyncio.get_running_loop().create_task(self.refresh())
        except RuntimeError:
            pass  # нет активного loop (напр. в тестах) — пропускаем

    async def _fetch_topics(self) -> list[dict]:
        if not self._driver.exists():
            log.warning("telegram driver not found: %s", self._driver)
            return []
        node = _node_bin()
        if node is None:
            log.warning("node binary not found — topic name refresh skipped")
            return []
        args = json.dumps({"chat_id": self._channel_id, "limit": 200})
        proc = await asyncio.create_subprocess_exec(
            node, str(self._driver), "get_forum_topics", args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return _parse_driver_json(out.decode("utf-8", "replace"))

    def _resolve_cache_path(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p

    def _load_cache(self) -> None:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            self._names = {int(k): str(v) for k, v in data.items()}
            log.info("topic names cache loaded: %d", len(self._names))
        except FileNotFoundError:
            self._names = {}
        except Exception as exc:
            log.warning("topic names cache load failed: %r", exc)
            self._names = {}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({str(k): v for k, v in self._names.items()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._cache_path)
        except Exception as exc:
            log.warning("topic names cache save failed: %r", exc)
