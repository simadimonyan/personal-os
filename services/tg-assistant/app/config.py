"""Загрузка конфигурации: секреты из .env + неизменные настройки из config.toml.

Секреты (BOT_TOKEN, OWNER_ID) — только в окружении / .env.
Пути, расписание, тон — в config.toml.
Единственный источник истины для настроек — объект Settings.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - проект требует 3.11+
    raise RuntimeError("Требуется Python 3.11+ (используется tomllib)")


# Корень проекта = папка, содержащая run.py (на уровень выше app/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VaultSettings(BaseModel):
    vault_path: Path
    diary_subpath: str
    attachments_subpath: str
    # Папка для отдельных заметок-«мыслей» (📝 Заметка → отдельный файл с тегами).
    ideas_subpath: str = "03 — Идеи и мысли"
    # Файл задач таск-менеджера (один файл-чеклист, синхронизируется с ботом и Todoist).
    tasks_subpath: str = "04 — Цели и задачи/Задачи.md"
    # Папка для вложений из заметок (картинки, документы, видео) — ресурсы Obsidian.
    files_subpath: str = "09 — Шаблоны и ресурсы/Файлы"
    # Папка для rolling-обзоров авторства (недельные/месячные метрики агентности).
    reviews_subpath: str = "02 — Внутренний мир/Обзоры авторства"

    @property
    def diary_dir(self) -> Path:
        return self.vault_path / self.diary_subpath

    @property
    def attachments_dir(self) -> Path:
        return self.vault_path / self.attachments_subpath

    @property
    def ideas_dir(self) -> Path:
        return self.vault_path / self.ideas_subpath

    @property
    def tasks_file(self) -> Path:
        return self.vault_path / self.tasks_subpath

    @property
    def files_dir(self) -> Path:
        return self.vault_path / self.files_subpath

    @property
    def reviews_dir(self) -> Path:
        return self.vault_path / self.reviews_subpath


class StorageSettings(BaseModel):
    db_path: Path

    def resolved(self, root: Path) -> Path:
        return self.db_path if self.db_path.is_absolute() else root / self.db_path


class ScheduleSettings(BaseModel):
    morning_start: str
    morning_end: str
    day_start: str
    day_end: str
    evening_start: str
    evening_end: str
    light_day_weekday: int = 6


class OutboxSettings(BaseModel):
    poll_interval_seconds: int = 30
    max_attempts: int = 12
    backoff_base_seconds: int = 15
    backoff_cap_seconds: int = 1800


class SchedulerSettings(BaseModel):
    misfire_grace_time_seconds: int = 1800
    coalesce: bool = True
    # Период тика проверки окон напоминаний (секунды). Тик устойчив к сну/перезапуску
    # ноутбука: пропущенную из-за сна минуту окна он догонит при ближайшем пробуждении.
    tick_interval_seconds: int = 300
    # DEPRECATED: переспросы отключены — напоминание по слоту приходит ровно один раз
    # в день (см. jobs._maybe_ping). Поле оставлено для обратной совместимости конфига.
    reask_interval_minutes: int = 30


class TodoistSettings(BaseModel):
    """Интеграция с Todoist. Токен — в .env (TODOIST_TOKEN), не здесь."""

    enabled: bool = False
    token: str = Field(default="", repr=False)
    # Если задан — создаём/читаем задачи только в этом проекте Todoist. Пусто = Inbox.
    project_id: str = ""
    # Период фонового синка (pull из Todoist + push), секунды.
    poll_interval_seconds: int = 300


class LoggingSettings(BaseModel):
    level: str = "INFO"
    max_bytes: int = 2_000_000
    backup_count: int = 5


class TopicMapping(BaseModel):
    # topic_id (str в TOML) -> obsidian_subpath
    # {"12345": "03 — Идеи и мысли/Идеи", "67890": "04 — Цели и задачи"}
    mappings: dict[str, str] = {}

    def get_folder(self, topic_id: int | None, default: str) -> str:
        if topic_id is None:
            return default
        return self.mappings.get(str(topic_id), default)


class ChannelSettings(BaseModel):
    enabled: bool = False
    channel_id: int = 0           # ID Telegram-группы (отрицательный)
    parsed_reaction: str = "✅"   # реакция бота после парсинга
    trigger_reaction: str = "👁"  # реакция пользователя как ручной триггер
    default_topic_folder: str = "03 — Идеи и мысли/Telegram"
    topic_mappings: TopicMapping = TopicMapping()
    # Авто-сохранение по имени топика: непривязанный топик → <topic_name_base>/<имя топика>.
    # Имя берётся через GramJS-драйвер get_forum_topics и кэшируется в topic_names_cache.
    topic_name_base: str = "02 — Внутренний мир"
    telegram_driver: str = "~/Desktop/personal os/tools/telegram/driver.cjs"
    topic_names_cache: str = "data/topic_names.json"  # относительно PROJECT_ROOT
    topic_refresh_interval_sec: int = 300


class Settings(BaseModel):
    """Сводный объект конфигурации приложения."""

    # секреты
    bot_token: str = Field(repr=False)
    owner_id: int
    timezone: str = "Europe/Moscow"

    # из config.toml
    vault: VaultSettings
    storage: StorageSettings
    schedule: ScheduleSettings
    outbox: OutboxSettings
    scheduler: SchedulerSettings
    logging: LoggingSettings
    channel: ChannelSettings = ChannelSettings()
    todoist: TodoistSettings = TodoistSettings()

    # производные
    project_root: Path = PROJECT_ROOT

    @property
    def db_file(self) -> Path:
        return self.storage.resolved(self.project_root)

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"


def _load_toml(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.toml не найден по пути {config_path}. "
            f"Создай его из шаблона в репозитории."
        )
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает singleton Settings. Кэшируется на процесс."""
    # .env рядом с корнем проекта
    load_dotenv(PROJECT_ROOT / ".env")

    bot_token = os.environ.get("BOT_TOKEN")
    owner_id_raw = os.environ.get("OWNER_ID")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env / окружении.")
    if not owner_id_raw:
        raise RuntimeError("OWNER_ID не задан в .env / окружении.")
    try:
        owner_id = int(owner_id_raw)
    except ValueError as exc:
        raise RuntimeError("OWNER_ID должен быть целым числом.") from exc

    timezone = os.environ.get("TZ", "Europe/Moscow")

    config_path = Path(
        os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config.toml"))
    )
    raw = _load_toml(config_path)

    # секция [channel]: вложенная таблица [channel.topics] вынимается отдельно,
    # т.к. это маппинг topic_id -> папка, а не поле ChannelSettings напрямую.
    channel_raw = dict(raw.get("channel", {}))
    topic_mappings_raw = channel_raw.pop("topics", {})
    channel = ChannelSettings(
        **channel_raw,
        topic_mappings=TopicMapping(mappings=topic_mappings_raw),
    )

    # Todoist: неизменные настройки из config.toml, секретный токен — из .env.
    todoist_raw = dict(raw.get("todoist", {}))
    todoist_token = os.environ.get("TODOIST_TOKEN", "").strip()
    if todoist_token:
        todoist_raw["token"] = todoist_token
        todoist_raw.setdefault("enabled", True)
    todoist = TodoistSettings(**todoist_raw)

    return Settings(
        bot_token=bot_token,
        owner_id=owner_id,
        timezone=timezone,
        vault=VaultSettings(**raw["vault"]),
        storage=StorageSettings(**raw["storage"]),
        schedule=ScheduleSettings(**raw["schedule"]),
        outbox=OutboxSettings(**raw.get("outbox", {})),
        scheduler=SchedulerSettings(**raw.get("scheduler", {})),
        logging=LoggingSettings(**raw.get("logging", {})),
        channel=channel,
        todoist=todoist,
    )
