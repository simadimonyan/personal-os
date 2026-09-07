#!/usr/bin/env python3
"""
Источники — единая база потребляемого контента (origin-tagged).

Физически это та же ``tools/zotero/zotero_vectors.db``: Zotero-книги и выделения
живут с ``origin='zotero'``, а ссылки из сообществ/веба добавляются сюда же с
``origin='community'`` (или любым другим). Единое смысловое пространство с
Obsidian и Zotero — граф, ``search-all`` и кросс-поиск работают без изменений,
просто теперь у каждого источника есть пометка, откуда он.

URL трактуется как «книга»: строка в ``books`` (item_type='webpage', file_path=url)
+ чанки полного текста в ``chunks``. Эмбеддинги — той же моделью, что Zotero.

Команды:
  migrate                                  — добавить колонку origin (idempotent)
  add-url <url> [--origin X] [--title T]   — один источник
  ingest-tg <chat_id> [--origin community] [--limit N]
                                           — вытащить ссылки из Telegram-чата
  ingest-zotero-links [--no-fetch]         — записи Zotero без PDF (webpage/video/
                                             blogPost) как ресурсы в Zotero-базу
  retitle [--dry]                          — починить мусорные заголовки веб-
                                             источников (метка из URL, без re-fetch)
  graph [--thr 0.55] [--top 6]             — граф базы (раскраска по origin)
  stats                                    — разбивка базы по origin

Переиспользует: tools/zotero/vectorize.py (embed, chunks, БД) и
services/radar/collectors.py (fetch_article).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools" / "zotero"))
sys.path.insert(0, str(REPO / "services" / "radar"))

import vectorize as zv        # embed, open_db, chunk_pages, pack_embedding, MAX_CHUNKS_PER_BOOK  # noqa: E402
import collectors as rc       # fetch_article  # noqa: E402

TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
MIN_TEXT = 200                # ниже — считаем страницу пустышкой/заглушкой


# ── миграция ────────────────────────────────────────────────────────────────
def migrate() -> None:
    db = zv.open_db()
    cols = [r[1] for r in db.execute("PRAGMA table_info(books)")]
    if "origin" not in cols:
        db.execute("ALTER TABLE books ADD COLUMN origin TEXT DEFAULT 'zotero'")
        db.execute("UPDATE books SET origin='zotero' WHERE origin IS NULL")
        db.commit()
        print("migrate: колонка origin добавлена (существующие строки = zotero)")
    else:
        print("migrate: origin уже есть")


# ── добавление одного URL ─────────────────────────────────────────────────────
def _source_id(url: str) -> str:
    return "src:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def add_url(url: str, origin: str = "community", title: str | None = None,
            tags: str = "") -> bool:
    text = rc.fetch_article(url, 40000)
    if not text or len(text) < MIN_TEXT:
        print(f"  ⚠ {url}: пусто/мало текста ({len(text or '')} симв.) — пропуск")
        return False
    # Метка источника — из самой ссылки (домен + путь), а не из случайной первой
    # строки текста: так источник всегда опознаётся по URL. Заголовок из HTML
    # тут недоступен (fetch_article отдаёт только текст), а первая строка обычно
    # мусор (ник+дата, «Provide feedback», навигация).
    title = title or zv._url_label(url)

    db = zv.open_db()
    sid = _source_id(url)
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    prev = db.execute("SELECT content_hash FROM books WHERE book_id=?", (sid,)).fetchone()
    db.execute(
        """INSERT INTO books(book_id,title,authors,year,item_type,tags,
                             file_path,content_hash,indexed_at,origin)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(book_id) DO UPDATE SET
             title=excluded.title, tags=excluded.tags,
             content_hash=excluded.content_hash,
             indexed_at=excluded.indexed_at, origin=excluded.origin""",
        (sid, title, None, None, "webpage", tags, url, h, time.time(), origin),
    )
    if prev and prev[0] == h:
        db.commit()
        print(f"  = [{origin}] {title[:50]} (без изменений)")
        return True

    batch = list(zv.chunk_pages([(1, text)]))[:zv.MAX_CHUNKS_PER_BOOK]
    vecs = zv.embed([c for c, _, _ in batch])
    db.execute("DELETE FROM chunks WHERE book_id=?", (sid,))
    for i, ((chunk, p0, p1), vec) in enumerate(zip(batch, vecs)):
        db.execute(
            """INSERT INTO chunks(book_id,chunk_idx,content,page_start,page_end,
                                  hash,embedding)
               VALUES(?,?,?,?,?,?,?)""",
            (sid, i, chunk, p0, p1,
             hashlib.sha1(chunk.encode()).hexdigest(), zv.pack_embedding(vec)),
        )
    db.commit()
    zv._invalidate_cache()
    print(f"  ✓ [{origin}] {title[:50]} ({len(batch)} чанков)")
    return True


# ── Zotero-ссылки (записи без PDF) → в Zotero-базу как ресурсы ─────────────────
def ingest_zotero_links(fetch_text: bool = True) -> None:
    """Индексирует записи Zotero БЕЗ PDF — webpage / videoRecording / blogPost —
    как ресурсы в самой Zotero-базе (origin='zotero'). Штатный индексатор берёт
    только PDF, поэтому эти ссылки-записи никуда не попадали, хотя они — часть
    библиотеки. Заголовок и URL берём из Zotero (они там нормальные).

    web/blog — забираем текст страницы и чанкуем (смысловой поиск по содержимому);
    видео — сохраняем как узел-ссылку с заголовком (транскрипта нет). Идемпотентно.
    """
    z = zv.open_zotero()
    rows = z.execute(
        """SELECT i.itemID, i.key, it.typeName FROM items i
             JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
              AND it.typeName IN ('webpage','videoRecording','blogPost')"""
    ).fetchall()
    db = zv.open_db()
    now = time.time()
    ok = 0
    for item_id, key, itype in rows:
        m = zv.book_meta(z, item_id)
        url = zv.field_value(z, item_id, "url")
        title = m["title"] or url or key
        if not url or not url.startswith("http"):
            print(f"  ⚠ {title[:40]}: нет URL — пропуск")
            continue
        text = ""
        if fetch_text and itype in ("webpage", "blogPost"):
            text = rc.fetch_article(url, 40000) or ""
        # чанки: из текста, если он есть; иначе один чанк из заголовка+URL,
        # чтобы запись была узлом графа и находилась поиском по названию.
        if text and len(text) >= MIN_TEXT:
            batch = list(zv.chunk_pages([(1, text)]))[:zv.MAX_CHUNKS_PER_BOOK]
        else:
            batch = [(f"{title}\n{url}", 1, 1)]
        h = hashlib.sha1((text or url).encode("utf-8")).hexdigest()
        db.execute(
            """INSERT INTO books(book_id,title,authors,year,item_type,tags,
                                 file_path,content_hash,indexed_at,origin)
               VALUES(?,?,?,?,?,?,?,?,?,'zotero')
               ON CONFLICT(book_id) DO UPDATE SET
                 title=excluded.title, item_type=excluded.item_type,
                 file_path=excluded.file_path, content_hash=excluded.content_hash,
                 indexed_at=excluded.indexed_at, origin='zotero'""",
            (key, title, m["authors"], m["year"], itype, m["tags"],
             url, h, now),
        )
        vecs = zv.embed([c for c, _, _ in batch])
        db.execute("DELETE FROM chunks WHERE book_id=?", (key,))
        for i, ((chunk, p0, p1), vec) in enumerate(zip(batch, vecs)):
            db.execute(
                """INSERT INTO chunks(book_id,chunk_idx,content,page_start,page_end,
                                      hash,embedding)
                   VALUES(?,?,?,?,?,?,?)""",
                (key, i, chunk, p0, p1,
                 hashlib.sha1(chunk.encode()).hexdigest(), zv.pack_embedding(vec)),
            )
        ok += 1
        mark = "📄" if text else "🔗"
        print(f"  {mark} [{itype}] {title[:46]} ({len(batch)} чанк.)")
    db.commit()
    zv._invalidate_cache()
    print(f"ingest-zotero-links: {ok}/{len(rows)} ссылок-записей Zotero "
          f"в библиотеке (origin='zotero')")
    if ok:
        print("  → перестрой граф библиотеки: python3 tools/zotero/vectorize.py graph")


# ── бэкфилл заголовков существующих источников (без re-fetch) ─────────────────
def retitle(dry: bool = False) -> None:
    """Чинит мусорные заголовки уже загруженных веб-источников, беря читаемую
    метку из самой ссылки. Ничего не качает заново и не трогает чанки —
    правит только books.title. Идемпотентно."""
    db = zv.open_db()
    cols = [r[1] for r in db.execute("PRAGMA table_info(books)")]
    if "origin" not in cols:
        print("origin не мигрирован — сначала: ingest.py migrate")
        return
    rows = db.execute(
        """SELECT book_id,title,file_path FROM books
            WHERE COALESCE(origin,'zotero')!='zotero'
              AND file_path LIKE 'http%'"""
    ).fetchall()
    fixed = 0
    for bid, title, url in rows:
        if not zv._is_junk_title(title, url):
            continue
        new = zv._url_label(url)
        if new == (title or ""):
            continue
        if fixed < 12:
            print(f"  {(title or '∅')[:34]!r:36} → {new}")
        if not dry:
            db.execute("UPDATE books SET title=? WHERE book_id=?", (new, bid))
        fixed += 1
    if not dry:
        db.commit()
    print(f"retitle: {'[dry] ' if dry else ''}исправлено {fixed}/{len(rows)} "
          f"веб-источников · заголовок теперь из ссылки")
    if fixed and not dry:
        print("  → перестрой граф: ingest.py graph")


# ── Telegram-чат → ссылки ─────────────────────────────────────────────────────
def _tg(tool: str, args: dict) -> object:
    """Драйвер сыплет gramjs-логи в stdout перед JSON — берём первый валидный
    JSON-объект/массив через raw_decode."""
    proc = subprocess.run(
        ["node", str(TELEGRAM_DRIVER), tool, json.dumps(args)],
        capture_output=True, text=True, timeout=180,
    )
    return _first_json(proc.stdout, proc.stderr)


def _first_json(out: str, err: str = "") -> object:
    """Достаём финальный результат драйвера из зашумлённого stdout.

    gramjs при больших лимитах льёт ANSI-логи И промежуточные объекты сообщений
    ДО итогового JSON, поэтому «первый валидный JSON» ловит одиночный объект, а не
    массив-результат. Берём кандидата, который тянется дальше всех в вывод —
    итоговый массив печатается последним и охватывает свои элементы, значит имеет
    наибольший конец. Так устойчиво отличаем результат от логов и вложенных строк.
    """
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
        raise RuntimeError(f"telegram driver: не JSON: {(out + err)[:200]}")
    return best


def ingest_tg(chat_id: str, origin: str = "community", limit: int = 500) -> None:
    msgs = _tg("get_chat_history", {"chat_id": chat_id, "limit": limit})
    urls: list[str] = []
    seen = set()
    for m in msgs if isinstance(msgs, list) else []:
        for u in URL_RE.findall(str(m.get("text") or m.get("message") or "")):
            u = u.rstrip(".,);]")
            if u not in seen and "t.me/" not in u:   # свои же tg-ссылки пропускаем
                seen.add(u)
                urls.append(u)
    print(f"ingest-tg {chat_id}: {len(msgs)} сообщений · {len(urls)} уникальных ссылок")
    ok = 0
    for u in urls:
        if add_url(u, origin=origin):
            ok += 1
    print(f"ingest-tg: добавлено/обновлено {ok}/{len(urls)}")


# ── граф базы источников (раскраска по origin) ────────────────────────────────
GRAPH_DIR = HERE / "graph"


def build_graph(threshold: float = 0.55, top_k: int = 6) -> None:
    """Семантический граф базы источников: узлы = собранные ССЫЛКИ (сообщества,
    веб, блог), раскрашены по origin. Zotero-книги исключены — под источниками
    имеются в виду ссылки, а не библиотека; у неё свой граф (tools/zotero/graph).
    Пишет в tools/sources/graph/."""
    zv.cmd_build_graph(threshold, top_k, color_by="origin", outdir=GRAPH_DIR,
                       exclude_origins={"zotero"})


# ── статистика ────────────────────────────────────────────────────────────────
def stats() -> None:
    db = zv.open_db()
    cols = [r[1] for r in db.execute("PRAGMA table_info(books)")]
    if "origin" not in cols:
        print("origin ещё не мигрирован — запусти: ingest.py migrate")
        return
    print("Источники по origin:")
    for origin, nb, nc in db.execute(
        """SELECT COALESCE(b.origin,'zotero') o, COUNT(DISTINCT b.book_id),
                  (SELECT COUNT(*) FROM chunks c WHERE c.book_id IN
                     (SELECT book_id FROM books b2 WHERE COALESCE(b2.origin,'zotero')=COALESCE(b.origin,'zotero')))
             FROM books b GROUP BY o ORDER BY 2 DESC"""):
        print(f"  {origin:12s} книг/источников: {nb:4d}  чанков: {nc}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    args = sys.argv[2:]

    def opt(flag, default=None):
        return args[args.index(flag) + 1] if flag in args else default

    if cmd == "migrate":
        migrate()
    elif cmd == "add-url":
        add_url(args[0], origin=opt("--origin", "community"),
                title=opt("--title"), tags=opt("--tags", ""))
    elif cmd == "ingest-tg":
        ingest_tg(args[0], origin=opt("--origin", "community"),
                  limit=int(opt("--limit", "500")))
    elif cmd == "ingest-zotero-links":
        ingest_zotero_links(fetch_text="--no-fetch" not in args)
    elif cmd == "retitle":
        retitle(dry="--dry" in args)
    elif cmd == "graph":
        build_graph(float(opt("--thr", "0.55")), int(opt("--top", "6")))
    elif cmd == "stats":
        stats()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
