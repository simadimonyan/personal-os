#!/usr/bin/env python3
"""Общая механика бюджета контекста Personal OS.

Один источник правды для хуков context_guard / checkpoint / agent_budget /
session_start_status. Всё падает мягко: любая ошибка здесь не должна
ронять сессию, поэтому вызывающая сторона оборачивает вызовы в try.
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
STATE = REPO / ".claude" / "state"
CHECKPOINT = STATE / "checkpoint.md"
FEATURE_LOG = STATE / "feature-log.md"
BUDGET_LOG = STATE / "agent-budget.jsonl"

# Рабочее окно сессии. Не выдумываем свой потолок: Opus 5 реально доходил
# до ~588k без сжатия, поэтому оценка консервативная и близкая к факту.
# Если в settings.json задан autoCompactWindow — считаем по нему.
DEFAULT_WINDOW = 500_000
# Доли окна, на которых система начинает вмешиваться. Считаются от полного
# контекста, а свежая сессия стартует с ~60k системного пролога — пороги
# должны это переживать, а не срабатывать на пустом месте.
SOFT = 0.55   # напомнить: пора закрывать фичу
HARD = 0.75   # требовать чекпоинт
FORCE = 0.88  # один раз задержать остановку и записать чекпоинт


def state_dir() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE


def window() -> int:
    """Окно автосжатия: env → пользовательские настройки → значение по умолчанию."""
    env = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if env and env.isdigit():
        return int(env)
    try:
        cfg = json.loads((Path.home() / ".claude" / "settings.json").read_text("utf-8"))
        v = cfg.get("autoCompactWindow")
        if isinstance(v, int) and v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_WINDOW


def _usage_tokens(entry: dict) -> int:
    u = (entry.get("message") or {}).get("usage") or {}
    return (
        u.get("input_tokens", 0)
        + u.get("cache_read_input_tokens", 0)
        + u.get("cache_creation_input_tokens", 0)
    )


def read_transcript(path: str):
    """Возвращает (контекст_основной_ветки, пик_субагента, файлы_правленые)."""
    main = side = 0
    files: list[str] = []
    if not path or not os.path.exists(path):
        return main, side, files
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "assistant":
                t = _usage_tokens(d)
                if d.get("isSidechain"):
                    side = max(side, t)
                elif t:
                    main = t  # последний ответ основной ветки = текущий контекст
            msg = d.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") in ("Write", "Edit", "NotebookEdit"):
                        fp = (block.get("input") or {}).get("file_path")
                        if fp and fp not in files:
                            files.append(fp)
    return main, side, files


def session_state(session_id: str) -> dict:
    f = state_dir() / f"session-{(session_id or 'unknown')[:8]}.json"
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except Exception:
            pass
    return {}


def save_session_state(session_id: str, data: dict) -> None:
    f = state_dir() / f"session-{(session_id or 'unknown')[:8]}.json"
    f.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))
