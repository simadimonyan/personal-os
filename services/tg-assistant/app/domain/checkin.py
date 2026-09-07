"""Модель CheckIn — накопление ответов в течение FSM-сессии.

Живёт в FSM-context (сериализуется в dict). Telegram-независима.
Связывает FSM-ответы с machine-ключами метрик из metrics.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CheckIn:
    """Накопленные ответы одного чек-ина."""

    date: str
    slot: str                       # morning|day|evening|situational
    checkin_id: int | None = None   # id строки в SQLite
    started_hhmm: str = field(default_factory=lambda: datetime.now().strftime("%H:%M"))
    answers: dict[str, Any] = field(default_factory=dict)
    free_text: str | None = None

    def set(self, key: str, value: Any) -> None:
        self.answers[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.answers.get(key, default)

    def toggle_multi(self, key: str, value: str) -> list[str]:
        """Toggle для мультивыбора: добавить/убрать значение. Возвращает текущий список."""
        current: list[str] = list(self.answers.get(key, []))
        if value in current:
            current.remove(value)
        else:
            current.append(value)
        self.answers[key] = current
        return current

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "slot": self.slot,
            "checkin_id": self.checkin_id,
            "started_hhmm": self.started_hhmm,
            "answers": self.answers,
            "free_text": self.free_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckIn:
        return cls(
            date=data["date"],
            slot=data["slot"],
            checkin_id=data.get("checkin_id"),
            started_hhmm=data.get("started_hhmm", datetime.now().strftime("%H:%M")),
            answers=data.get("answers", {}),
            free_text=data.get("free_text"),
        )
