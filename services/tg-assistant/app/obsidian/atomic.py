"""Атомарная запись файла через .tmp + os.replace (общий хелпер).

Вынесено из ObsidianWriter, чтобы переиспользовать в tasks_doc (файл задач) —
гарантия, что Яндекс.Диск никогда не увидит полу-записанный файл.

Блокирующий I/O — вызывать через asyncio.to_thread из async-кода.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
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
        # подчистить временный файл при любой ошибке
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
