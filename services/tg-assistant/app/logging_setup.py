"""Настройка логирования с ротацией файла.

КРИТИЧНО (§4.1 ARCHITECTURE): в логи НЕ пишется содержимое ответов
пользователя и текст заметок — только события (имя слота, статус записи,
ошибки). Приватность психо-данных.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings


def setup_logging(settings: Settings) -> logging.Logger:
    """Конфигурирует root-логгер: файл с ротацией + stderr.

    Возвращает логгер приложения.
    """
    logs_dir: Path = settings.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "bot.log"

    level = getattr(logging, settings.logging.level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    # очищаем хендлеры на случай повторной инициализации (например в тестах)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=settings.logging.max_bytes,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(level)
    root.addHandler(stream_handler)

    # aiogram / apscheduler — поспокойнее, чтобы не шумели на INFO
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

    app_logger = logging.getLogger("assistant")
    app_logger.info("logging initialized (level=%s, file=%s)", settings.logging.level, log_file)
    return app_logger
