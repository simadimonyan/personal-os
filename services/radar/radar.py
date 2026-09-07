#!/usr/bin/env python3
"""
radar — агрегатор инфополя Personal OS.

Собирает свежее из RSS-лент и Telegram-каналов, отбрасывает уже виденное,
сводит в дайджест трендов (через claude-local-api) и кладёт в Obsidian/Telegram.

Команды:
  ./radar.py run            — собрать и выдать дайджест (по настройкам sources.json)
  ./radar.py run --dry      — то же, но печать в консоль без доставки и без отметки seen
  ./radar.py sources        — показать настроенные источники
  ./radar.py discover-tg    — список твоих Telegram-каналов (для sources.json)
  ./radar.py test           — проверить, что RSS и TG-драйвер отвечают

Запуск по расписанию — через cron-сервис:
  cd ../cron && ./cronctl.py add infofield --schedule "0 9,21 * * *" \
      --shell "/usr/bin/python3 '$(pwd)/../radar/radar.py' run"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import analyze
import collectors
import store
from client import server_alive

BASE = Path(__file__).resolve().parent
SOURCES_FILE = BASE / "sources.json"
STATE_FILE = BASE / "state.json"
LOG_DIR = BASE / "logs"
LOG_FILE = LOG_DIR / "radar.log"
OBSIDIAN_DRIVER = Path.home() / ".claude" / "skills" / "obsidian" / "driver.mjs"
TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"
SEEN_CAP = 2000


def log(msg: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def load_sources() -> dict:
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8") or '{"seen": []}')


def save_state(state: dict) -> None:
    state["seen"] = state["seen"][-SEEN_CAP:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Сбор ─────────────────────────────────────────────────────────────────────
def collect(src: dict) -> tuple[list[dict], list[dict]]:
    settings = src.get("settings", {})
    cap = settings.get("max_per_source", 15)
    lookback = settings.get("lookback_hours", 24)
    full = settings.get("fetch_full", True)
    cutoff = time.time() - lookback * 3600

    rss_items, tg_items = [], []
    for feed in src.get("rss", []):
        try:
            got = collectors.fetch_rss(feed["name"], feed["url"], cap, feed.get("full", full))
            rss_items += [i for i in got if i["ts"] is None or i["ts"] >= cutoff]
        except Exception as e:  # noqa: BLE001
            log(f"⚠ RSS '{feed['name']}': {e}")
    for ch in src.get("telegram_channels", []):
        try:
            got = collectors.fetch_telegram(ch["name"], ch["chat"], cap)
            tg_items += [i for i in got if i["ts"] is None or i["ts"] >= cutoff]
        except Exception as e:  # noqa: BLE001
            log(f"⚠ TG '{ch['name']}': {e}")
    return rss_items, tg_items


# ── Доставка ─────────────────────────────────────────────────────────────────
def deliver_obsidian(path: str, content: str) -> None:
    payload = json.dumps({"path": path, "content": "\n" + content + "\n"}, ensure_ascii=False)
    subprocess.run(["node", str(OBSIDIAN_DRIVER), "obsidian_append", payload],
                   check=True, capture_output=True, text=True)


def deliver_telegram(chat: str, content: str, parse_mode: str = "html") -> None:
    payload = json.dumps({"chat_id": chat, "text": content, "parse_mode": parse_mode},
                         ensure_ascii=False)
    proc = subprocess.run(["node", str(TELEGRAM_DRIVER), "send_message", payload],
                          capture_output=True, text=True)
    if '"success": true' not in proc.stdout:
        raise RuntimeError((proc.stdout + proc.stderr)[:200])


# ── Команды ──────────────────────────────────────────────────────────────────
def cmd_run(args) -> None:
    src = load_sources()
    settings = src.get("settings", {})
    state = load_state()
    seen = set(state["seen"])

    rss_items, tg_items = collect(src)
    fresh = [i for i in (rss_items + tg_items) if i["uid"] not in seen]
    log(f"собрано: RSS {len(rss_items)}, TG {len(tg_items)} · новых {len(fresh)}")

    digest = analyze.build_digest(fresh, model=settings.get("model", "sonnet"))
    body = analyze.header(len(rss_items), len(tg_items)) + "\n\n" + digest

    if args.dry:
        print("\n" + body)
        return

    obs = settings.get("digest_obsidian")
    if obs:
        deliver_obsidian(obs, body)
        log(f"→ Obsidian: {obs}")
    tg = settings.get("digest_telegram")
    if tg:
        deliver_telegram(tg, body)
        log(f"→ Telegram: {tg}")
    if not obs and not tg:
        print("\n" + body)

    state["seen"].extend(i["uid"] for i in fresh)
    save_state(state)
    log("готово.")


def cmd_ingest(args) -> None:
    """Фоновый сбор RSS в SQLite с векторизацией. Сохраняет только новые материалы."""
    src = load_sources()
    settings = src.get("settings", {})
    cap = settings.get("max_per_source", 15)
    full = settings.get("fetch_full", True)
    conn = store.connect()
    # уже сохранённые ссылки — по ним за полным текстом повторно не ходим
    try:
        seen_uids = {row[0] for row in conn.execute("SELECT uid FROM items")}
    except Exception:  # noqa: BLE001
        seen_uids = set()

    collected: list[dict] = []
    for feed in src.get("rss", []):
        try:
            collected += collectors.fetch_rss(feed["name"], feed["url"], cap,
                                              feed.get("full", full), seen_uids)
        except Exception as e:  # noqa: BLE001
            log(f"⚠ RSS '{feed['name']}': {e}")
    for ch in src.get("telegram_channels", []):
        try:
            collected += collectors.fetch_telegram(ch["name"], ch["chat"], cap)
        except Exception as e:  # noqa: BLE001
            log(f"⚠ TG '{ch['name']}': {e}")

    have = store.existing_uids(conn, [i["uid"] for i in collected])
    fresh = [i for i in collected if i["uid"] not in have]
    # дедуп внутри одной пачки по uid
    seen, uniq = set(), []
    for i in fresh:
        if i["uid"] not in seen:
            seen.add(i["uid"])
            uniq.append(i)

    added = 0
    if uniq:
        texts = [f"{i['title']}. {i['text']}" for i in uniq]
        embs = store.embed(texts)
        added = store.add_items(conn, uniq, embs)

    if args.vectorize_pending:
        pend = store.pending(conn)
        if pend:
            embs = store.embed([f"{t}. {x}" for _, t, x in pend])
            for (row_id, _, _), blob in zip(pend, embs):
                store.set_embedding(conn, row_id, blob)
            log(f"векторизовано из бэклога: {len(pend)}")

    st = store.stats(conn)
    conn.close()
    log(f"ingest: собрано {len(collected)}, добавлено {added} · в базе {st['total']} "
        f"(вект. {st['embedded']}, ожидают {st['pending']}, {st['size_mb']}MB)")


def cmd_vectorize(args) -> None:
    """Догнать векторизацию для строк без эмбеддинга (фоновый проход)."""
    conn = store.connect()
    pend = store.pending(conn)
    if not pend:
        print("всё уже векторизовано.")
        return
    embs = store.embed([f"{t}. {x}" for _, t, x in pend])
    for (row_id, _, _), blob in zip(pend, embs):
        store.set_embedding(conn, row_id, blob)
    conn.close()
    log(f"векторизовано: {len(pend)}")


def cmd_search(args) -> None:
    conn = store.connect()
    res = store.search(conn, args.query, args.k)
    conn.close()
    if not res:
        print("ничего не найдено (база пуста? запусти ingest).")
        return
    for r in res:
        when = dt.datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M") if r["ts"] else "—"
        print(f"{r['score']:.3f}  [{r['source']}] {r['title']}  {when}\n        {r['url']}")


def cmd_dbstats(args) -> None:
    conn = store.connect()
    st = store.stats(conn)
    conn.close()
    print(f"radar.db: {st['total']} новостей · векторизовано {st['embedded']} · "
          f"ожидают {st['pending']} · {st['size_mb']}MB")
    for src_name, n in st["by_source"]:
        print(f"  {src_name:22} {n}")


def cmd_post_digest(args) -> None:
    """Сгенерировать посты в стиле канала (1 новость = 1 пост) и опубликовать."""
    import channel_digest

    conn = store.connect()
    items = store.recent(conn, hours=args.hours, limit=40)
    conn.close()
    posts = channel_digest.build_posts(items, n=args.n, model=args.model, channel=args.channel)

    if args.dry:
        sep = "\n\n" + "─" * 40 + "\n\n"
        print(sep.join(posts))
        return

    for i, post in enumerate(posts, 1):
        deliver_telegram(args.channel, post)
        log(f"пост {i}/{len(posts)} → {args.channel} ({len(post)} символов)")
        if i < len(posts):
            time.sleep(6)  # пауза между постами, чтобы не частить
    log(f"опубликовано постов: {len(posts)} → {args.channel}")


def cmd_draft_posts(args) -> None:
    """Сгенерировать посты в стиле канала и выдать JSON-массив (для бота-редактора).

    Каждый элемент: {"text": "<html-пост>", "url": "<ссылка на источник>"}.
    Ничего не публикует — только возвращает черновики на ревью.
    """
    import channel_digest

    conn = store.connect()
    items = store.recent(conn, hours=args.hours, limit=60)
    conn.close()
    # исключить уже использованные истории. По умолчанию — память бота-редактора
    # (published.json): так дедуп работает даже при прямом вызове радара SMM-агентом.
    exclude_path = args.exclude_file or str(
        BASE.parent / "tg-blog-editor" / "data" / "published.json")
    exclude: set[str] = set()
    p = Path(exclude_path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for x in data:
                if isinstance(x, str):
                    exclude.add(x)
                elif isinstance(x, dict) and x.get("url"):
                    exclude.add(x["url"])
        except (json.JSONDecodeError, OSError):
            exclude = set()
    if exclude:
        items = [it for it in items if (it.get("url") or "") not in exclude]
    objs = channel_digest.build_post_objects(items, n=args.n, model=args.model,
                                              channel=args.channel)
    print(json.dumps(objs, ensure_ascii=False))


def cmd_sources(args) -> None:
    src = load_sources()
    print("Настройки:", json.dumps(src.get("settings", {}), ensure_ascii=False))
    print(f"\nRSS ({len(src.get('rss', []))}):")
    for f in src.get("rss", []):
        print(f"  - {f['name']:22} {f['url']}")
    print(f"\nTelegram ({len(src.get('telegram_channels', []))}):")
    for c in src.get("telegram_channels", []):
        print(f"  - {c['name']:22} {c['chat']}")


def cmd_discover_tg(args) -> None:
    chans = collectors.discover_channels()
    print(f"Твои каналы ({len(chans)}). Скопируй нужные в sources.json → telegram_channels:\n")
    for c in chans:
        print(json.dumps({"name": c["name"], "chat": c["chat"]}, ensure_ascii=False))


def cmd_test(args) -> None:
    src = load_sources()
    print(f"claude-local-api: {'🟢' if server_alive() else '🔴 (будет сырой дайджест)'}")
    for f in src.get("rss", [])[:3]:
        try:
            n = len(collectors.fetch_rss(f["name"], f["url"], 5))
            print(f"RSS 🟢 {f['name']}: {n} items")
        except Exception as e:  # noqa: BLE001
            print(f"RSS 🔴 {f['name']}: {e}")
    for c in src.get("telegram_channels", [])[:2]:
        try:
            n = len(collectors.fetch_telegram(c["name"], c["chat"], 5))
            print(f"TG  🟢 {c['name']}: {n} msgs")
        except Exception as e:  # noqa: BLE001
            print(f"TG  🔴 {c['name']}: {e}")


def main() -> None:
    p = argparse.ArgumentParser(prog="radar", description="Агрегатор инфополя Personal OS")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run", help="собрать и выдать дайджест")
    pr.add_argument("--dry", action="store_true", help="печать в консоль, без доставки и без отметки seen")
    pr.set_defaults(func=cmd_run)

    pi = sub.add_parser("ingest", help="фоновый сбор RSS в SQLite с векторизацией")
    pi.add_argument("--vectorize-pending", action="store_true",
                    help="заодно догнать невекторизованные строки")
    pi.set_defaults(func=cmd_ingest)

    sub.add_parser("vectorize", help="догнать векторизацию ожидающих строк").set_defaults(func=cmd_vectorize)

    ps = sub.add_parser("search", help="семантический поиск по базе новостей")
    ps.add_argument("query")
    ps.add_argument("k", nargs="?", type=int, default=10)
    ps.set_defaults(func=cmd_search)

    pd = sub.add_parser("post-digest", help="опубликовать дайджест в стиле канала")
    pd.add_argument("--channel", default="@digit_code", help="куда публиковать (по умолчанию @digit_code)")
    pd.add_argument("--n", type=int, default=5, help="сколько новостей в дайджесте")
    pd.add_argument("--hours", type=int, default=24, help="за сколько часов брать материалы")
    pd.add_argument("--model", default="sonnet", help="модель claude-local-api")
    pd.add_argument("--dry", action="store_true", help="показать, не публикуя")
    pd.set_defaults(func=cmd_post_digest)

    pdr = sub.add_parser("draft-posts", help="вернуть посты как JSON (для бота-редактора), без публикации")
    pdr.add_argument("--channel", default="@digit_code", help="канал для футера-ссылки")
    pdr.add_argument("--n", type=int, default=5, help="сколько постов")
    pdr.add_argument("--hours", type=int, default=24, help="за сколько часов брать материалы")
    pdr.add_argument("--model", default="sonnet", help="модель claude-local-api")
    pdr.add_argument("--exclude-file", default="", help="JSON-файл со списком уже использованных url (дедуп)")
    pdr.set_defaults(func=cmd_draft_posts)

    sub.add_parser("db-stats", help="статистика базы новостей").set_defaults(func=cmd_dbstats)
    sub.add_parser("sources", help="показать источники").set_defaults(func=cmd_sources)
    sub.add_parser("discover-tg", help="список твоих каналов").set_defaults(func=cmd_discover_tg)
    sub.add_parser("test", help="проверка источников").set_defaults(func=cmd_test)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
