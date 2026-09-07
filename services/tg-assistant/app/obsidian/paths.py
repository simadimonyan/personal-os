"""Резолв путей внутри vault. Единственное место, где формируются имена файлов.

Имя дневного файла: YYYY-MM-DD.md в diary_dir.
Вложения: attachments_dir/voice_HHMMSS.ogg.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


class VaultPaths:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def diary_dir(self) -> Path:
        return self._s.vault.diary_dir

    @property
    def attachments_dir(self) -> Path:
        return self._s.vault.attachments_dir

    @property
    def ideas_dir(self) -> Path:
        return self._s.vault.ideas_dir

    @property
    def tasks_file(self) -> Path:
        """Файл задач таск-менеджера (один файл-чеклист)."""
        return self._s.vault.tasks_file

    @property
    def files_dir(self) -> Path:
        """Папка вложений из заметок (картинки, документы, видео)."""
        return self._s.vault.files_dir

    @property
    def reviews_dir(self) -> Path:
        """Папка rolling-обзоров авторства (недельные/месячные метрики)."""
        return self._s.vault.reviews_dir

    def review_file(self, name: str) -> Path:
        """Rolling-файл обзора (напр. «Недельные обзоры»)."""
        return self.reviews_dir / f"{name}.md"

    def attachment_file(self, name: str) -> Path:
        """Путь к вложению в папке файлов. Если занято — добавляет ' (N)' к имени."""
        target = self.files_dir / name
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        i = 2
        while (cand := self.files_dir / f"{stem} ({i}){suffix}").exists():
            i += 1
        return cand

    def diary_file(self, date: str) -> Path:
        """date — строка YYYY-MM-DD."""
        return self.diary_dir / f"{date}.md"

    def thought_file(self, filename: str) -> Path:
        """Отдельный файл заметки-мысли. filename — без расширения .md."""
        return self.ideas_dir / f"{filename}.md"

    def voice_file(self, hhmmss: str, ext: str = "ogg") -> Path:
        return self.attachments_dir / f"voice_{hhmmss}.{ext}"

    def ensure_dirs(self) -> None:
        """Создаёт diary/attachments/ideas/files директории, если их нет. Блокирующий
        I/O — вызывать через asyncio.to_thread из async-кода."""
        self.diary_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.ideas_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
