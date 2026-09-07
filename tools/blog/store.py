#!/usr/bin/env python3
"""
Блог — отдельная база собственного контента Димитри (статьи, посты, объявления)
с прицелом на публикацию в разные соцсети из одного места.

Это НЕ база источников (то, что читаешь) — это твой ОПУБЛИКОВАННЫЙ и черновой
контент. Одна статья → много публикаций в разных сетях, каждая со своим статусом
и ссылкой. Отсюда потом переупаковка под площадку и трекинг, где что вышло.

Схема (blog.db):
  articles      — единица контента (заголовок + тело + формат + теги + origin)
  publications  — куда ушла статья: сеть, статус, remote_url, дата
  networks      — зарегистрированные площадки (telegram/vk/...), вкл/выкл + конфиг

Костяк: хранилище + CLI. Реальные адаптеры публикации — в publish.py (пока стабы,
кроме telegram, который может опереться на существующий tg-blog-editor/канал).

CLI:
  init
  add-article --title T --body-file F [--format md] [--tags a,b] [--origin manual]
  import-tg <channel> [--limit N] [--mine-only]   — втянуть свои посты из TG-канала
  list [--status draft|published]
  networks                                          — список площадок
  add-network <name> [--enabled]
  publish <article_id> <network>                    — (стаб) отметить/выполнить публикацию
  stats
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "blog.db"
TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"

# площадки, известные системе с самого начала (config пустой — заполняется позже)
DEFAULT_NETWORKS = ["telegram", "vk", "dzen", "telegraph", "x", "linkedin"]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY,
            title       TEXT,
            body        TEXT,
            format      TEXT DEFAULT 'md',
            tags        TEXT,
            origin      TEXT DEFAULT 'manual',   -- manual | digit_code | import | ...
            source_url  TEXT,
            created_at  REAL,
            updated_at  REAL
        );
        CREATE TABLE IF NOT EXISTS networks (
            name        TEXT PRIMARY KEY,
            enabled     INTEGER DEFAULT 0,
            config_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS publications (
            id           INTEGER PRIMARY KEY,
            article_id   INTEGER REFERENCES articles(id),
            network      TEXT REFERENCES networks(name),
            status       TEXT DEFAULT 'draft',   -- draft | published | failed
            remote_url   TEXT,
            published_at REAL,
            UNIQUE(article_id, network)
        );
        CREATE INDEX IF NOT EXISTS idx_pub_article ON publications(article_id);
        """
    )
    return conn


def init() -> None:
    conn = db()
    for n in DEFAULT_NETWORKS:
        conn.execute("INSERT OR IGNORE INTO networks(name,enabled) VALUES(?,0)", (n,))
    conn.commit()
    print(f"init: blog.db готова · площадки-заготовки: {', '.join(DEFAULT_NETWORKS)}")


def add_article(title: str, body: str, fmt: str = "md", tags: str = "",
                origin: str = "manual", source_url: str | None = None) -> int:
    conn = db()
    now = time.time()
    cur = conn.execute(
        """INSERT INTO articles(title,body,format,tags,origin,source_url,
                                created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (title, body, fmt, tags, origin, source_url, now, now),
    )
    conn.commit()
    print(f"add-article: #{cur.lastrowid} «{(title or '?')[:50]}» [{origin}]")
    return cur.lastrowid


# ── импорт своих постов из Telegram-канала ────────────────────────────────────
def _tg(tool: str, args: dict) -> object:
    proc = subprocess.run(["node", str(TELEGRAM_DRIVER), tool, json.dumps(args)],
                          capture_output=True, text=True, timeout=180)
    # gramjs при больших лимитах льёт логи И промежуточные объекты сообщений ДО
    # итогового массива, поэтому «первый валидный JSON» ловит одиночный объект.
    # Берём кандидата, который тянется дальше всех в вывод — итоговый массив
    # печатается последним и охватывает свои элементы (наибольший конец).
    out = proc.stdout
    dec = json.JSONDecoder()
    best, best_end = None, -1
    for m in re.finditer(r"[\[{]", out):
        try:
            obj, consumed = dec.raw_decode(out[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, (list, dict)) and m.start() + consumed > best_end:
            best, best_end = obj, m.start() + consumed
    if best is None:
        raise RuntimeError(f"telegram driver: не JSON: {(out + proc.stderr)[:200]}")
    return best


def import_tg(channel: str, limit: int = 300, mine_only: bool = True) -> None:
    msgs = _tg("get_chat_history", {"chat_id": channel, "limit": limit})
    added = 0
    for m in msgs if isinstance(msgs, list) else []:
        text = str(m.get("text") or m.get("message") or "").strip()
        if len(text) < 40:                     # пропускаем реплики/односложные
            continue
        # заголовок — первая строка, тело — остальное
        first, *_ = text.splitlines()
        add_article(title=first[:120], body=text, fmt="md",
                    origin="digit_code" if channel.lstrip("@") == "digit_code" else "import",
                    source_url=m.get("url"))
        added += 1
    print(f"import-tg {channel}: {len(msgs)} сообщений → {added} статей")


def list_articles(status: str | None = None) -> None:
    conn = db()
    rows = conn.execute(
        """SELECT a.id,a.title,a.origin,
                  (SELECT GROUP_CONCAT(network||':'||status,', ')
                     FROM publications p WHERE p.article_id=a.id)
             FROM articles a ORDER BY a.created_at DESC LIMIT 50"""
    ).fetchall()
    for aid, title, origin, pubs in rows:
        print(f"  #{aid:<4} [{origin:10}] {(title or '?')[:52]:52}  {pubs or '—'}")
    if not rows:
        print("  (пусто — add-article / import-tg)")


def networks() -> None:
    conn = db()
    for name, en, cfg in conn.execute("SELECT name,enabled,config_json FROM networks ORDER BY name"):
        print(f"  {'🟢' if en else '⚪️'} {name:10} {cfg}")


def add_network(name: str, enabled: bool = False) -> None:
    conn = db()
    conn.execute("INSERT OR IGNORE INTO networks(name,enabled) VALUES(?,?)", (name, int(enabled)))
    conn.execute("UPDATE networks SET enabled=? WHERE name=?", (int(enabled), name))
    conn.commit()
    print(f"add-network: {name} · {'вкл' if enabled else 'выкл'}")


def publish(article_id: int, network: str) -> None:
    """Костяк: помечает намерение публикации и зовёт адаптер из publish.py.
    Реальные адаптеры (кроме заглушек) появятся по мере подключения площадок."""
    conn = db()
    art = conn.execute("SELECT id,title,body FROM articles WHERE id=?", (article_id,)).fetchone()
    if not art:
        sys.exit(f"нет статьи #{article_id}")
    net = conn.execute("SELECT name,enabled FROM networks WHERE name=?", (network,)).fetchone()
    if not net:
        sys.exit(f"нет площадки {network} — сперва add-network {network}")
    try:
        from publish import get_publisher            # локальный импорт костяка
    except Exception:
        sys.path.insert(0, str(HERE))
        from publish import get_publisher
    pub = get_publisher(network)
    remote_url, status = pub.publish({"id": art[0], "title": art[1], "body": art[2]})
    conn.execute(
        """INSERT INTO publications(article_id,network,status,remote_url,published_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(article_id,network) DO UPDATE SET
             status=excluded.status, remote_url=excluded.remote_url,
             published_at=excluded.published_at""",
        (article_id, network, status, remote_url, time.time()),
    )
    conn.commit()
    print(f"publish: #{article_id} → {network}: {status} {remote_url or ''}")


def stats() -> None:
    conn = db()
    na = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"Блог: статей {na}")
    for net, c in conn.execute(
        "SELECT network,COUNT(*) FROM publications WHERE status='published' GROUP BY network"):
        print(f"  опубликовано в {net}: {c}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    args = sys.argv[2:]

    def opt(flag, default=None):
        return args[args.index(flag) + 1] if flag in args else default

    if cmd == "init":
        init()
    elif cmd == "add-article":
        body = Path(opt("--body-file")).read_text(encoding="utf-8") if opt("--body-file") else (opt("--body") or "")
        add_article(opt("--title", ""), body, fmt=opt("--format", "md"),
                    tags=opt("--tags", ""), origin=opt("--origin", "manual"))
    elif cmd == "import-tg":
        import_tg(args[0], limit=int(opt("--limit", "300")),
                  mine_only="--mine-only" in args)
    elif cmd == "list":
        list_articles(opt("--status"))
    elif cmd == "networks":
        networks()
    elif cmd == "add-network":
        add_network(args[0], enabled="--enabled" in args)
    elif cmd == "publish":
        publish(int(args[0]), args[1])
    elif cmd == "stats":
        stats()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
