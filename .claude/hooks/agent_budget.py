#!/usr/bin/env python3
"""Учёт расхода субагентов (SubagentStop).

Пишет в .claude/state/agent-budget.jsonl, сколько контекста сжёг субагент,
и предупреждает, если тот подошёл к потолку — значит его инструкция велит
читать слишком много, и её надо править, а не терпеть.
"""
import json
import sys

import ctx_lib as C

AGENT_SOFT = 40_000
AGENT_HARD = 60_000


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    _main, side, _files = C.read_transcript(payload.get("transcript_path", ""))
    if not side:
        return 0

    C.state_dir()
    with open(C.BUDGET_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": C.now(),
            "session": payload.get("session_id", "")[:8],
            "agent_peak": side,
        }, ensure_ascii=False) + "\n")

    if side >= AGENT_HARD:
        C.out({
            "systemMessage": f"Субагент дошёл до ~{side // 1000}k токенов "
                             f"(потолок {AGENT_HARD // 1000}k).",
            "hookSpecificOutput": {
                "hookEventName": "SubagentStop",
                "additionalContext": (
                    f"Субагент израсходовал ~{side // 1000}k токенов — выше потолка "
                    f"{AGENT_HARD // 1000}k. Не читай его артефакты сам: возьми отчёт. "
                    "Если такой расход повторяется — сузь задание агенту "
                    "(конкретные пути вместо обзора) или разбей на два вызова."
                ),
            },
        })
    elif side >= AGENT_SOFT:
        C.out({"suppressOutput": True,
               "systemMessage": f"Субагент: ~{side // 1000}k токенов."})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
