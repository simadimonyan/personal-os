#!/usr/bin/env python3
"""Сторож контекста: следит, сколько занято, и не даёт сессии распухнуть.

Вешается на Stop (раз в ход, дёшево). Три ступени от окна автосжатия:
  55% — тихое напоминание закрыть фичу чекпоинтом;
  75% — требование записать чекпоинт сейчас;
  88% — один раз за сессию удерживает остановку, пока чекпоинт не записан.

Молчит, пока запас есть. Любая ошибка = молчание, сессия не страдает.
"""
import json
import sys

import ctx_lib as C


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    # защита от петли: Claude Code выставляет флаг, если остановку уже блокировали
    if payload.get("stop_hook_active"):
        return 0

    sid = payload.get("session_id", "")
    tokens, side, _files = C.read_transcript(payload.get("transcript_path", ""))
    if not tokens:
        return 0

    win = C.window()
    ratio = tokens / win
    st = C.session_state(sid)
    warned = st.get("warned", 0.0)
    forced = st.get("forced", False)
    st["peak"] = max(st.get("peak", 0), tokens)
    st["peak_subagent"] = max(st.get("peak_subagent", 0), side)

    k = tokens // 1000
    result = 0

    if ratio >= C.FORCE and not forced:
        st["forced"] = True
        st["warned"] = ratio
        C.save_session_state(sid, st)
        C.out({
            "decision": "block",
            "reason": (
                f"СТОП по бюджету контекста: занято ~{k}k из {win // 1000}k "
                f"({ratio:.0%}). Прежде чем закончить ход — запиши чекпоинт, "
                "иначе сжатие съест ход работы:\n"
                "  python3 .claude/hooks/checkpoint.py --note \"<что сделано>\" "
                "--next \"<следующий шаг>\"\n"
                "Затем одной строкой скажи Димитри, что фича закрыта и можно "
                "начинать новую сессию (/clear) — память подхватится из чекпоинта."
            ),
        })
        return 0

    if ratio >= C.HARD and warned < C.HARD:
        st["warned"] = ratio
        C.save_session_state(sid, st)
        C.out({
            "systemMessage": (
                f"⚠️ Контекст ~{k}k из {win // 1000}k ({ratio:.0%}). "
                "Закрой текущую фичу чекпоинтом и начинай новую сессию."
            ),
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": (
                    f"Бюджет контекста: занято ~{k}k ({ratio:.0%} окна). "
                    "Доводи текущую фичу до конца, пиши чекпоинт "
                    "(python3 .claude/hooks/checkpoint.py --note ... --next ...) "
                    "и не начинай новую крупную задачу в этой сессии."
                ),
            },
        })
        return 0

    if ratio >= C.SOFT and warned < C.SOFT:
        st["warned"] = ratio
        C.save_session_state(sid, st)
        C.out({
            "systemMessage": (
                f"Контекст ~{k}k из {win // 1000}k ({ratio:.0%}) — "
                "следующую фичу лучше начинать с чистой сессии."
            ),
            "suppressOutput": True,
        })
        return result

    C.save_session_state(sid, st)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
