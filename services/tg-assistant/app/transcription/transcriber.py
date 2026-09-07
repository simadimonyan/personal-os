"""Точка расширения транскрипции (§0.1, §6 ARCHITECTURE).

В MVP активен NullTranscriber (возвращает None — голос не расшифровывается).
Когда появится ключ/окружение — добавляется один класс (напр.
LocalWhisperTranscriber) с тем же интерфейсом, остальной код не меняется.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Transcriber(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: Path) -> str | None:
        """Возвращает расшифрованный текст или None, если расшифровка недоступна."""
        raise NotImplementedError


class NullTranscriber(Transcriber):
    """Заглушка MVP: не расшифровывает, всегда None."""

    async def transcribe(self, audio_path: Path) -> str | None:  # noqa: ARG002
        return None


# Когда появится local Whisper (рекомендация §6):
#
# class LocalWhisperTranscriber(Transcriber):
#     def __init__(self, model_size: str = "small") -> None:
#         from faster_whisper import WhisperModel
#         self._model = WhisperModel(model_size, device="auto", compute_type="int8")
#
#     async def transcribe(self, audio_path: Path) -> str | None:
#         import asyncio
#         def _run() -> str:
#             segments, _ = self._model.transcribe(str(audio_path), language="ru")
#             return " ".join(s.text.strip() for s in segments).strip()
#         return await asyncio.to_thread(_run) or None
