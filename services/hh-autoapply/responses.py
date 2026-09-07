#!/usr/bin/env python3
"""
Монитор ответов работодателей на hh.ru.

Аналог check_chats из reference-проекта, но без своего Telegram-бота: отчёт
уходит в stdout, а кроновский приёмник сам доставит его в Telegram/Obsidian.

Смысл: автоотклик отправляет десятки писем в день, и приглашение легко
утонуть в списке. Скрипт запоминает статус каждого отклика в history.db и
сообщает только про изменения — приглашения и отказы, появившиеся с прошлого
запуска. Первый прогон молча заполняет базу, чтобы не выдать 226 «новых».

Запуск:  python3 responses.py          # только изменения
         python3 responses.py --all    # весь список откликов
"""
from __future__ import annotations

import sys

import history
from hhdriver import driver

# Статусы hh пишет человеческим текстом — классифицируем по подстрокам.
GOOD = ("приглаш", "интерв", "собеседов", "оффер", "тестовое")
BAD = ("отказ", "не подош", "отклон")


def classify(status: str) -> str:
    s = (status or "").lower()
    if any(w in s for w in GOOD):
        return "invite"
    if any(w in s for w in BAD):
        return "reject"
    return "pending"


def key_of(app: dict) -> str:
    return f"{(app.get('company') or '').strip()}|{(app.get('title') or '').strip()}"


def main() -> None:
    show_all = "--all" in sys.argv

    if driver("hh_check_auth", tries=3) is not True:
        raise SystemExit("hh: нет авторизации — нужно войти (hh_login) и сохранить сессию")

    apps = driver("hh_get_applications", tries=3)
    if not isinstance(apps, list) or not apps:
        print("hh-ответы: список откликов пуст или не загрузился")
        return

    conn = history.connect()
    known = {r["key"]: r["status"] for r in conn.execute("SELECT key, status FROM negotiations")}
    first_run = not known

    changed: list[tuple[dict, str]] = []
    for app in apps:
        k = key_of(app)
        status = (app.get("status") or "").strip()
        kind = classify(status)
        is_new = known.get(k) != status
        if is_new:
            changed.append((app, kind))
        # announced=0 только на изменившихся: об этом ещё не говорили в Telegram.
        # На первом прогоне ставим 1 — иначе бот вывалит все 226 откликов разом.
        announced = 0 if (is_new and not first_run) else 1
        conn.execute(
            """INSERT INTO negotiations (key, title, company, status, url, kind, announced, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 status=excluded.status, url=excluded.url, kind=excluded.kind,
                 announced=CASE WHEN negotiations.status IS excluded.status
                                THEN negotiations.announced ELSE excluded.announced END,
                 updated_at=CURRENT_TIMESTAMP""",
            (k, app.get("title"), app.get("company"), status, app.get("url"), kind, announced),
        )
    conn.commit()

    if first_run:
        print(f"hh-ответы: первый прогон — зафиксировано {len(apps)} откликов, "
              f"со следующего раза буду сообщать только про изменения")
        return

    invites = [a for a, kind in changed if kind == "invite"]
    rejects = [a for a, kind in changed if kind == "reject"]

    for a in invites:
        print(f"  🎯 ПРИГЛАШЕНИЕ: {a.get('title')} — {a.get('company')} ({a.get('status')})\n"
              f"     {a.get('url')}")
    for a in rejects:
        print(f"  ❌ отказ: {a.get('title')} — {a.get('company')}")
    for a, kind in changed:
        if kind == "pending":
            print(f"  · движение: {a.get('title')} — {a.get('company')}: {a.get('status')}")

    if show_all:
        print("\n— все отклики —")
        for a in apps:
            print(f"  {a.get('status') or '—'} | {a.get('title')} — {a.get('company')}")

    if not changed:
        print(f"hh-ответы: без изменений, всего откликов на hh {len(apps)}")
    else:
        print(f"hh-ответы: приглашений {len(invites)}, отказов {len(rejects)}, "
              f"прочих изменений {len(changed) - len(invites) - len(rejects)}, "
              f"всего откликов на hh {len(apps)}")


if __name__ == "__main__":
    main()
