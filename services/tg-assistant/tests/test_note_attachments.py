"""Вложения в заметках: безопасные имена файлов + уникальность в папке файлов."""

from __future__ import annotations

from types import SimpleNamespace

from app.handlers.notes import _sanitize_filename
from app.obsidian.paths import VaultPaths


def test_sanitize_strips_wikilink_breakers():
    # символы [ ] # ^ | : / \ ломают имя файла и/или wiki-ссылку Obsidian
    assert _sanitize_filename("отчёт[2025]#v^1|copy:final.pdf") == "отчёт 2025 v 1 copy final.pdf"
    assert _sanitize_filename("   ") == "файл"
    assert _sanitize_filename("обычное имя.png") == "обычное имя.png"


def _paths(tmp_path) -> VaultPaths:
    vault = SimpleNamespace(
        files_dir=tmp_path, diary_dir=tmp_path / "d",
        attachments_dir=tmp_path / "a", ideas_dir=tmp_path / "i",
    )
    return VaultPaths(SimpleNamespace(vault=vault))


def test_attachment_file_is_unique(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "img.jpg").write_text("x")
    assert paths.attachment_file("img.jpg").name == "img (2).jpg"
    (tmp_path / "img (2).jpg").write_text("x")
    assert paths.attachment_file("img.jpg").name == "img (3).jpg"
    # свободное имя не трогаем
    assert paths.attachment_file("new.png").name == "new.png"
