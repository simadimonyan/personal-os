#!/usr/bin/env python3
"""
Zotero -> vector index.

MVP: books catalog + annotations (your highlights) with embeddings.
Chunks table is created up front but filled by a later step (full PDF text).

Reuses the SAME embedding model as obsidian_vsearch.py, so annotations, book
chunks and Obsidian notes all live in one semantic space (cross-search works).

Commands:
  index                build/update `books` + `annotations` (incremental by hash)
  index-chunks         extract full PDF text -> `chunks` (incremental, skips scans)
  ocr-scans            OCR scanned PDFs (no text layer) via tesseract rus+eng
  graph [thr] [k]      build book-level semantic map -> graph/graph.html
  search <query> [n]   semantic search over annotations
  search-all <q> [n]   semantic search over annotations + chunks (boosts annotations)
  stats                row counts

Env:
  ZOTERO_DATA_DIR   Zotero data folder (default: real library on Yandex.Disk)
"""
import os
import re
import sys
import json
import time
import struct
import shutil
import sqlite3
import hashlib
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, unquote

HERE = Path(__file__).resolve().parent


# --- Читаемая метка и детектор мусорных заголовков веб-источников ------------
# Веб-страница хранится как «книга» (item_type='webpage', file_path=url). Часто
# из текста извлекается мусорный заголовок (ник+дата с habr, «Provide feedback»,
# «Documentation Index», «12.1. Introduction»). Тогда метка строится из самой
# ссылки — источник всегда опознаётся по URL, а не по случайной первой строке.
_LANG_SEG = {"ru", "en", "de", "fr", "es", "it", "ua", "uk", "m", "www"}
_JUNK_TITLES = {
    "provide feedback", "documentation index", "table of contents",
    "about this documentation", "data activism", "documentation",
    "readme", "readme.md", "introduction", "index", "home", "untitled",
    "documentation home", "docs", "blog", "articles",
}


def _url_label(url: str) -> str:
    """domain — последние осмысленные сегменты пути (без языка и расширений)."""
    try:
        p = urlsplit(url)
    except Exception:  # noqa: BLE001
        return (url or "")[:120]
    host = re.sub(r"^www\.", "", (p.netloc or "").lower())
    segs: list[str] = []
    for raw in p.path.split("/"):
        s = unquote(raw).strip()
        if not s or s.lower() in _LANG_SEG:
            continue
        s = re.sub(r"\.(html?|php|aspx?|md|txt)$", "", s, flags=re.I)
        s = s.replace("-", " ").replace("_", " ").strip()
        if not s or (segs and segs[-1] == s):   # пропуск пустых и повторов подряд
            continue
        segs.append(s)
    if not segs:
        return host or (url or "")[:120]
    return f"{host} — {' '.join(segs[-2:])}"[:120]


def _is_junk_title(title: str | None, url: str | None) -> bool:
    """Заголовок бесполезен для опознания источника → метку берём из URL."""
    if not title or not title.strip():
        return True
    t = title.strip()
    if url and t == url:
        return True
    if t.lower() in _JUNK_TITLES:
        return True
    if re.search(r"\d{1,2}\s+\S+\s+\d{4}", t):     # «geoolekom 12 мар 2019 …»
        return True
    if re.match(r"^\d+(\.\d+)*\.?\s", t):           # «12.1. Introduction»
        return True
    return len(t) < 6

# --- Shared model (already downloaded ~915MB in the Obsidian tool cache) ---
MODEL_CACHE = HERE.parent / "obsidian" / "tools" / "models"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
# Форсируем локальный кэш модели (как obsidian_vsearch): под сервером/launchd в
# окружении может быть свой HF_HOME → setdefault не переопределит и модель уйдёт
# качаться в сеть (под VPN — виснет намертво). Плюс offline: никаких обращений к
# hub, только локальный кэш.
os.environ["HF_HOME"] = str(MODEL_CACHE)
os.environ["TRANSFORMERS_CACHE"] = str(MODEL_CACHE)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

DB_PATH = HERE / "zotero_vectors.db"

CHUNK_SIZE = 800          # chars, matches obsidian_vsearch for one shared space
CHUNK_OVERLAP = 100
MAX_CHUNKS_PER_BOOK = 4000
MIN_CHARS_PER_PAGE = 40   # below this avg -> treat PDF as scanned (no text layer)

ZOTERO_DATA_DIR = os.environ.get(
    "ZOTERO_DATA_DIR",
    str(Path.home() / "Yandex.Disk.localized" / "Self-Education"
        / "Knowledge base" / "Zotero"),
)
ZOTERO_DB = Path(ZOTERO_DATA_DIR) / "zotero.sqlite"


# --- Embeddings (binary-compatible with obsidian_vectors.db) ---
def pack_embedding(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine(a, b):
    # embeddings are L2-normalized -> dot product == cosine
    return sum(x * y for x, y in zip(a, b))


# --- Fast search via a cached numpy matrix (sqlite3 here lacks load_extension,
#     so sqlite-vec is out; numpy matmul over ~83k vecs is <100ms anyway) ---
def _matrix(db, table):
    """Return (ids: np.int64[N], mat: np.float32[N,384]) from cache, rebuilt
    lazily when the row count changes."""
    import numpy as np
    npy = HERE / f"_{table}.npy"
    ids_npy = HERE / f"_{table}_ids.npy"
    n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if npy.exists() and ids_npy.exists():
        mat = np.load(npy, mmap_mode="r")
        if mat.shape[0] == n:
            return np.load(ids_npy), mat
    ids, vecs = [], []
    for rid, emb in db.execute(f"SELECT id, embedding FROM {table}"):
        ids.append(rid)
        vecs.append(np.frombuffer(emb, dtype=np.float32))
    if not vecs:
        return np.zeros(0, np.int64), np.zeros((0, 384), np.float32)
    mat = np.vstack(vecs).astype(np.float32)
    ids = np.array(ids, np.int64)
    np.save(npy, mat)
    np.save(ids_npy, ids)
    return ids, mat


def _topk(q, ids, mat, k, boost=1.0):
    import numpy as np
    if mat.shape[0] == 0:
        return []
    scores = (mat @ np.asarray(q, np.float32)) * boost
    k = min(k, scores.shape[0])
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [(float(scores[i]), int(ids[i])) for i in top]


_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(texts):
    m = get_model()
    vecs = m.encode(texts, batch_size=32, show_progress_bar=False,
                    normalize_embeddings=True)
    return [list(map(float, v)) for v in vecs]


# --- Target DB ---
def open_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # WAL: concurrent readers (mission-control, daemon) don't block the writer;
    # busy_timeout: writer waits instead of failing on transient locks.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            book_id      TEXT PRIMARY KEY,
            title        TEXT,
            authors      TEXT,
            year         INTEGER,
            item_type    TEXT,
            tags         TEXT,
            file_path    TEXT,
            content_hash TEXT,
            indexed_at   REAL
        );
        CREATE TABLE IF NOT EXISTS annotations (
            id         INTEGER PRIMARY KEY,
            book_id    TEXT REFERENCES books(book_id),
            zotero_key TEXT UNIQUE,
            text       TEXT,
            comment    TEXT,
            color      TEXT,
            page_label TEXT,
            hash       TEXT,
            embedding  BLOB NOT NULL,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY,
            book_id    TEXT REFERENCES books(book_id),
            chunk_idx  INTEGER,
            content    TEXT,
            page_start INTEGER,
            page_end   INTEGER,
            hash       TEXT,
            embedding  BLOB NOT NULL,
            UNIQUE(book_id, chunk_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_ann_book ON annotations(book_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_book ON chunks(book_id);

        -- FTS5 keyword index (Postgres-tsvector analog) над текстом чанков и
        -- выделений. external-content: сам текст не дублируется, лежит в базовых
        -- таблицах. Триггеры держат индекс в синхроне при insert/update/delete —
        -- как tsvector-триггер в Postgres. remove_diacritics 2 — регистр/диакритика.
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content, content='chunks', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2');
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES('delete', old.id, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES('delete', old.id, old.content);
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE VIRTUAL TABLE IF NOT EXISTS annotations_fts USING fts5(
            text, comment, content='annotations', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2');
        CREATE TRIGGER IF NOT EXISTS annotations_ai AFTER INSERT ON annotations BEGIN
            INSERT INTO annotations_fts(rowid, text, comment)
                VALUES (new.id, new.text, new.comment);
        END;
        CREATE TRIGGER IF NOT EXISTS annotations_ad AFTER DELETE ON annotations BEGIN
            INSERT INTO annotations_fts(annotations_fts, rowid, text, comment)
                VALUES('delete', old.id, old.text, old.comment);
        END;
        CREATE TRIGGER IF NOT EXISTS annotations_au AFTER UPDATE ON annotations BEGIN
            INSERT INTO annotations_fts(annotations_fts, rowid, text, comment)
                VALUES('delete', old.id, old.text, old.comment);
            INSERT INTO annotations_fts(rowid, text, comment)
                VALUES (new.id, new.text, new.comment);
        END;
        """
    )
    return conn


# --- Zotero reader (snapshot copy to avoid locking the live DB) ---
def open_zotero():
    if not ZOTERO_DB.exists():
        sys.exit(f"zotero.sqlite not found at {ZOTERO_DB}. Set ZOTERO_DATA_DIR.")
    snap = Path(tempfile.gettempdir()) / "zotero_snapshot.sqlite"
    shutil.copy2(ZOTERO_DB, snap)
    return sqlite3.connect(f"file:{snap}?mode=ro", uri=True)


def field_value(z, item_id, field_name):
    row = z.execute(
        """SELECT idv.value FROM itemData id
             JOIN fields f ON id.fieldID = f.fieldID
             JOIN itemDataValues idv ON id.valueID = idv.valueID
            WHERE id.itemID = ? AND f.fieldName = ?""",
        (item_id, field_name),
    ).fetchone()
    return row[0] if row else None


def book_meta(z, item_id):
    title = field_value(z, item_id, "title")
    date = field_value(z, item_id, "date") or ""
    year = None
    for tok in date.replace("-", " ").split():
        if tok.isdigit() and len(tok) == 4:
            year = int(tok)
            break
    itype = z.execute(
        """SELECT it.typeName FROM items i
             JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID = ?""",
        (item_id,),
    ).fetchone()
    authors = z.execute(
        """SELECT c.lastName, c.firstName FROM itemCreators ic
             JOIN creators c ON ic.creatorID = c.creatorID
            WHERE ic.itemID = ? ORDER BY ic.orderIndex""",
        (item_id,),
    ).fetchall()
    authors_str = "; ".join(
        (ln or "") + ((", " + fn) if fn else "") for ln, fn in authors
    )
    tags = z.execute(
        """SELECT t.name FROM itemTags itg
             JOIN tags t ON itg.tagID = t.tagID WHERE itg.itemID = ?""",
        (item_id,),
    ).fetchall()
    tags_str = "; ".join(t[0] for t in tags)
    path = z.execute(
        """SELECT path FROM itemAttachments
            WHERE parentItemID = ? AND contentType = 'application/pdf'
            LIMIT 1""",
        (item_id,),
    ).fetchone()
    return {
        "title": title,
        "year": year,
        "item_type": itype[0] if itype else None,
        "authors": authors_str,
        "tags": tags_str,
        "file_path": path[0] if path else None,
    }


def cmd_index():
    z = open_zotero()
    rows = z.execute(
        """SELECT ann.key, book.itemID, book.key,
                  ia.text, ia.comment, ia.color, ia.pageLabel
             FROM itemAnnotations ia
             JOIN items ann ON ia.itemID = ann.itemID
             JOIN itemAttachments att ON ia.parentItemID = att.itemID
             JOIN items book ON att.parentItemID = book.itemID
            WHERE ia.text IS NOT NULL OR ia.comment IS NOT NULL"""
    ).fetchall()

    db = open_db()
    now = time.time()

    # --- books catalog (snapshot metadata) ---
    book_items = {}  # book_key -> book_itemID
    for _, bid, bkey, *_ in rows:
        book_items[bkey] = bid
    for bkey, bid in book_items.items():
        m = book_meta(z, bid)
        db.execute(
            """INSERT INTO books(book_id,title,authors,year,item_type,tags,
                                 file_path,content_hash,indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(book_id) DO UPDATE SET
                 title=excluded.title, authors=excluded.authors,
                 year=excluded.year, item_type=excluded.item_type,
                 tags=excluded.tags, file_path=excluded.file_path,
                 indexed_at=excluded.indexed_at""",
            (bkey, m["title"], m["authors"], m["year"], m["item_type"],
             m["tags"], m["file_path"], None, now),
        )

    # --- annotations (incremental by hash) ---
    existing = dict(db.execute("SELECT zotero_key, hash FROM annotations").fetchall())
    to_embed, meta = [], []
    for ann_key, bid, bkey, text, comment, color, page in rows:
        blob = ((text or "") + "\n" + (comment or "")).strip()
        h = hashlib.sha1(blob.encode("utf-8")).hexdigest()
        if existing.get(ann_key) == h:
            continue
        to_embed.append(blob)
        meta.append((ann_key, bkey, text, comment, color, page, h))

    added = 0
    if to_embed:
        vecs = embed(to_embed)
        for (ann_key, bkey, text, comment, color, page, h), vec in zip(meta, vecs):
            db.execute(
                """INSERT INTO annotations(book_id,zotero_key,text,comment,
                                           color,page_label,hash,embedding,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(zotero_key) DO UPDATE SET
                     text=excluded.text, comment=excluded.comment,
                     color=excluded.color, page_label=excluded.page_label,
                     hash=excluded.hash, embedding=excluded.embedding""",
                (bkey, ann_key, text, comment, color, page, h,
                 pack_embedding(vec), now),
            )
            added += 1

    db.commit()
    if added:
        _invalidate_cache()
    nb = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    na = db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    print(f"index: books={nb} annotations={na} (+{added} new/changed)")


STORAGE_DIR = Path(ZOTERO_DATA_DIR) / "storage"


def extract_pages(pdf_path):
    """Return list of (page_number, text). Empty list if unreadable."""
    import fitz
    out = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return out
    for i, page in enumerate(doc, start=1):
        try:
            out.append((i, page.get_text("text")))
        except Exception:
            out.append((i, ""))
    doc.close()
    return out


def chunk_pages(pages, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Yield (chunk_text, page_start, page_end) tracking page spans."""
    text_parts, pmap = [], []
    for pnum, ptext in pages:
        ptext = (ptext or "").strip()
        if not ptext:
            continue
        if text_parts:
            text_parts.append("\n")
            pmap.append(pnum)
        text_parts.append(ptext)
        pmap.extend([pnum] * len(ptext))
    text = "".join(text_parts)
    n = len(text)
    if n == 0:
        return
    step = max(1, size - overlap)
    for start in range(0, n, step):
        end = min(start + size, n)
        chunk = text[start:end].strip()
        if chunk:
            yield chunk, pmap[start], pmap[end - 1]
        if end >= n:
            break


def cmd_index_chunks():
    z = open_zotero()
    # every PDF attachment that hangs off a regular (parent) item
    rows = z.execute(
        """SELECT att.key, book.itemID, book.key, ia.path
             FROM itemAttachments ia
             JOIN items att ON ia.itemID = att.itemID
             JOIN items book ON ia.parentItemID = book.itemID
            WHERE ia.contentType = 'application/pdf'
              AND ia.path LIKE 'storage:%'"""
    ).fetchall()

    db = open_db()
    now = time.time()
    done = scanned = missing = 0

    for att_key, book_item, book_key, path in rows:
        fname = path[len("storage:"):]
        pdf = STORAGE_DIR / att_key / fname
        if not pdf.exists():
            missing += 1
            continue

        pages = extract_pages(str(pdf))
        text = "".join(t for _, t in pages)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()

        # ensure a books row exists (may have no annotations)
        m = book_meta(z, book_item)
        prev = db.execute(
            "SELECT content_hash FROM books WHERE book_id=?", (book_key,)
        ).fetchone()
        db.execute(
            """INSERT INTO books(book_id,title,authors,year,item_type,tags,
                                 file_path,content_hash,indexed_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(book_id) DO UPDATE SET
                 title=excluded.title, authors=excluded.authors,
                 year=excluded.year, item_type=excluded.item_type,
                 tags=excluded.tags, file_path=excluded.file_path,
                 content_hash=excluded.content_hash, indexed_at=excluded.indexed_at""",
            (book_key, m["title"], m["authors"], m["year"], m["item_type"],
             m["tags"], m["file_path"], h, now),
        )

        if prev and prev[0] == h:
            continue  # unchanged since last run

        avg = len(text) / max(1, len(pages))
        if avg < MIN_CHARS_PER_PAGE:
            scanned += 1
            continue  # scanned / no text layer -> needs OCR later

        db.execute("DELETE FROM chunks WHERE book_id=?", (book_key,))
        batch = list(chunk_pages(pages))[:MAX_CHUNKS_PER_BOOK]
        vecs = embed([c for c, _, _ in batch])
        for i, ((chunk, p0, p1), vec) in enumerate(zip(batch, vecs)):
            db.execute(
                """INSERT INTO chunks(book_id,chunk_idx,content,page_start,
                                      page_end,hash,embedding)
                   VALUES(?,?,?,?,?,?,?)""",
                (book_key, i, chunk, p0, p1,
                 hashlib.sha1(chunk.encode()).hexdigest(), pack_embedding(vec)),
            )
        db.commit()
        done += 1
        print(f"  ✓ {m['authors'] or '?'} — {(m['title'] or fname)[:50]} "
              f"({len(batch)} chunks)")

    if done:
        _invalidate_cache()
    nc = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"index-chunks: indexed={done} scanned/skipped={scanned} "
          f"missing={missing} · total chunks={nc}")


def _invalidate_cache():
    for t in ("annotations", "chunks"):
        for suf in (f"_{t}.npy", f"_{t}_ids.npy"):
            p = HERE / suf
            if p.exists():
                p.unlink()


def ocr_pages(pdf_path, dpi=300, lang="rus+eng"):
    """OCR a scanned PDF page-by-page via fitz raster + tesseract CLI.
    Returns list of (page_number, text)."""
    import fitz
    import subprocess
    out = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return out
    for i, page in enumerate(doc, start=1):
        try:
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            r = subprocess.run(["tesseract", "-", "-", "-l", lang],
                               input=png, capture_output=True, timeout=120)
            out.append((i, r.stdout.decode("utf-8", "ignore")))
        except Exception:
            out.append((i, ""))
    doc.close()
    return out


def cmd_ocr_scans():
    import shutil as _sh
    if not _sh.which("tesseract"):
        sys.exit("tesseract not found. Install: brew install tesseract tesseract-lang")
    z = open_zotero()
    # books that have a PDF on disk but produced no chunks == scanned
    db = open_db()
    have_chunks = {r[0] for r in db.execute("SELECT DISTINCT book_id FROM chunks")}
    rows = z.execute(
        """SELECT att.key, book.itemID, book.key, ia.path
             FROM itemAttachments ia
             JOIN items att ON ia.itemID = att.itemID
             JOIN items book ON ia.parentItemID = book.itemID
            WHERE ia.contentType='application/pdf' AND ia.path LIKE 'storage:%'"""
    ).fetchall()
    done = 0
    for att_key, book_item, book_key, path in rows:
        if book_key in have_chunks:
            continue
        pdf = STORAGE_DIR / att_key / path[len("storage:"):]
        if not pdf.exists():
            continue
        m = book_meta(z, book_item)
        print(f"  OCR: {m['authors'] or '?'} — {(m['title'] or pdf.name)[:50]} ...")
        pages = ocr_pages(str(pdf))
        text = "".join(t for _, t in pages)
        if len(text) < MIN_CHARS_PER_PAGE:
            print("    still empty — skip")
            continue
        batch = list(chunk_pages(pages))[:MAX_CHUNKS_PER_BOOK]
        vecs = embed([c for c, _, _ in batch])
        db.execute("DELETE FROM chunks WHERE book_id=?", (book_key,))
        for i, ((chunk, p0, p1), vec) in enumerate(zip(batch, vecs)):
            db.execute(
                """INSERT INTO chunks(book_id,chunk_idx,content,page_start,
                                      page_end,hash,embedding)
                   VALUES(?,?,?,?,?,?,?)""",
                (book_key, i, chunk, p0, p1,
                 hashlib.sha1(chunk.encode()).hexdigest(), pack_embedding(vec)))
        db.execute("UPDATE books SET content_hash=? WHERE book_id=?",
                   (hashlib.sha1(text.encode()).hexdigest(), book_key))
        db.commit()
        done += 1
        print(f"    ✓ {len(batch)} chunks")
    if done:
        _invalidate_cache()
    print(f"ocr-scans: OCR'd {done} book(s)")


def cmd_search(query, limit=8):
    db = open_db()
    q = embed([query])[0]
    ids, mat = _matrix(db, "annotations")
    for score, rid in _topk(q, ids, mat, limit):
        text, comment, page, title, authors, year, path = db.execute(
            """SELECT a.text,a.comment,a.page_label,b.title,b.authors,b.year,b.file_path
                 FROM annotations a LEFT JOIN books b ON a.book_id=b.book_id
                WHERE a.id=?""", (rid,)).fetchone()
        url = path if (path or "").startswith("http") else None
        if url and _is_junk_title(title, path):
            title = _url_label(url)
        who = f"{authors or '?'} — {title or '?'}" + (f" ({year})" if year else "")
        loc = f" · стр. {page}" if page else ""
        print(f"\n[{score:.3f}] {who}{loc}")
        if url:
            print(f"  🔗 {url}")
        if text:
            print(f"  «{text.strip()[:280]}»")
        if comment:
            print(f"  ↳ заметка: {comment.strip()[:200]}")


def cmd_search_all(query, limit=8, ann_boost=1.25):
    db = open_db()
    q = embed([query])[0]
    ai, am = _matrix(db, "annotations")
    ci, cm = _matrix(db, "chunks")
    pool = ([(s, "ann", r) for s, r in _topk(q, ai, am, limit, ann_boost)]
            + [(s, "chunk", r) for s, r in _topk(q, ci, cm, limit)])
    pool.sort(key=lambda x: -x[0])
    for score, kind, rid in pool[:limit]:
        if kind == "ann":
            body, page, title, authors, year, path = db.execute(
                """SELECT COALESCE(a.text,a.comment),a.page_label,
                          b.title,b.authors,b.year,b.file_path
                     FROM annotations a LEFT JOIN books b ON a.book_id=b.book_id
                    WHERE a.id=?""", (rid,)).fetchone()
            tag = "✍ выделение"
        else:
            body, p0, p1, title, authors, year, path = db.execute(
                """SELECT c.content,c.page_start,c.page_end,b.title,b.authors,b.year,b.file_path
                     FROM chunks c LEFT JOIN books b ON c.book_id=b.book_id
                    WHERE c.id=?""", (rid,)).fetchone()
            page = f"{p0}" if p0 == p1 else f"{p0}–{p1}"
            tag = "📖 текст"
        url = path if (path or "").startswith("http") else None
        if url and _is_junk_title(title, path):
            title = _url_label(url)
        who = f"{authors or '?'} — {title or '?'}" + (f" ({year})" if year else "")
        loc = f" · стр. {page}" if page else ""
        print(f"\n[{score:.3f}] {tag} · {who}{loc}")
        if url:
            print(f"  🔗 {url}")
        print(f"  {(body or '').strip()[:280]}")


# ── Гибридный поиск: FTS5 (BM25) + вектор, слияние RRF ─────────────────────────
def _fts_match(query):
    """MATCH-строка для FTS5 из слов запроса (каждое слово в кавычках, OR —
    широкий keyword-recall; смысловую точность добирает вектор)."""
    toks = re.findall(r"\w+", query or "", re.UNICODE)
    return " OR ".join(f'"{t}"' for t in toks) if toks else None


def _ensure_fts(db):
    """Догоняет FTS-индекс до базовых таблиц (external-content rebuild). Обычно
    ничего не делает — триггеры держат синхрон; нужен для первичного backfill и
    после массовых операций мимо триггеров."""
    for fts, base in (("chunks_fts", "chunks"), ("annotations_fts", "annotations")):
        try:
            nf = db.execute(f"SELECT count(*) FROM {fts}").fetchone()[0]
        except sqlite3.OperationalError:
            nf = -1
        nb = db.execute(f"SELECT count(*) FROM {base}").fetchone()[0]
        if nf != nb:
            db.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
    db.commit()


def cmd_fts_rebuild():
    db = open_db()
    for fts in ("chunks_fts", "annotations_fts"):
        db.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
    db.commit()
    nc = db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    na = db.execute("SELECT count(*) FROM annotations_fts").fetchone()[0]
    print(f"fts-rebuild: chunks_fts={nc} · annotations_fts={na}")


def _scope_where(scope):
    if scope == "zotero":
        return "COALESCE(b.origin,'zotero')='zotero'"
    if scope == "sources":
        return "COALESCE(b.origin,'zotero')!='zotero'"
    return "1=1"


def _hybrid_search(db, query, limit=8, scope="all", k_rrf=60):
    """Возвращает (order, meta): order — список (kind,id) по убыванию RRF;
    meta.scores — RRF-балл, meta.method — 'vector'/'fts'/'vector+fts'.

    Два ранжировщика: вектор (косинус по chunks+annotations) и FTS (BM25 по
    chunks_fts+annotations_fts). Слияние Reciprocal Rank Fusion:
    RRF(d)=Σ 1/(k+rank_i) — устойчиво к разным шкалам, как принято для BM25+ANN."""
    pool = max(limit * 8, 40)
    where = _scope_where(scope)
    allowed_c = {r[0] for r in db.execute(
        f"SELECT c.id FROM chunks c JOIN books b ON c.book_id=b.book_id WHERE {where}")}
    allowed_a = {r[0] for r in db.execute(
        f"SELECT a.id FROM annotations a JOIN books b ON a.book_id=b.book_id WHERE {where}")}

    q = embed([query])[0]
    ci, cm = _matrix(db, "chunks")
    ai, am = _matrix(db, "annotations")
    vscored = ([(s, ("chunk", rid)) for s, rid in _topk(q, ci, cm, pool) if rid in allowed_c]
               + [(s, ("ann", rid)) for s, rid in _topk(q, ai, am, pool) if rid in allowed_a])
    vec_rank = [it for _, it in sorted(vscored, key=lambda x: -x[0])]

    fts_rank = []
    m = _fts_match(query)
    if m:
        _ensure_fts(db)
        fscored = []
        for rid, bm in db.execute(
                f"""SELECT c.id, bm25(chunks_fts) FROM chunks_fts
                      JOIN chunks c ON c.id=chunks_fts.rowid
                      JOIN books b ON b.book_id=c.book_id
                     WHERE chunks_fts MATCH ? AND {where}
                     ORDER BY bm25(chunks_fts) LIMIT ?""", (m, pool)):
            fscored.append((bm, ("chunk", rid)))
        for rid, bm in db.execute(
                f"""SELECT a.id, bm25(annotations_fts) FROM annotations_fts
                      JOIN annotations a ON a.id=annotations_fts.rowid
                      JOIN books b ON b.book_id=a.book_id
                     WHERE annotations_fts MATCH ? AND {where}
                     ORDER BY bm25(annotations_fts) LIMIT ?""", (m, pool)):
            fscored.append((bm, ("ann", rid)))
        fts_rank = [it for _, it in sorted(fscored, key=lambda x: x[0])]  # bm25 меньше=лучше

    vset, fset = set(vec_rank), set(fts_rank)
    scores = {}
    for rank, it in enumerate(vec_rank, 1):
        scores[it] = scores.get(it, 0.0) + 1.0 / (k_rrf + rank)
    for rank, it in enumerate(fts_rank, 1):
        scores[it] = scores.get(it, 0.0) + 1.0 / (k_rrf + rank)
    order = sorted(scores, key=lambda it: -scores[it])[:limit]
    method = {it: ("vector+fts" if it in vset and it in fset
                   else "vector" if it in vset else "fts") for it in order}
    vscore = {it: s for s, it in vscored}
    return order, {"scores": scores, "method": method, "vscore": vscore,
                   "vec_rank": vec_rank, "fts_rank": fts_rank}


def _item_display(db, kind, rid):
    if kind == "ann":
        body, page, title, authors, year, path = db.execute(
            """SELECT COALESCE(a.text,a.comment),a.page_label,
                      b.title,b.authors,b.year,b.file_path
                 FROM annotations a LEFT JOIN books b ON a.book_id=b.book_id
                WHERE a.id=?""", (rid,)).fetchone()
        tag, loc = "✍ выделение", (f" · стр. {page}" if page else "")
    else:
        body, p0, p1, title, authors, year, path = db.execute(
            """SELECT c.content,c.page_start,c.page_end,b.title,b.authors,b.year,b.file_path
                 FROM chunks c LEFT JOIN books b ON c.book_id=b.book_id
                WHERE c.id=?""", (rid,)).fetchone()
        page = f"{p0}" if p0 == p1 else f"{p0}–{p1}"
        tag, loc = "📖 текст", (f" · стр. {page}" if page else "")
    url = path if (path or "").startswith("http") else None
    if url and _is_junk_title(title, path):
        title = _url_label(url)
    who = f"{authors or '?'} — {title or '?'}" + (f" ({year})" if year else "")
    return {"tag": tag, "who": who, "loc": loc, "url": url,
            "snippet": (body or "").strip()[:300]}


def _fts_only(db, query, limit, scope):
    """Чистый BM25 без вектора — не грузит модель (fts-режим быстрый, <1с)."""
    m = _fts_match(query)
    if not m:
        return []
    _ensure_fts(db)
    where = _scope_where(scope)
    scored = []
    for rid, bm in db.execute(
            f"""SELECT c.id, bm25(chunks_fts) FROM chunks_fts
                  JOIN chunks c ON c.id=chunks_fts.rowid
                  JOIN books b ON b.book_id=c.book_id
                 WHERE chunks_fts MATCH ? AND {where}
                 ORDER BY bm25(chunks_fts) LIMIT ?""", (m, limit * 4)):
        scored.append((bm, ("chunk", rid)))
    for rid, bm in db.execute(
            f"""SELECT a.id, bm25(annotations_fts) FROM annotations_fts
                  JOIN annotations a ON a.id=annotations_fts.rowid
                  JOIN books b ON b.book_id=a.book_id
                 WHERE annotations_fts MATCH ? AND {where}
                 ORDER BY bm25(annotations_fts) LIMIT ?""", (m, limit * 4)):
        scored.append((bm, ("ann", rid)))
    return [it for _, it in sorted(scored, key=lambda x: x[0])][:limit]


def cmd_hybrid(query, limit=8, scope="all", as_json=False, mode="hybrid"):
    """mode: hybrid (RRF FTS+вектор) · vector (только косинус) · fts (только BM25).
    fts-режим идёт мимо модели — быстрый; vector/hybrid грузят эмбеддер."""
    db = open_db()
    results = []
    if mode == "fts":
        for rank, (kind, rid) in enumerate(_fts_only(db, query, limit, scope), 1):
            d = _item_display(db, kind, rid)
            d.update({"kind": kind, "score": round(1.0 / rank, 4), "method": "fts"})
            results.append(d)
        _emit_hybrid(query, scope, mode, results, as_json)
        return
    _, meta = _hybrid_search(db, query, max(limit, 1), scope)
    if mode == "vector":
        items = meta["vec_rank"][:limit]
    else:
        items = sorted(meta["scores"], key=lambda it: -meta["scores"][it])[:limit]
    for kind, rid in items:
        d = _item_display(db, kind, rid)
        if mode == "vector":
            score, meth = round(meta["vscore"].get((kind, rid), 0.0), 4), "vector"
        else:
            score, meth = round(meta["scores"][(kind, rid)], 4), meta["method"][(kind, rid)]
        d.update({"kind": kind, "score": score, "method": meth})
        results.append(d)
    _emit_hybrid(query, scope, mode, results, as_json)


def _emit_hybrid(query, scope, mode, results, as_json):
    if as_json:
        print(json.dumps({"query": query, "scope": scope, "mode": mode,
                          "results": results}, ensure_ascii=False, indent=2))
        return
    print(f"{mode} · scope={scope} · «{query}» · {len(results)} рез.")
    for d in results:
        print(f"\n[{d['score']:.4f}] {d['method']:11} {d['tag']} · {d['who']}{d['loc']}")
        if d["url"]:
            print(f"  🔗 {d['url']}")
        print(f"  {d['snippet'][:280]}")


TYPE_COLORS = {
    "book": "#4C9AFF", "journalArticle": "#F5A623", "webpage": "#57D9A3",
    "videoRecording": "#FF7452", "blogPost": "#C0B6F2",
    "presentation": "#F78FB3", "conferencePaper": "#79E2F2",
    "report": "#FFC400", "thesis": "#8777D9", "document": "#B3BAC5",
}
TYPE_NAMES = {
    "book": "Книги", "journalArticle": "Статьи", "webpage": "Веб-страницы",
    "videoRecording": "Видео", "blogPost": "Блог-посты",
    "presentation": "Презентации", "conferencePaper": "Доклады",
    "report": "Отчёты", "thesis": "Диссертации", "document": "Документы",
}

# Раскраска графа базы источников — по origin (откуда пришёл источник),
# а не по типу. Для Zotero-графа не используется.
ORIGIN_COLORS = {
    "zotero": "#4C9AFF", "community": "#57D9A3", "web": "#F5A623",
    "blog": "#C0B6F2", "manual": "#FFC400",
}
ORIGIN_NAMES = {
    "zotero": "Zotero (книги)", "community": "Сообщества",
    "web": "Веб", "blog": "Блог", "manual": "Вручную",
}


def cmd_build_graph(threshold=0.55, top_k=6, color_by="type", outdir=None,
                    exclude_origins=None):
    """Book-level semantic map: node = book, edge = thematic similarity.
    Reuses the Obsidian graph layout + HTML viewer for an identical look.

    color_by='type'   — раскраска по item_type (граф библиотеки Zotero);
    color_by='origin' — раскраска по origin (граф единой базы источников).
    outdir            — куда писать graph.json/html (по умолчанию tools/zotero/graph).
    exclude_origins   — множество origin, которые НЕ включать в граф. Для базы
                        «Источники» это {'zotero'}: под источниками имеются в виду
                        собранные ссылки, а не книги библиотеки (у Zotero свой граф)."""
    by_origin = color_by == "origin"
    import numpy as np
    import importlib.util
    ov_path = HERE.parent / "obsidian" / "tools" / "obsidian_vsearch.py"
    spec = importlib.util.spec_from_file_location("ov_zot", ov_path)
    ov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ov)

    db = open_db()
    has_origin_col = "origin" in [c[1] for c in db.execute("PRAGMA table_info(books)")]
    # Граф библиотеки (color_by='type') по умолчанию — только Zotero: собранные
    # ссылки живут в своём графе источников (color_by='origin'). Так карточка и
    # граф «Библиотека · Zotero» не смешиваются с community-ссылками.
    if exclude_origins is None and not by_origin and has_origin_col:
        all_origins = {r[0] for r in db.execute(
            "SELECT DISTINCT COALESCE(origin,'zotero') FROM books")}
        exclude_origins = all_origins - {"zotero"}
    excluded_ids: set[str] = set()
    if exclude_origins and has_origin_col:
        ph = ",".join("?" * len(exclude_origins))
        excluded_ids = {r[0] for r in db.execute(
            f"SELECT book_id FROM books WHERE COALESCE(origin,'zotero') IN ({ph})",
            tuple(exclude_origins))}
    sums, counts = {}, {}
    for tbl in ("chunks", "annotations"):
        for book_id, emb in db.execute(f"SELECT book_id, embedding FROM {tbl}"):
            if not book_id or book_id in excluded_ids:
                continue
            v = np.frombuffer(emb, np.float32)
            if book_id in sums:
                sums[book_id] += v
                counts[book_id] += 1
            else:
                sums[book_id] = v.copy()
                counts[book_id] = 1
    ids = list(sums)
    if not ids:
        sys.exit("empty index — run `index` / `index-chunks` first")

    mat = np.vstack([sums[b] / counts[b] for b in ids]).astype(np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = mat @ mat.T
    np.fill_diagonal(sims, -1.0)

    E = {}
    kk = min(top_k, len(ids) - 1)
    for i in range(len(ids)):
        row = sims[i]
        cand = np.argpartition(row, -kk)[-kk:] if kk < len(ids) else range(len(ids))
        for j in cand:
            j = int(j)
            s = float(row[j])
            if s < threshold:
                continue
            a, b = (i, j) if i < j else (j, i)
            if a != b and E.get((a, b), -1) < s:
                E[(a, b)] = s
    edges = [{"source": ids[i], "target": ids[j], "weight": round(w, 3)}
             for (i, j), w in E.items()]

    positions = ov._compute_positions(ids, edges)
    degree = {}
    for (i, j) in E:
        degree[ids[i]] = degree.get(ids[i], 0) + 1
        degree[ids[j]] = degree.get(ids[j], 0) + 1

    has_origin = "origin" in [c[1] for c in db.execute("PRAGMA table_info(books)")]
    ocol = ",COALESCE(origin,'zotero') origin" if has_origin else ",'zotero' origin"
    meta_rows = {r[0]: r for r in db.execute(
        f"SELECT book_id,title,authors,year,item_type,tags,file_path{ocol} FROM books")}
    nodes, notes = [], {}
    for b in ids:
        _, title, authors, year, itype, tags, file_path, origin = meta_rows.get(
            b, (b, None, None, None, None, None, None, "zotero"))
        url = file_path if (file_path or "").startswith("http") else None
        # веб-источник с пустым/мусорным заголовком → читаемая метка из ссылки
        if url and _is_junk_title(title, file_path):
            title = _url_label(url)
        title = title or b
        px, py = positions.get(b, (1500, 1500))
        label = (title[:44] + "…") if len(title) > 45 else title
        who = f"{authors or '?'} — {title}" + (f" ({year})" if year else "")
        if by_origin:
            section, sname = origin, ORIGIN_NAMES.get(origin, origin or "Другое")
            color = ORIGIN_COLORS.get(origin, "#8C9BAB")
        else:
            section, sname = itype or "document", TYPE_NAMES.get(itype, "Другое")
            color = TYPE_COLORS.get(itype, "#8C9BAB")
        nodes.append({
            "id": b, "label": label, "note": who, "path": b,
            "url": url,
            "section": section,
            "section_name": sname,
            "color": color,
            "degree": degree.get(b, 0),
            "snippet": (authors or "") + (f" · теги: {tags}" if tags else ""),
            "x": px, "y": py,
        })
        anns = db.execute(
            "SELECT text,comment,page_label FROM annotations WHERE book_id=? ORDER BY id",
            (b,)).fetchall()
        parts = [who]
        if url:
            parts.append(f"🔗 {url}")
        parts += [f"Тип: {TYPE_NAMES.get(itype, '—')} · выделений: {len(anns)}", ""]
        if anns:
            parts.append(f"── Выделения ({len(anns)}) ──")
            for t, c, pg in anns[:150]:
                line = "• " + (t or "").strip() + (f"  (стр. {pg})" if pg else "")
                if c:
                    line += f"\n   ↳ {c.strip()}"
                parts.append(line)
        first = db.execute(
            "SELECT content FROM chunks WHERE book_id=? ORDER BY chunk_idx LIMIT 3",
            (b,)).fetchall()
        if first:
            parts.append("\n── Из текста ──")
            parts += [f[0].strip() for f in first]
        notes[b] = "\n".join(parts)[:40000]

    graph_data = {"nodes": nodes, "edges": edges, "meta": {
        "total_notes": len(ids), "total_chunks": len(ids),
        "total_edges": len(edges), "threshold": threshold,
        "top_k": top_k, "built_at": int(time.time())}}

    if by_origin:                     # легенда по origin (граф базы источников)
        ov.SECTION_COLORS = ORIGIN_COLORS
        ov.SECTION_NAMES = ORIGIN_NAMES
    else:                             # легенда по типу книги (граф Zotero)
        ov.SECTION_COLORS = TYPE_COLORS
        ov.SECTION_NAMES = TYPE_NAMES
    outdir = Path(outdir) if outdir else HERE / "graph"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "graph.json").write_text(
        json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "graph.html").write_text(
        ov._generate_html(graph_data, notes), encoding="utf-8")
    print(f"graph: books={len(ids)} edges={len(edges)} "
          f"-> {outdir / 'graph.html'}")


STATUS_PATH = HERE / "status.json"
FINGERPRINT_PATH = HERE / ".fingerprint.json"


def _zotero_fingerprint():
    """Cheap signal of whether the Zotero library changed since last refresh."""
    z = open_zotero()
    ann = z.execute("SELECT COUNT(*) FROM itemAnnotations").fetchone()[0]
    keys = z.execute(
        """SELECT att.key FROM itemAttachments ia
             JOIN items att ON ia.itemID = att.itemID
            WHERE ia.contentType='application/pdf' ORDER BY att.key"""
    ).fetchall()
    joined = ",".join(k[0] for k in keys)
    return {"annotations": ann, "pdf_count": len(keys),
            "pdf_keys_hash": hashlib.sha1(joined.encode()).hexdigest()}


def cmd_refresh():
    """Incremental refresh for the background service: reindex only what
    changed in Zotero, rebuild graph if anything moved, write status.json."""
    t0 = time.time()
    fp = _zotero_fingerprint()
    prev = {}
    if FINGERPRINT_PATH.exists():
        try:
            prev = json.loads(FINGERPRINT_PATH.read_text())
        except Exception:
            prev = {}
    prev_status = {}
    if STATUS_PATH.exists():
        try:
            prev_status = json.loads(STATUS_PATH.read_text())
        except Exception:
            prev_status = {}

    actions = []
    if fp["annotations"] != prev.get("annotations") or not DB_PATH.exists():
        cmd_index()
        actions.append("annotations")
    if fp["pdf_keys_hash"] != prev.get("pdf_keys_hash"):
        cmd_index_chunks()
        actions.append("chunks")
    FINGERPRINT_PATH.write_text(json.dumps(fp))

    db = open_db()
    cur_ann = db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    cur_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    graph_json = HERE / "graph" / "graph.json"
    # rebuild graph if anything reindexed, counts moved (e.g. OCR), or missing
    if (actions or not graph_json.exists()
            or cur_chunks != prev_status.get("chunks")
            or cur_ann != prev_status.get("annotations")):
        cmd_build_graph()
        actions.append("graph")

    st = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "books": db.execute("SELECT COUNT(*) FROM books").fetchone()[0],
        "annotations": db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0],
        "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "db_mb": round(DB_PATH.stat().st_size / 1e6, 1) if DB_PATH.exists() else 0,
        "actions": actions or ["no-change"],
        "took_s": round(time.time() - t0, 1),
        "graph_html": str(HERE / "graph" / "graph.html"),
    }
    STATUS_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    print(f"refresh: {', '.join(st['actions'])} · {st['took_s']}s")


def cmd_stats():
    db = open_db()
    nb = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    na = db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
    nc = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    size = DB_PATH.stat().st_size / 1e6 if DB_PATH.exists() else 0
    print(f"books={nb} annotations={na} chunks={nc} db={size:.1f}MB")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "index":
        cmd_index()
    elif cmd == "index-chunks":
        cmd_index_chunks()
    elif cmd == "ocr-scans":
        cmd_ocr_scans()
    elif cmd == "graph":
        thr = float(sys.argv[2]) if len(sys.argv) > 2 else 0.55
        tk = int(sys.argv[3]) if len(sys.argv) > 3 else 6
        cmd_build_graph(thr, tk)
    elif cmd == "refresh":
        cmd_refresh()
    elif cmd == "search":
        cmd_search(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8)
    elif cmd == "search-all":
        cmd_search_all(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8)
    elif cmd == "hybrid":
        a = sys.argv[2:]
        query = a[0]
        limit = next((int(x) for x in a[1:] if x.isdigit()), 8)
        scope = a[a.index("--scope") + 1] if "--scope" in a else "all"
        mode = a[a.index("--mode") + 1] if "--mode" in a else "hybrid"
        cmd_hybrid(query, limit, scope, as_json="--json" in a, mode=mode)
    elif cmd == "fts-rebuild":
        cmd_fts_rebuild()
    elif cmd == "stats":
        cmd_stats()
    else:
        sys.exit(f"unknown command: {cmd}")
