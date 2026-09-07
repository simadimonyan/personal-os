#!/usr/bin/env python3
"""PostToolUse (Write|Edit) — очередь переиндексации и владелец файла.

Две задачи:
1. Заметка, которую правил агент, попадает в .reindex-queue — vault-indexer
   и SessionStart видят, что именно разошлось с индексом, без полного скана.
2. Файл, оказавшийся root-овским (Bash в части сессий работает от root),
   чинится сразу: mission-control и сервисы pos работают от пользователя и
   падают на таком файле с EPERM. Если прав на chown нет — говорим об этом
   агенту, а не молчим.
"""

import json
import os
import pwd
import sys
from pathlib import Path

ROOT = Path("/Users/dimitrisimonyan/Desktop/personal os")
VAULT = Path("/Users/dimitrisimonyan/Yandex.Disk.localized/"
             "Self-Education/Knowledge base/Obsidian/Органон")
QUEUE = ROOT / "tools" / "obsidian" / "tools" / ".reindex-queue"
OWNER = "dimitrisimonyan"
SKIP_SECTIONS = ("08 — Социальный капитал",)
QUEUE_MAX = 500


def under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def enqueue(rel: str) -> None:
    try:
        seen = QUEUE.read_text(encoding="utf-8").splitlines() if QUEUE.exists() else []
    except Exception:
        seen = []
    if rel in seen:
        return
    seen.append(rel)
    try:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE.write_text("\n".join(seen[-QUEUE_MAX:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def fix_owner(path: Path) -> str | None:
    """Вернуть предупреждение, если файл root-овский и починить нельзя."""
    try:
        st = path.stat()
    except Exception:
        return None
    if st.st_uid != 0:
        return None
    try:
        u = pwd.getpwnam(OWNER)
    except KeyError:
        return None
    if os.geteuid() == 0:
        try:
            os.chown(path, u.pw_uid, u.pw_gid)
            return None
        except Exception:
            pass
    return (f"Файл {path} принадлежит root — сервисы pos (mission-control и др.) "
            f"работают от {OWNER} и упадут на нём с EPERM. "
            f"Почини: sudo chown {OWNER}:staff '{path}'")


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    raw = ((data.get("tool_response") or {}).get("filePath")
           or (data.get("tool_input") or {}).get("file_path"))
    if not raw:
        return
    path = Path(raw)
    if not path.exists():
        return

    if under(path, VAULT) and path.suffix == ".md":
        rel = str(path.relative_to(VAULT))
        if not rel.startswith(SKIP_SECTIONS):
            enqueue(rel)

    if under(path, VAULT) or under(path, ROOT):
        warn = fix_owner(path)
        if warn:
            print(json.dumps({
                "systemMessage": "⚠️ root-овский файл в проекте",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": warn,
                },
            }, ensure_ascii=False))


if __name__ == "__main__":
    main()
