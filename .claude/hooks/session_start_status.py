#!/usr/bin/env python3
"""SessionStart — состояние Personal OS в контекст сессии.

Докладывает только проблемы: лежащие сервисы pos, упавшие крон-задачи,
отставание вектор-индекса от заметок. Когда всё в порядке — молчит,
чтобы не жечь контекст на «всё хорошо».

Вход: JSON на stdin (не используется). Выход: JSON с systemMessage
(короткая строка пользователю) и additionalContext (детали агенту).
"""

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/Users/dimitrisimonyan/Desktop/personal os")
VAULT = Path("/Users/dimitrisimonyan/Yandex.Disk.localized/"
             "Self-Education/Knowledge base/Obsidian/Органон")
DB = ROOT / "tools" / "obsidian" / "tools" / "obsidian_vectors.db"
POS_STATE = ROOT / "services" / "pos_state.json"
CRON_STATE = ROOT / "services" / "cron" / "state.json"
QUEUE = ROOT / "tools" / "obsidian" / "tools" / ".reindex-queue"
MEMORY_STATUS = ROOT / ".claude" / "hooks" / "memory_status.json"
MEMORY_LOCK = ROOT / ".claude" / "hooks" / ".memory-update.lock"

# 08 — тысячи карточек контактов из vCard/CSV-импорта. Они живут в
# social_capital.db и своём графе; в вектор-индекс не идут, иначе затопят
# L4-граф. Не считать их отставанием индекса.
SKIP_SECTIONS = ("08 — Социальный капитал",)

# Сколько заметок должно разойтись с индексом, чтобы об этом сообщать.
INDEX_NOISE_FLOOR = 5
# Насколько устаревшее состояние супервизора считать «молчит».
POS_STALE_MIN = 30


def pos_problems() -> list[tuple[str, str]]:
    """Список (короткая метка для строки статуса, полная строка для агента)."""
    out = []
    try:
        st = json.loads(POS_STATE.read_text(encoding="utf-8"))
    except Exception:
        return out
    try:
        age_min = (time.time() - datetime.strptime(
            st["updated"], "%Y-%m-%d %H:%M:%S").timestamp()) / 60
    except Exception:
        age_min = 0
    if age_min > POS_STALE_MIN:
        out.append(("супервизор молчит",
                    f"супервизор pos молчит {age_min / 60:.1f} ч — состояние ниже "
                    f"может быть неактуальным (./services/pos.py status)"))
    dead = [n for n, s in st.get("services", {}).items() if not s.get("alive")]
    if dead:
        out.append((f"лежат {len(dead)} сервиса",
                    "сервисы лежат: " + ", ".join(dead) +
                    " (./services/pos.py restart <имя>)"))
    return out


def cron_problems() -> list[tuple[str, str]]:
    try:
        st = json.loads(CRON_STATE.read_text(encoding="utf-8"))
    except Exception:
        return []
    bad = [f"{k} ({v.get('last_status')})" for k, v in st.items()
           if isinstance(v, dict) and v.get("last_status") not in (None, "ok", "skipped")]
    if not bad:
        return []
    return [(f"крон: {len(bad)} с ошибкой", "крон с ошибкой: " + ", ".join(bad))]


def index_lag() -> list[tuple[str, str]]:
    if not DB.exists() or not VAULT.is_dir():
        return []
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        indexed = dict(conn.execute(
            "SELECT path, MAX(updated_at) FROM notes GROUP BY path"))
        conn.close()
    except Exception:
        return []
    fresh, changed = 0, 0
    for f in VAULT.rglob("*.md"):
        try:
            rel = str(f.relative_to(VAULT))
        except ValueError:
            continue
        if rel.startswith(SKIP_SECTIONS):
            continue
        was = indexed.get(rel)
        if was is None:
            fresh += 1
        elif f.stat().st_mtime > was + 60:
            changed += 1
    if fresh + changed < INDEX_NOISE_FLOOR:
        return []
    when = ""
    if indexed:
        when = datetime.fromtimestamp(max(indexed.values())).strftime(" (индекс от %d.%m)")
    return [(f"индекс отстаёт на {fresh + changed}",
             f"вектор-индекс отстаёт{when}: {fresh} новых, {changed} изменённых заметок "
             f"— vsearch index / vault-indexer перед работой с базой")]


def queued_notes() -> list[tuple[str, str]]:
    try:
        rows = [l for l in QUEUE.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []
    uniq = sorted({l.split("\t", 1)[-1] for l in rows})
    if not uniq:
        return []
    head = ", ".join(uniq[:3]) + (f" и ещё {len(uniq) - 3}" if len(uniq) > 3 else "")
    return [(f"{len(uniq)} заметок в очереди",
             f"правил заметки мимо индекса ({len(uniq)}): {head} "
             f"— список в tools/obsidian/tools/.reindex-queue")]


def memory_pipeline() -> list[tuple[str, str]]:
    """Как отработал автоконвейер памяти. Молчим, когда всё прошло."""
    if MEMORY_LOCK.exists():
        return [("память обновляется", "конвейер памяти сейчас идёт фоном "
                 "(.claude/hooks/memory_update.py) — заметки L1–L4 могут "
                 "поменяться под руками")]
    try:
        st = json.loads(MEMORY_STATUS.read_text(encoding="utf-8"))
    except Exception:
        return []
    if st.get("ok") or "skipped" in st:
        return []
    what = st.get("error") or " · ".join(
        f"{k}: {v}" for k, v in (st.get("steps") or {}).items()
        if str(v).startswith("СБОЙ") or "ошибка" in str(v))
    what = what or "подробности в .claude/hooks/.memory-update.log"
    return [("память: сбой прогона",
             f"конвейер памяти упал {st.get('finished', '?')}: {what} "
             f"— прогнать заново: python3 .claude/hooks/memory_update.py --force")]


CHECKPOINT = ROOT / ".claude" / "state" / "checkpoint.md"
FEATURE_LOG = ROOT / ".claude" / "state" / "feature-log.md"
# Сколько символов чекпоинта подмешивать. Больше — дороже каждая сессия.
# Хватает последней записи: остальное лежит в файле и читается по требованию.
CHECKPOINT_BUDGET = 1400
FEATURE_LINES = 3      # сколько закрытых фич напоминать
FEATURE_CHARS = 220    # и до какой длины резать каждую


def carryover() -> str:
    """Что система делала до сброса контекста — чтобы новая сессия помнила."""
    parts = []
    try:
        if CHECKPOINT.exists():
            text = CHECKPOINT.read_text(encoding="utf-8")
            body = text.split("\n\n", 2)[-1].strip()
            if body:
                parts.append("Чекпоинт прошлой работы (.claude/state/checkpoint.md):\n"
                             + body[:CHECKPOINT_BUDGET])
    except Exception:
        pass
    try:
        if FEATURE_LOG.exists():
            lines = [l for l in FEATURE_LOG.read_text(encoding="utf-8").splitlines()
                     if l.strip()][-FEATURE_LINES:]
            if lines:
                short = [l[:FEATURE_CHARS] + ("…" if len(l) > FEATURE_CHARS else "")
                         for l in lines]
                parts.append("Последние закрытые фичи:\n" + "\n".join(short))
    except Exception:
        pass
    return "\n\n".join(parts)


def main() -> None:
    sys.stdin.read()  # вход не нужен, но stdin надо вычитать
    problems = (pos_problems() + cron_problems() + index_lag()
                + queued_notes() + memory_pipeline())
    carry = carryover()
    if not problems and not carry:
        return

    blocks = []
    if carry:
        blocks.append(carry + "\n\nЭто память о прошлой сессии, а не задание. "
                              "Продолжай с неё, если Димитри не задал другое.")
    if problems:
        blocks.append(
            "Состояние Personal OS на старте сессии (хук session_start_status):\n"
            + "\n".join(f"- {full}" for _, full in problems) +
            "\nЭто фон, а не задача. Чини только то, что мешает текущему запросу, "
            "и скажи об этом одной строкой.")

    msg = "Personal OS: " + " · ".join(short for short, _ in problems) if problems else ""
    if carry:
        msg = (msg + " · " if msg else "") + "подхвачен чекпоинт прошлой сессии"
    print(json.dumps({
        "systemMessage": msg,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(blocks),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
