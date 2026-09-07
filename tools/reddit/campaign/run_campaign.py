#!/usr/bin/env python3
"""
Прогон публикаций по сабреддитам, где разрешение не требуется.

Порядок сознательный: сначала самый маленький сабреддит — если пост снимут,
это выяснится дёшево, а не на аудитории в 48 тысяч. После каждой публикации
пауза и проверка, что пост жив; две подряд снятых публикации останавливают
прогон целиком (значит дело в тексте или в аккаунте, а не в площадке).

Запуск: python3 campaign/run_campaign.py [--dry]
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

spec = importlib.util.spec_from_file_location("rt", ROOT / "reddit.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

LEARNERS = (HERE / "learners.md").read_text(encoding="utf-8").strip()
TEACHERS = (HERE / "teachers.md").read_text(encoding="utf-8").strip()

T_LEARN = ("Building an app for learning English from films — community first, "
           "not flashcards. Would you use it? (5–7 min survey)")
T_TEACH = ("Teachers: would you run clubs and do breakdowns inside an "
           "English-through-films app? (survey)")

# от малого к большому — дешёвая проверка гипотезы
PLAN = [
    ("English_Learning_Base", T_LEARN, LEARNERS),
    ("ukr_english_learning", T_LEARN, LEARNERS),
    ("englishteachers", T_TEACH, TEACHERS),
    ("LanguageBuds", T_LEARN, LEARNERS),
    ("LearningEnglish", T_LEARN, LEARNERS),
    ("OnlineESLTeaching", T_TEACH, TEACHERS),
]

GAP_SEC = 300        # пауза между публикациями
CHECK_AFTER = 90     # через сколько проверять, что пост жив
DRY = "--dry" in sys.argv


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    conn = rt.db()
    rd = rt.Reddit(rt.load_config(), conn)
    removed_streak = 0
    done = []

    for i, (sub, title, text) in enumerate(PLAN):
        if DRY:
            log(f"(dry) r/{sub} · {title}")
            continue
        if i:
            log(f"пауза {GAP_SEC} с перед r/{sub}")
            time.sleep(GAP_SEC)

        log(f"публикую в r/{sub}")
        res = rd.post_form(
            "/api/submit", sr=sub, kind="self", title=title, text=text,
            sendreplies="true", resubmit="true",
        )
        payload = res.get("json", {}) if isinstance(res, dict) else {}
        errors = payload.get("errors") or []
        if errors:
            log(f"  ✗ отклонено: {errors}")
            continue
        d = payload.get("data", {})
        pid = d.get("name") or (f"t3_{d['id']}" if d.get("id") else None)
        link = d.get("url")
        conn.execute(
            "INSERT OR REPLACE INTO posts(id,subreddit,title,kind,permalink,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (pid, sub, title, "self", link, time.time()),
        )
        conn.commit()
        log(f"  ✓ {link}")

        time.sleep(CHECK_AFTER)
        info = rd.info(pid) if pid else None
        removed = None
        if info:
            removed = info.get("removed_by_category")
            listed = rd.in_listing(sub, pid, info.get("created_utc") or time.time())
            alive = 0 if (removed or listed is False) else (1 if listed else None)
        else:
            alive, removed = 0, "не найден"
        conn.execute("UPDATE posts SET alive=?,removed_by=?,checked_at=? WHERE id=?",
                     (alive, removed, time.time(), pid))
        conn.commit()
        state = {1: "жив", 0: f"СНЯТ ({removed})", None: "не ясно"}[alive]
        log(f"  проверка: {state}")
        done.append((sub, link, state))

        removed_streak = removed_streak + 1 if alive == 0 else 0
        if removed_streak >= 2:
            log("две подряд снятых — останавливаю прогон")
            break

    log("ИТОГ:")
    for sub, link, state in done:
        log(f"  r/{sub}: {state} · {link}")
    rt.Reddit.stop_server()


if __name__ == "__main__":
    main()
