"""
Векторное хранилище новостей radar (SQLite + эмбеддинги).

Каждая RSS-новость складывается в radar.db и векторизуется локальной ONNX-моделью
multilingual-e5-small (onnxruntime + tokenizers, без torch — легко для фона).
Эмбеддинг хранится как BLOB (struct float32), размерность 384.

e5 требует префиксы: документы — "passage: ...", запросы — "query: ...".

Используется фоновой задачей `radar.py ingest`: новые материалы сохраняются и
векторизуются, дальше доступен семантический поиск `radar.py search`.
"""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "radar.db"
MODEL_DIR = BASE / "model"
ONNX_PATH = MODEL_DIR / "multilingual-e5-small.onnx"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"
MAX_TOKENS = 384

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT UNIQUE,
    source     TEXT,
    kind       TEXT,
    title      TEXT,
    text       TEXT,
    url        TEXT,
    ts         REAL,
    fetched_at REAL,
    embedding  BLOB
);
CREATE INDEX IF NOT EXISTS idx_items_ts  ON items(ts);
CREATE INDEX IF NOT EXISTS idx_items_emb ON items(id) WHERE embedding IS NULL;
"""


# ── Эмбеддинги (ONNX, multilingual-e5-small) ──────────────────────────────────
_session = None
_tokenizer = None


def _load():
    global _session, _tokenizer
    if _session is None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        _session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
        _tokenizer.enable_truncation(max_length=MAX_TOKENS)
        _tokenizer.enable_padding()
    return _session, _tokenizer


def _embed_raw(texts: list[str]) -> "list":
    """Прогнать тексты через ONNX, вернуть нормированные векторы (numpy float32)."""
    import numpy as np

    session, tokenizer = _load()
    encs = tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
    ttype = np.zeros_like(ids)

    (last_hidden,) = session.run(
        ["last_hidden_state"],
        {"input_ids": ids, "attention_mask": mask, "token_type_ids": ttype},
    )
    # mean pooling по токенам с учётом attention_mask
    m = mask[..., None].astype(np.float32)
    summed = (last_hidden * m).sum(axis=1)
    counts = np.clip(m.sum(axis=1), 1e-9, None)
    vecs = summed / counts
    # L2-нормализация → косинус = скалярное произведение
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return (vecs / np.clip(norms, 1e-9, None)).astype(np.float32)


def embed(texts: list[str], *, prefix: str = "passage: ", batch: int = 32) -> list[bytes]:
    """Эмбеддинги документов (по умолчанию passage). Возвращает список BLOB-ов."""
    out: list[bytes] = []
    for i in range(0, len(texts), batch):
        chunk = [prefix + t for t in texts[i:i + batch]]
        for v in _embed_raw(chunk):
            out.append(struct.pack(f"{len(v)}f", *v.tolist()))
    return out


def embed_query(text: str) -> bytes:
    """Эмбеддинг запроса (e5-префикс 'query: ')."""
    v = _embed_raw(["query: " + text])[0]
    return struct.pack(f"{len(v)}f", *v.tolist())


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


# ── Хранилище ─────────────────────────────────────────────────────────────────
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def existing_uids(conn: sqlite3.Connection, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    qmarks = ",".join("?" * len(uids))
    rows = conn.execute(f"SELECT uid FROM items WHERE uid IN ({qmarks})", uids).fetchall()
    return {r[0] for r in rows}


def add_items(conn: sqlite3.Connection, items: list[dict], embeddings: list[bytes]) -> int:
    import time

    now = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO items(uid,source,kind,title,text,url,ts,fetched_at,embedding) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (it["uid"], it["source"], it["kind"], it["title"], it["text"],
             it["url"], it["ts"], now, emb)
            for it, emb in zip(items, embeddings)
        ],
    )
    conn.commit()
    return conn.total_changes


def pending(conn: sqlite3.Connection) -> list[tuple]:
    """Строки без эмбеддинга (если ingest сохранял сырьём — векторизовать фоном)."""
    return conn.execute(
        "SELECT id, title, text FROM items WHERE embedding IS NULL"
    ).fetchall()


def set_embedding(conn: sqlite3.Connection, row_id: int, blob: bytes) -> None:
    conn.execute("UPDATE items SET embedding=? WHERE id=?", (blob, row_id))
    conn.commit()


def search(conn: sqlite3.Connection, query: str, k: int = 10) -> list[dict]:
    import numpy as np

    qvec = np.frombuffer(embed_query(query), dtype=np.float32)
    rows = conn.execute(
        "SELECT title, source, url, ts, embedding FROM items WHERE embedding IS NOT NULL"
    ).fetchall()
    scored = []
    for title, source, url, ts, emb in rows:
        vec = np.frombuffer(emb, dtype=np.float32)
        score = float(np.dot(qvec, vec))  # нормированные → косинус = скаляр
        scored.append((score, title, source, url, ts))
    scored.sort(reverse=True)
    return [
        {"score": round(s, 3), "title": t, "source": src, "url": u, "ts": ts}
        for s, t, src, u, ts in scored[:k]
    ]


def recent(conn: sqlite3.Connection, hours: int = 24, limit: int = 40) -> list[dict]:
    """Свежие материалы за последние `hours` часов (для дайджеста)."""
    import time

    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT source, kind, title, text, url, ts FROM items "
        "WHERE ts IS NULL OR ts >= ? ORDER BY COALESCE(ts, fetched_at) DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [
        {"source": s, "kind": k, "title": t, "text": x, "url": u, "ts": ts}
        for s, k, t, x, u, ts in rows
    ]


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    embedded = conn.execute("SELECT COUNT(*) FROM items WHERE embedding IS NOT NULL").fetchone()[0]
    by_src = conn.execute(
        "SELECT source, COUNT(*) FROM items GROUP BY source ORDER BY 2 DESC"
    ).fetchall()
    size_mb = round(DB_PATH.stat().st_size / 1e6, 1) if DB_PATH.exists() else 0
    return {"total": total, "embedded": embedded, "pending": total - embedded,
            "size_mb": size_mb, "by_source": by_src}
