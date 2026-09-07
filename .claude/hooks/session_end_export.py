#!/usr/bin/env python3
"""SessionEnd — вербатим сессии уезжает в L0 Obsidian сам.

Протокол Personal OS требует сохранять полный диалог в
«10 — Claude/Контекст и Сессии/», а не summary. Раньше это зависело от
того, вспомнил ли агент про протокол в конце сессии.

Грабля, ради которой хук и написан: claude-sessions определяет vault по CWD
и без VAULT_DIR пишет экспорт в локальную папку проекта. Здесь VAULT_DIR
задан явно.

Про таймаут: CLI берёт максимальный timeout среди SessionEnd-хуков и
зажимает его в 1.5–60 с (SESSION_END_HOOK_TIMEOUT_MS_DEFAULT..60000),
дальше рубит по AbortSignal. Поэтому в settings.json стоит 60, а не 120,
и подпроцессу дано 45 — чтобы он умер сам, до того как его срежут.
Экспорт на транскрипте 876 КБ занимает 0.05 с, запас огромный.

Журнал `.session-export.log` рядом со скриптом отвечает на вопрос
«а сработало ли»: одна строка на каждое завершение сессии с причиной
(clear / resume / prompt_input_exit / other). Убитый терминал в журнал
не попадёт — при SIGKILL хуки не выполняются вовсе.

После экспорта отсоединённо запускается memory_update.py — весь конвейер
`update-memory` (L1→L4). Он живёт минуты, а SessionEnd-хуку CLI даёт
максимум 60 с, поэтому процесс уходит в свою сессию (setsid) и хук сразу
возвращается.

POS_MEMORY_CHILD: конвейер сам поднимает безголовый `claude`, у которого
те же хуки. Без этого маркера его завершение экспортировало бы служебный
прогон в L0 и запускало ещё один конвейер — и так до бесконечности.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

VAULT = ("/Users/dimitrisimonyan/Yandex.Disk.localized/"
         "Self-Education/Knowledge base/Obsidian/Органон")
EXPORTER = Path.home() / ".claude" / "skills" / "sync-claude-sessions" / "scripts" / "claude-sessions"
PROJECTS = Path.home() / ".claude" / "projects"


def find_transcript(data: dict) -> Path | None:
    p = data.get("transcript_path")
    if p and Path(p).is_file():
        return Path(p)
    # Запасной путь: <projects>/<cwd со слэшами через дефис>/<session_id>.jsonl
    cwd = data.get("cwd") or os.getcwd()
    proj = PROJECTS / cwd.replace("/", "-")
    sid = data.get("session_id")
    if sid:
        cand = proj / f"{sid}.jsonl"
        if cand.is_file():
            return cand
    if proj.is_dir():
        files = sorted((f for f in proj.glob("*.jsonl") if f.stat().st_size > 0),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            return files[0]
    return None


LOG = Path(__file__).with_name(".session-export.log")
LOG_KEEP = 200


def log(reason: str, outcome: str) -> None:
    from datetime import datetime
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{reason}\t{outcome}"
    try:
        old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
        LOG.write_text("\n".join((old + [line])[-LOG_KEEP:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def spawn_memory_update(reason: str) -> None:
    """Отсоединённый запуск конвейера памяти. Ошибки — не проблема хука."""
    runner = Path(__file__).with_name("memory_update.py")
    if not runner.is_file():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(runner), "--reason", reason],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    reason = data.get("reason") or "?"
    if os.environ.get("POS_MEMORY_CHILD"):
        log(reason, "пропуск: служебный прогон памяти")
        return
    if not EXPORTER.is_file():
        log(reason, "нет экспортёра")
        return
    t = find_transcript(data)
    if t is None:
        log(reason, "транскрипт не найден")
        return
    # Пустой или крошечный транскрипт (открыл-закрыл) в память не нужен.
    if t.stat().st_size < 2048:
        log(reason, f"пропуск, {t.stat().st_size} Б")
        return
    env = dict(os.environ, VAULT_DIR=VAULT)
    try:
        r = subprocess.run([sys.executable, str(EXPORTER), "-q", "export", str(t)],
                           env=env, capture_output=True, timeout=45)
        log(reason, "ок" if r.returncode == 0 else f"rc={r.returncode}")
    except Exception as e:
        log(reason, f"сбой: {type(e).__name__}")
    # Конвейер памяти запускаем в любом случае: экспорт мог не удаться, а
    # необработанные сессии прошлых заходов всё равно ждут в L0.
    spawn_memory_update(reason)


if __name__ == "__main__":
    main()
