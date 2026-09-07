#!/usr/bin/env python3
"""
Obsidian semantic vector search + knowledge graph.
Usage:
  python3 obsidian_vsearch.py index                    # build/update index
  python3 obsidian_vsearch.py search "query"           # semantic search
  python3 obsidian_vsearch.py hybrid "query"           # semantic + keyword
  python3 obsidian_vsearch.py brainstorm "topic"       # hidden connections around topic
  python3 obsidian_vsearch.py build_graph              # build graph.json + graph.html
  python3 obsidian_vsearch.py stats                    # index info
"""

import sys
import os
import json
import re
import sqlite3
import hashlib
import struct
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
VAULT = Path("/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон")
DB_PATH = TOOLS_DIR / "obsidian_vectors.db"
MODEL_CACHE = TOOLS_DIR / "models"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
GRAPH_DIR = TOOLS_DIR / "graph"

os.environ["HF_HOME"] = str(MODEL_CACHE)
os.environ["TRANSFORMERS_CACHE"] = str(MODEL_CACHE)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
DEFAULT_LIMIT = 10
DEFAULT_THRESHOLD = 0.25
# Max chunks per note used for semantic linking (graph/brainstorm).
# p90 of the vault is ~40 chunks; this keeps normal notes whole and trims
# only the giant Telegram chat-log exports in the Archive that would otherwise
# dominate the graph and blow up memory.
CHUNK_CAP = 40
# Dedup of duplicate notes before graph/brainstorm. The vault has the same note
# copied across folders (Архив 09, Claude workspace 10), producing score≈1.0
# pseudo-connections that drown out real cross-topic bridges.
DEDUP = True
DEDUP_SIM = 0.985       # near-identical notes (caught even if renamed)
DEDUP_STEM_SIM = 0.90   # same filename in a different folder + high similarity

# Разделы, которые в индекс заметок не идут. `08 — Социальный капитал` — это
# 5057 карточек контактов из vCard/CSV-импорта: они однотипны, дают score≈1.0
# друг с другом и в графе заметок перекрывают всё остальное (87% узлов).
# Люди живут в social_capital.db и своём графе — там связи фактические.
EXCLUDE_SECTIONS = ("08 — Социальный капитал",)

SECTION_COLORS = {
    "01": "#E57373", "02": "#4DB6AC", "03": "#4FC3F7",
    "04": "#81C784", "05": "#FFD54F", "06": "#CE93D8",
    "07": "#FFAB40", "08": "#F06292", "09": "#90A4AE",
    "10": "#9575CD", "11": "#78909C",
}
SECTION_NAMES = {
    "01": "Личность", "02": "Внутренний мир", "03": "Идеи и мысли",
    "04": "Цели и задачи", "05": "Знания и навыки", "06": "Проекты",
    "07": "Жизнь", "08": "Социальный капитал", "09": "Шаблоны",
    "10": "Claude", "11": "Архив",
}


# --- DB ---

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # Индексация теперь идёт фоном из хука памяти, а дашборд и поиск читают базу
    # в это же время. Без WAL читатель ловит «database is locked» — та же грабля,
    # что была у вектор-базы Zotero.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            chunk_idx INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(path, chunk_idx)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON notes(path)")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
        USING fts5(path, content, content='notes', content_rowid='id', tokenize='unicode61')
    """)
    conn.commit()
    return conn


def pack_embedding(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# --- Chunking ---

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return chunks


def extract_text(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        if raw.startswith("---"):
            end = raw.find("---", 3)
            if end != -1:
                raw = raw[end + 3:].lstrip()
        return raw.strip()
    except Exception:
        return ""


# --- Model ---

_model = None

def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    m = get_model()
    vecs = m.encode(texts, batch_size=32, show_progress_bar=False, normalize_embeddings=True)
    return vecs.tolist()


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def get_section(path: str) -> str:
    part = path.split("/")[0] if "/" in path else path
    for key in SECTION_COLORS:
        if part.startswith(key):
            return key
    return "00"


# --- Commands ---

def cmd_index(args):
    conn = get_conn()
    md_files = [f for f in VAULT.rglob("*.md")
                if not str(f.relative_to(VAULT)).startswith(EXCLUDE_SECTIONS)]
    to_process = []
    for f in md_files:
        rel = str(f.relative_to(VAULT))
        text = extract_text(f)
        if not text:
            continue
        h = hashlib.md5(text.encode()).hexdigest()
        row = conn.execute("SELECT hash FROM notes WHERE path=? AND chunk_idx=0", (rel,)).fetchone()
        if row is None or row[0] != h:
            to_process.append((rel, f, text, h))

    # Заметки, которых больше нет по этому пути (удалены или раздел переехал в
    # другой номер), иначе поиск и граф тянут мёртвые пути.
    alive = {str(f.relative_to(VAULT)) for f in md_files}
    gone = [r[0] for r in conn.execute("SELECT DISTINCT path FROM notes")
            if r[0] not in alive]
    for rel in gone:
        conn.execute("DELETE FROM notes WHERE path=?", (rel,))
    if gone:
        conn.commit()

    if not to_process:
        if gone:
            conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
            conn.commit()
        print(json.dumps({"status": "up_to_date", "files": len(md_files),
                          "pruned": len(gone)}))
        return

    print(json.dumps({"status": "indexing", "files_to_update": len(to_process)}), flush=True)
    now = int(time.time())
    for rel, f, text, h in to_process:
        chunks = chunk_text(text)
        vecs = embed(chunks)
        conn.execute("DELETE FROM notes WHERE path=?", (rel,))
        for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
            conn.execute(
                "INSERT INTO notes(path, chunk_idx, content, hash, embedding, updated_at) VALUES (?,?,?,?,?,?)",
                (rel, i, chunk, h, pack_embedding(vec), now)
            )
        conn.commit()
    conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print(json.dumps({"status": "done", "indexed": len(to_process),
                      "pruned": len(gone)}))


def cmd_search(args):
    query = args[0] if args else ""
    limit = int(args[1]) if len(args) > 1 else DEFAULT_LIMIT
    threshold = float(args[2]) if len(args) > 2 else DEFAULT_THRESHOLD
    if not query:
        print(json.dumps({"error": "query required"})); return

    conn = get_conn()
    rows = conn.execute("SELECT path, chunk_idx, content, embedding FROM notes").fetchall()
    if not rows:
        print(json.dumps({"error": "index empty, run index first"})); return

    q_vec = embed([query])[0]
    scores = []
    for path, chunk_idx, content, emb_blob in rows:
        score = cosine(q_vec, unpack_embedding(emb_blob))
        if score >= threshold:
            scores.append((score, path, content))
    scores.sort(reverse=True)

    seen = {}
    for score, path, content in scores:
        if path not in seen:
            seen[path] = (score, content)

    results = [{"path": p, "score": round(s, 3), "snippet": c[:300].replace("\n", " ").strip()}
               for p, (s, c) in sorted(seen.items(), key=lambda x: -x[1][0])[:limit]]
    print(json.dumps({"query": query, "results": results}, ensure_ascii=False, indent=2))


def _fts_match_q(query):
    """MATCH-строка FTS5 из слов запроса (каждое в кавычках, OR). Защищает от
    спецсимволов FTS-синтаксиса и даёт широкий keyword-recall."""
    import re
    toks = re.findall(r"\w+", query or "", re.UNICODE)
    return " OR ".join(f'"{t}"' for t in toks) if toks else None


def cmd_hybrid(args):
    """Гибрид FTS5(BM25) + вектор со слиянием RRF — как в vectorize.py, чтобы все
    хранилища контекста искали одинаково. RRF(d)=Σ 1/(k+rank_i)."""
    query = args[0] if args else ""
    limit = next((int(x) for x in args[1:] if x.isdigit()), DEFAULT_LIMIT)
    mode = args[args.index("--mode") + 1] if "--mode" in args else "hybrid"
    if not query:
        print(json.dumps({"error": "query required"})); return

    conn = get_conn()
    if mode == "fts":                     # чистый BM25 — без модели, быстрый
        m = _fts_match_q(query)
        results, seen = [], set()
        if m:
            try:
                for p, content in conn.execute(
                        "SELECT path, content FROM notes_fts WHERE notes_fts MATCH ? "
                        "ORDER BY bm25(notes_fts) LIMIT ?", (m, limit * 8)):
                    if p in seen:
                        continue
                    seen.add(p)
                    results.append({"path": p, "score": round(1.0 / len(seen), 4),
                                    "method": "fts",
                                    "snippet": (content or "")[:300].replace("\n", " ").strip()})
                    if len(results) >= limit:
                        break
            except Exception:
                pass
        print(json.dumps({"query": query, "mode": mode, "results": results},
                         ensure_ascii=False, indent=2))
        return

    rows = conn.execute("SELECT path, content, embedding FROM notes").fetchall()
    if not rows:
        print(json.dumps({"error": "index empty, run index first"})); return

    q_vec = embed([query])[0]
    best = {}   # path -> (score, content) — лучший чанк заметки
    for path, content, emb_blob in rows:
        score = cosine(q_vec, unpack_embedding(emb_blob))
        if path not in best or score > best[path][0]:
            best[path] = (score, content)
    vec_rank = [p for p, _ in sorted(best.items(), key=lambda x: -x[1][0])]

    fts_rank, seen = [], set()
    m = _fts_match_q(query)
    if m:
        try:
            for (p,) in conn.execute(
                    "SELECT path FROM notes_fts WHERE notes_fts MATCH ? "
                    "ORDER BY bm25(notes_fts) LIMIT ?", (m, limit * 8)):
                if p not in seen:
                    seen.add(p); fts_rank.append(p)
        except Exception:
            pass

    k, scores = 60, {}
    for r, p in enumerate(vec_rank, 1):
        scores[p] = scores.get(p, 0.0) + 1.0 / (k + r)
    for r, p in enumerate(fts_rank, 1):
        scores[p] = scores.get(p, 0.0) + 1.0 / (k + r)
    vset, fset = set(vec_rank), set(fts_rank)
    if mode == "vector":
        order = vec_rank[:limit]
    elif mode == "fts":
        order = fts_rank[:limit]
    else:
        order = sorted(scores, key=lambda p: -scores[p])[:limit]
    results = []
    for rank, p in enumerate(order, 1):
        if mode == "vector":
            sc, meth = round(best[p][0], 4), "vector"
        elif mode == "fts":
            sc, meth = round(1.0 / rank, 4), "fts"
        else:
            sc = round(scores[p], 4)
            meth = ("vector+fts" if p in vset and p in fset
                    else "vector" if p in vset else "fts")
        results.append({"path": p, "score": sc, "method": meth,
                        "snippet": best.get(p, (0, ""))[1][:300].replace("\n", " ").strip()})
    print(json.dumps({"query": query, "mode": mode, "results": results},
                     ensure_ascii=False, indent=2))


def _load_chunks(conn, cap=CHUNK_CAP):
    """Load up to `cap` chunks per note. Returns list of dicts with embedding."""
    rows = conn.execute(
        "SELECT path, chunk_idx, content, embedding, hash FROM notes ORDER BY path, chunk_idx"
    ).fetchall()
    by_note = {}
    for path, idx, content, emb, h in rows:
        by_note.setdefault(path, []).append((idx, content, emb, h))
    chunks = []
    for path, lst in by_note.items():
        lst.sort(key=lambda x: x[0])
        for idx, content, emb, h in lst[:cap]:
            chunks.append({
                "id": f"{path}#{idx}",
                "path": path,
                "chunk_idx": idx,
                "content": content,
                "embedding": unpack_embedding(emb),
                "hash": h,
            })
    return chunks


def _dedup_chunks(chunks, sim=DEDUP_SIM, stem_sim=DEDUP_STEM_SIM):
    """Drop duplicate notes (same content copied across folders) before linking.

    Two notes are duplicates if: identical content hash; OR same filename stem
    plus high max-similarity; OR near-identical content (sim>=DEDUP_SIM) even if
    renamed. The canonical copy is kept (Архив/09 and Claude workspace/10 are
    deprioritised), the rest are dropped. Returns (kept_chunks, dropped_map).
    """
    import numpy as np
    by_note, hashes = {}, {}
    for c in chunks:
        by_note.setdefault(c["path"], []).append(c["embedding"])
        hashes[c["path"]] = c.get("hash")
    mats = {p: np.array(v, dtype=np.float32) for p, v in by_note.items()}

    def rank(p):
        sec = get_section(p)
        base = 100 if sec == "09" else 50 if sec == "10" else 0
        return (base, len(p))

    order = sorted(by_note.keys(), key=rank)
    keep, drop = [], {}
    for p in order:
        rep = None
        for k in keep:
            if hashes[p] and hashes[p] == hashes[k]:
                rep = k; break
            same_stem = Path(p).stem == Path(k).stem
            s = float((mats[p] @ mats[k].T).max())
            if s >= sim or (same_stem and s >= stem_sim):
                rep = k; break
        if rep is None:
            keep.append(p)
        else:
            drop[p] = rep
    keep_set = set(keep)
    return [c for c in chunks if c["path"] in keep_set], drop


def cmd_brainstorm(args):
    """Find hidden cross-section connections via chunk-level max-similarity.

    Note↔note similarity = max cosine over all pairs of their chunks, so a
    connection is found even when the shared idea sits deep inside a long note
    (not just in its first chunk).
    """
    query = args[0] if args else ""
    limit = int(args[1]) if len(args) > 1 else 15
    threshold = float(args[2]) if len(args) > 2 else 0.65
    import numpy as np

    conn = get_conn()
    chunks = _load_chunks(conn, CHUNK_CAP)
    if not chunks:
        print(json.dumps({"error": "index empty, run index first"})); return
    dropped = 0
    if DEDUP:
        chunks, drop = _dedup_chunks(chunks)
        dropped = len(drop)

    emb = np.array([c["embedding"] for c in chunks], dtype=np.float32)
    note_chunks = {}
    for i, c in enumerate(chunks):
        note_chunks.setdefault(c["path"], []).append(i)
    paths = list(note_chunks.keys())

    # If query given — focus on notes whose best chunk matches the query
    if query:
        qv = np.array(embed([query])[0], dtype=np.float32)
        qscore = {p: float((emb[idx] @ qv).max()) for p, idx in note_chunks.items()}
        focus = sorted(paths, key=lambda p: -qscore[p])[:100]
    else:
        focus = paths

    # Find cross-section pairs with high max-sim between their chunks
    hidden = []
    for i in range(len(focus)):
        for j in range(i + 1, len(focus)):
            p1, p2 = focus[i], focus[j]
            s1, s2 = get_section(p1), get_section(p2)
            if s1 == s2:
                continue  # same section — not "hidden"
            score = float((emb[note_chunks[p1]] @ emb[note_chunks[p2]].T).max())
            if score >= threshold:
                hidden.append({
                    "score": round(score, 3),
                    "note1": p1,
                    "section1": SECTION_NAMES.get(s1, s1),
                    "note2": p2,
                    "section2": SECTION_NAMES.get(s2, s2),
                    "bridge": f"{SECTION_NAMES.get(s1, s1)} ↔ {SECTION_NAMES.get(s2, s2)}"
                })

    hidden.sort(key=lambda x: -x["score"])

    # Group by bridge type
    bridges = {}
    for h in hidden:
        k = tuple(sorted([h["section1"], h["section2"]]))
        bridges.setdefault(k, []).append(h)

    bridge_summary = [
        {"bridge": f"{k[0]} ↔ {k[1]}", "count": len(v), "top_score": v[0]["score"]}
        for k, v in sorted(bridges.items(), key=lambda x: -x[1][0]["score"])
    ]

    print(json.dumps({
        "query": query or "all vault",
        "hidden_connections": hidden[:limit],
        "bridge_summary": bridge_summary[:10],
        "total_found": len(hidden),
        "duplicates_dropped": dropped
    }, ensure_ascii=False, indent=2))


def _compute_positions(paths, edges_list, width=3000, height=3000):
    """Compute node positions using networkx spring layout."""
    import math, random
    try:
        import networkx as nx
        import numpy as np
        G = nx.Graph()
        G.add_nodes_from(paths)
        for e in edges_list:
            G.add_edge(e["source"], e["target"], weight=e["weight"])
        print(json.dumps({"status": "computing_layout"}), flush=True)
        pos = nx.spring_layout(G, k=2.5/math.sqrt(len(paths)),
                               iterations=60, seed=42, weight="weight")
        result = {}
        for p, (x, y) in pos.items():
            result[p] = (
                round((x + 1) / 2 * width * 0.9 + width * 0.05, 1),
                round((y + 1) / 2 * height * 0.9 + height * 0.05, 1)
            )
        return result
    except Exception:
        # Fallback: galaxy layout by section
        sections = {}
        for p in paths:
            s = get_section(p)
            sections.setdefault(s, []).append(p)
        section_list = sorted(sections.keys())
        n_sec = len(section_list)
        cx, cy = width / 2, height / 2
        R = min(width, height) * 0.36
        result = {}
        for si, sec in enumerate(section_list):
            angle = (si / n_sec) * 2 * math.pi - math.pi / 2
            sx = cx + R * math.cos(angle)
            sy = cy + R * math.sin(angle)
            sec_nodes = sections[sec]
            r_inner = min(50 + len(sec_nodes) * 2.5, 200)
            for ni, node_id in enumerate(sec_nodes):
                na = (ni / max(len(sec_nodes), 1)) * 2 * math.pi
                result[node_id] = (
                    round(sx + r_inner * math.cos(na) + random.uniform(-20, 20), 1),
                    round(sy + r_inner * math.sin(na) + random.uniform(-20, 20), 1)
                )
        return result


def cmd_build_graph(args):
    """Build a chunk-level semantic graph: graph.json + graph.html.

    Nodes are individual chunks (path#idx), so the graph shows *which part* of a
    note links to which part of another. Edges are the top-k most similar chunks
    above `threshold`, computed in numpy batches (no full NxN matrix in memory).
    Consecutive chunks of the same note get weak layout-only edges so a note's
    chunks cluster together visually.
    """
    threshold = float(args[0]) if args else 0.55
    top_k = int(args[1]) if len(args) > 1 else 8
    cap = int(args[2]) if len(args) > 2 else CHUNK_CAP

    import numpy as np

    conn = get_conn()
    chunks = _load_chunks(conn, cap)
    conn.close()

    if not chunks:
        print(json.dumps({"error": "index empty, run index first"})); return

    dropped = 0
    if DEDUP:
        chunks, drop = _dedup_chunks(chunks)
        dropped = len(drop)

    n = len(chunks)
    total_notes = len({c["path"] for c in chunks})
    print(json.dumps({"status": "building_chunk_graph", "chunks": n,
                      "notes": total_notes, "cap": cap,
                      "duplicates_dropped": dropped}), flush=True)

    ids = [c["id"] for c in chunks]
    emb = np.array([c["embedding"] for c in chunks], dtype=np.float32)

    # Batched top-k retrieval — keep only strong neighbours per chunk
    edge_set = {}
    BATCH = 1024
    kk = min(top_k, n - 1)
    for s in range(0, n, BATCH):
        e = min(s + BATCH, n)
        sims = emb[s:e] @ emb.T  # (b, n)
        for bi in range(e - s):
            i = s + bi
            row = sims[bi]
            row[i] = -1.0  # exclude self
            cand = np.argpartition(row, -kk)[-kk:] if kk < n else np.arange(n)
            for j in cand:
                j = int(j)
                score = float(row[j])
                if score < threshold:
                    continue
                a, b = (i, j) if i < j else (j, i)
                if a == b:
                    continue
                if edge_set.get((a, b), -1) < score:
                    edge_set[(a, b)] = score

    edges = [{"source": ids[i], "target": ids[j], "weight": round(w, 3)}
             for (i, j), w in edge_set.items()]

    # Layout edges = displayed edges + weak links between consecutive chunks
    # of the same note (so a note's chunks stay near each other).
    by_note_idx = {}
    for i, c in enumerate(chunks):
        by_note_idx.setdefault(c["path"], []).append(i)
    layout_edges = list(edges)
    for path, lst in by_note_idx.items():
        lst.sort(key=lambda i: chunks[i]["chunk_idx"])
        for a, b in zip(lst, lst[1:]):
            layout_edges.append({"source": ids[a], "target": ids[b], "weight": 0.4})

    positions = _compute_positions(ids, layout_edges)

    # Degrees from displayed edges only
    degree = {}
    for (i, j) in edge_set:
        degree[i] = degree.get(i, 0) + 1
        degree[j] = degree.get(j, 0) + 1

    note_chunk_count = {p: len(l) for p, l in by_note_idx.items()}
    nodes = []
    for i, c in enumerate(chunks):
        section = get_section(c["path"])
        px, py = positions.get(c["id"], (1500, 1500))
        stem = Path(c["path"]).stem
        multi = note_chunk_count[c["path"]] > 1
        label = f"{stem} #{c['chunk_idx']}" if multi else stem
        nodes.append({
            "id": c["id"],
            "label": label,
            "note": stem,
            "path": c["path"],
            "section": section,
            "section_name": SECTION_NAMES.get(section, "Другое"),
            "color": SECTION_COLORS.get(section, "#CCCCCC"),
            "degree": degree.get(i, 0),
            "snippet": c["content"][:320].replace("\n", " ").strip(),
            "x": px, "y": py
        })

    # Full note texts (read from disk, cleaned of frontmatter) for the in-graph reader.
    NOTE_TEXT_CAP = 40000
    note_full = {}
    for path in {c["path"] for c in chunks}:
        txt = extract_text(VAULT / path)
        if len(txt) > NOTE_TEXT_CAP:
            txt = txt[:NOTE_TEXT_CAP] + "\n\n… [текст обрезан, всего " + str(len(txt)) + " симв.]"
        note_full[path] = txt

    graph_data = {"nodes": nodes, "edges": edges, "meta": {
        "total_notes": total_notes, "total_chunks": n, "total_edges": len(edges),
        "threshold": threshold, "top_k": top_k, "cap": cap,
        "duplicates_dropped": dropped, "built_at": int(time.time())
    }}

    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    graph_json_path = GRAPH_DIR / "graph.json"
    graph_html_path = GRAPH_DIR / "graph.html"

    with open(graph_json_path, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)

    html = _generate_html(graph_data, note_full)
    with open(graph_html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(json.dumps({
        "status": "done",
        "chunks": n,
        "notes": total_notes,
        "edges": len(edges),
        "cap": cap,
        "duplicates_dropped": dropped,
        "graph_json": str(graph_json_path),
        "graph_html": str(graph_html_path)
    }, ensure_ascii=False, indent=2))


def _generate_html(graph_data: dict, note_full: dict = None) -> str:
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]
    meta = graph_data.get("meta", {})
    # Escape "<" as < so note content like "</script>", "<script" or "<!--"
    # cannot break out of the inline <script> block (HTML parser script-data states).
    def _safe(obj):
        return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    notes_json = _safe(note_full or {})
    nodes_json = _safe(nodes)
    edges_json = _safe(edges)
    sec_colors = _safe(SECTION_COLORS)
    sec_names = _safe(SECTION_NAMES)
    meta_json = _safe(meta)
    tpl = r"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Органон — Semantic Graph</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--panel:rgba(20,22,28,0.78);--line:rgba(255,255,255,0.08);--txt:#e6e9ef;--mut:#828893;--accent:#6aa6e0;--bg1:#0d0f13;--bg2:#08090c}
body.light{--panel:rgba(255,255,255,0.82);--line:rgba(20,30,50,0.12);--txt:#1c2129;--mut:#737a86;--accent:#2f6db0;--bg1:#f4f6fa;--bg2:#e6eaf1}
html{background:var(--bg2)}
body{height:100vh;background:linear-gradient(160deg,var(--bg1),var(--bg2));color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;font-size:14px;overflow:hidden;transition:background .25s,color .25s}
#layout{display:flex;height:100vh}
#sidebar{flex:0 0 296px;background:var(--panel);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow:hidden}
#sidebar.hidden{display:none}
#cpanel{position:absolute;top:14px;left:14px;z-index:4}
#topbar{padding:15px 16px 11px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:11px}
#topbar h1{font-size:11.5px;letter-spacing:.14em;color:var(--mut);font-weight:600;text-transform:uppercase}
#searchbox{background:transparent;border:1px solid var(--line);border-radius:8px;padding:9px 12px;color:var(--txt);font-size:13px;outline:none;transition:border-color .15s,box-shadow .15s}
#searchbox:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(106,166,224,.14)}
.sec-title{font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:var(--mut);padding:13px 16px 5px}
#filters{display:flex;flex-direction:column;gap:1px;overflow-y:auto;padding:0 8px 8px}
.sec-btn{display:flex;align-items:center;gap:9px;padding:7px 10px;cursor:pointer;font-size:12.5px;border:none;background:none;color:var(--txt);text-align:left;width:100%;border-radius:6px;transition:background .12s,opacity .12s}
.sec-btn:hover{background:var(--line)}
.sec-btn.off{opacity:0.3}
.sec-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.sec-count{margin-left:auto;color:var(--mut);font-size:11px;font-variant-numeric:tabular-nums}
#info{padding:13px 16px;border-top:1px solid var(--line);overflow-y:auto;max-height:48%;font-size:12px;line-height:1.55;color:var(--mut);display:none}
#info h3{font-size:13px;color:var(--txt);margin-bottom:6px;word-break:break-word;line-height:1.3}
#info .meta{color:var(--mut);font-size:11px;margin-bottom:3px}
#info .snip{color:var(--txt);opacity:.8;margin:8px 0 4px;font-size:11.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word;max-height:230px;overflow-y:auto;border-left:2px solid var(--line);padding-left:9px}
#info .opennote{margin-top:9px;width:100%;padding:8px;border-radius:7px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-size:12px;cursor:pointer;transition:background .12s}
#info .opennote:hover{background:var(--accent);color:#fff}
#info .nbh{margin:11px 0 5px;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut)}
#reader{position:absolute;inset:0;z-index:9;display:none;align-items:center;justify-content:center}
#reader.open{display:flex}
#reader .bg{position:absolute;inset:0;background:rgba(0,0,0,0.55)}
body.light #reader .bg{background:rgba(20,30,50,0.35)}
#reader .box{position:relative;width:min(760px,86%);max-height:84%;display:flex;flex-direction:column;background:var(--panel);backdrop-filter:blur(18px);border:1px solid var(--line);border-radius:14px;box-shadow:0 24px 70px rgba(0,0,0,.5);overflow:hidden}
#reader .rhd{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:12px}
#reader .rhd h2{font-size:15px;color:var(--txt);line-height:1.35;word-break:break-word;flex:1}
#reader .rhd .rsub{color:var(--mut);font-size:11px;margin-top:3px}
#reader .rclose{flex-shrink:0;width:30px;height:30px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--txt);font-size:17px;cursor:pointer}
#reader .rclose:hover{background:var(--line)}
#reader .rbody{padding:18px 22px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;font-size:13.5px;line-height:1.7;color:var(--txt)}
.nb{display:flex;align-items:center;gap:7px;padding:4px 6px;border-radius:5px;cursor:pointer;color:var(--txt);font-size:12px}
.nb:hover{background:var(--line)}
.nbw{font-variant-numeric:tabular-nums;color:var(--accent);font-size:11px;width:30px;flex-shrink:0}
.nbc{width:8px;height:8px;border-radius:2px;flex-shrink:0}
.nb .same{color:var(--mut);font-size:10px}
#stats{padding:9px 16px;font-size:10.5px;color:var(--mut);border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
#canvas-wrap{flex:1;position:relative;overflow:hidden}
canvas{display:block;width:100%;height:100%;cursor:grab}
canvas:active{cursor:grabbing}
#tip{position:absolute;pointer-events:none;background:var(--panel);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:7px;padding:6px 9px;font-size:12px;color:var(--txt);max-width:260px;opacity:0;transition:opacity .1s;z-index:5;box-shadow:0 6px 22px rgba(0,0,0,.28)}
#tip .t-note{color:var(--mut);font-size:10.5px;margin-top:2px}
#controls{position:absolute;top:14px;right:14px;display:flex;flex-direction:column;gap:6px;z-index:4}
.ctl{width:34px;height:34px;border-radius:8px;border:1px solid var(--line);background:var(--panel);backdrop-filter:blur(10px);color:var(--txt);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .12s,color .12s,border-color .12s}
.ctl:hover{background:var(--line)}
.ctl.active{color:var(--accent);border-color:var(--accent)}
#hint{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:var(--panel);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:7px;padding:5px 14px;font-size:11px;color:var(--mut);pointer-events:none}
</style></head><body>
<div id="layout">
<div id="sidebar">
  <div id="topbar">
    <h1>Органон · Semantic Graph</h1>
    <input id="searchbox" placeholder="поиск по заметкам и тексту…">
  </div>
  <div class="sec-title">Разделы</div>
  <div id="filters"></div>
  <div id="info"></div>
  <div id="stats"></div>
</div>
<div id="canvas-wrap">
  <canvas id="cv"></canvas>
  <div id="tip"></div>
  <button class="ctl" id="cpanel" title="Скрыть / показать панель (Tab)">&#8676;</button>
  <div id="controls">
    <button class="ctl" id="zin" title="Приблизить">+</button>
    <button class="ctl" id="zout" title="Отдалить">&#8722;</button>
    <button class="ctl" id="cfit" title="Показать всё (F)">&#9633;</button>
    <button class="ctl active" id="clabels" title="Подписи заметок (L)">Aa</button>
    <button class="ctl" id="ctheme" title="Светлая / тёмная тема (T)">&#9790;</button>
    <button class="ctl" id="cmotion" title="Движение (пробел)">&#10059;</button>
  </div>
  <div id="hint">колесо — зум · перетаскивание — панорама · клик — детали · двойной клик — фокус</div>
  <div id="reader"><div class="bg"></div><div class="box"><div class="rhd"><div><h2 id="rtitle"></h2><div class="rsub" id="rsub"></div></div><button class="rclose" id="rclose" title="Закрыть (Esc)">&#10005;</button></div><div class="rbody" id="rbody"></div></div></div>
</div>
</div>
<script>
const NODES=__NODES__;
const EDGES=__EDGES__;
const SEC_COLORS=__SEC_COLORS__;
const SEC_NAMES=__SEC_NAMES__;
const META=__META__;
const NOTES=__NOTES__;

const THEMES={
  dark:{add:false,edge:'140,190,235',edgeA:0.14,edgeADim:0.045,labelBg:'rgba(8,11,16,0.82)',labelBd:'rgba(130,180,225,0.18)',labelTx:'rgba(218,228,240,0.86)',labelHi:'#eaf4ff',ring:'#cfe6ff',hi:'120,195,255',part:'rgba(160,215,255,0.95)',mmBg:'rgba(8,10,14,0.74)',mmBd:'rgba(130,180,225,0.14)',mmView:'rgba(120,195,255,0.8)',node:'rgba(140,195,245,0.0)'},
  light:{add:false,edge:'45,70,105',edgeA:0.18,edgeADim:0.06,labelBg:'rgba(255,255,255,0.9)',labelBd:'rgba(20,40,75,0.16)',labelTx:'rgba(36,44,56,0.9)',labelHi:'#0a0e16',ring:'#15233a',hi:'25,95,170',part:'rgba(20,85,160,0.92)',mmBg:'rgba(255,255,255,0.82)',mmBd:'rgba(20,40,75,0.14)',mmView:'rgba(25,95,170,0.85)',node:'rgba(15,35,65,0.14)'}
};
let theme=THEMES.dark;

const cv=document.getElementById('cv'),ctx=cv.getContext('2d'),tip=document.getElementById('tip');
let W,H,DPR=Math.min(window.devicePixelRatio||1,2);
function resize(){const r=cv.getBoundingClientRect();cv.width=r.width*DPR;cv.height=r.height*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);W=r.width;H=r.height;if(typeof requestDraw==='function')requestDraw();}
new ResizeObserver(resize).observe(cv);resize();

const WORLD=3000,PAD=90,N=NODES.length;
const nodeMap={};
NODES.forEach((n,i)=>{n._i=i;nodeMap[n.id]=n;});
const xs=NODES.map(n=>n.x),ys=NODES.map(n=>n.y);
const xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs),yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
NODES.forEach(n=>{
  n.hx=PAD+(n.x-xMin)/((xMax-xMin)||1)*(WORLD-PAD*2);
  n.hy=PAD+(n.y-yMin)/((yMax-yMin)||1)*(WORLD-PAD*2);
  n.ph=Math.random()*6.28;n.fr=0.3+Math.random()*0.4;n.am=2+Math.random()*3.5;n.dly=Math.random()*0.4;
});
EDGES.forEach(e=>{e.a=nodeMap[e.source];e.b=nodeMap[e.target];});
const adj={};
EDGES.forEach(e=>{(adj[e.source]||(adj[e.source]=[])).push([e.target,e.weight]);(adj[e.target]||(adj[e.target]=[])).push([e.source,e.weight]);});

let scale=1,tx=0,ty=0,tScale=1,tTx=0,tTy=0;
function fit(anim){const s=Math.min(W,H)/(WORLD*1.06);const ax=W/2-WORLD/2*s,ay=H/2-WORLD/2*s;tScale=s;tTx=ax;tTy=ay;if(!anim){scale=s;tx=ax;ty=ay;}}
fit(false);

let hiddenSec=new Set(),filterText='',hovered=null,selected=null,motion=false,labelsOn=true;
let isPan=false,panSX=0,panSY=0,panTX=0,panTY=0,moved=false;
const introStart=performance.now(),INTRO=1100;
let nowT=performance.now();
const WX=new Float64Array(N),WY=new Float64Array(N),SX=new Float64Array(N),SY=new Float64Array(N);
let mmRect=null;const MM=152,MMP=12;

function esc(s){return (s||'').replace(/[&<>]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':'&gt;');}
function ease(t){t=t<0?0:t>1?1:t;return 1-Math.pow(1-t,3);}
function fromScreen(sx,sy){return [(sx-tx)/scale,(sy-ty)/scale];}
function vis(n){if(hiddenSec.has(n.section))return false;if(!filterText)return true;return n.label.toLowerCase().indexOf(filterText)>=0||((n.text||n.snippet||'').toLowerCase().indexOf(filterText)>=0);}
function rscale(){return Math.max(0.5,Math.min(1.7,scale*1.6));}
function radius(n){return Math.max(1.3,Math.min(6.5,1.4+Math.sqrt(n.degree)*0.95));}
function rrect(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}

function compute(){
  const it0=(nowT-introStart)/INTRO,tt=nowT/1000;
  for(let i=0;i<N;i++){
    const n=NODES[i];let hx=n.hx,hy=n.hy;
    const it=ease((it0-n.dly)/(1-n.dly));
    if(it<1){hx=WORLD/2+(hx-WORLD/2)*it;hy=WORLD/2+(hy-WORLD/2)*it;}
    if(motion){hx+=Math.sin(tt*n.fr+n.ph)*n.am;hy+=Math.cos(tt*n.fr*0.9+n.ph)*n.am;}
    WX[i]=hx;WY[i]=hy;SX[i]=hx*scale+tx;SY[i]=hy*scale+ty;
  }
}

function draw(){
  nowT=performance.now();
  scale+=(tScale-scale)*0.16;tx+=(tTx-tx)*0.16;ty+=(tTy-ty)*0.16;
  compute();
  ctx.clearRect(0,0,W,H);
  const hl=selected?new Set([selected.id].concat((adj[selected.id]||[]).map(a=>a[0]))):null;
  const rs=rscale(),m=90;

  // edges — thin, theme-aware (no additive blend: too costly on 26k lines)
  ctx.strokeStyle='rgba('+theme.edge+','+(hl?theme.edgeADim:theme.edgeA)+')';
  ctx.lineWidth=Math.max(0.35,scale*0.55);
  ctx.beginPath();
  for(let k=0;k<EDGES.length;k++){const e=EDGES[k],a=e.a,b=e.b;if(!vis(a)||!vis(b))continue;
    if(hl&&hl.has(e.source)&&hl.has(e.target))continue;
    const ax=SX[a._i],ay=SY[a._i],bx=SX[b._i],by=SY[b._i];
    if((ax<-m&&bx<-m)||(ax>W+m&&bx>W+m)||(ay<-m&&by<-m)||(ay>H+m&&by>H+m))continue;
    ctx.moveTo(ax,ay);ctx.lineTo(bx,by);
  }
  ctx.stroke();

  // highlighted edges (focus) — accent, straight, crisp
  if(hl){
    for(let k=0;k<EDGES.length;k++){const e=EDGES[k];if(!(hl.has(e.source)&&hl.has(e.target)))continue;if(!vis(e.a)||!vis(e.b))continue;
      const ax=SX[e.a._i],ay=SY[e.a._i],bx=SX[e.b._i],by=SY[e.b._i];
      ctx.strokeStyle='rgba('+theme.hi+','+(0.3+0.5*e.weight)+')';ctx.lineWidth=0.7+1.3*e.weight;
      ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(bx,by);ctx.stroke();
    }
    if(motion){
      ctx.fillStyle=theme.part;
      for(let k=0;k<EDGES.length;k++){const e=EDGES[k];if(!(hl.has(e.source)&&hl.has(e.target)))continue;if(!vis(e.a)||!vis(e.b))continue;
        const ax=SX[e.a._i],ay=SY[e.a._i],bx=SX[e.b._i],by=SY[e.b._i],t=((nowT/1500)+k*0.13)%1;
        ctx.beginPath();ctx.arc(ax+(bx-ax)*t,ay+(by-ay)*t,1.6,0,6.3);ctx.fill();
      }
    }
  }

  // subtle glow only on focus (selected + neighbours + hovered)
  function glow(n,strong){const i=n._i,sx=SX[i],sy=SY[i];if(sx<-40||sx>W+40||sy<-40||sy>H+40)return;const r=Math.max(2,radius(n)*rs)*3.2;const g=ctx.createRadialGradient(sx,sy,0,sx,sy,r);g.addColorStop(0,n.color);g.addColorStop(1,'rgba(0,0,0,0)');ctx.globalAlpha=strong?0.42:0.18;ctx.fillStyle=g;ctx.beginPath();ctx.arc(sx,sy,r,0,6.3);ctx.fill();ctx.globalAlpha=1;}
  if(selected){glow(selected,true);(adj[selected.id]||[]).forEach(a=>{const nn=nodeMap[a[0]];if(nn&&vis(nn))glow(nn,false);});}
  if(hovered&&hovered!==selected)glow(hovered,true);

  // nodes batched by colour, with hairline outline for definition
  const nb={};
  for(let i=0;i<N;i++){const n=NODES[i];if(!vis(n))continue;const sx=SX[i],sy=SY[i];if(sx<-30||sx>W+30||sy<-30||sy>H+30)continue;(nb[n.color]||(nb[n.color]=[])).push(i);}
  ctx.lineWidth=1;
  for(const c in nb){const arr=nb[c];
    ctx.fillStyle=c;ctx.globalAlpha=1;ctx.beginPath();
    for(let q=0;q<arr.length;q++){const i=arr[q];if(hl&&!hl.has(NODES[i].id))continue;const r=Math.max(1.4,radius(NODES[i])*rs);ctx.moveTo(SX[i]+r,SY[i]);ctx.arc(SX[i],SY[i],r,0,6.3);}
    ctx.fill();ctx.strokeStyle=theme.node;ctx.stroke();
    if(hl){ctx.globalAlpha=0.1;ctx.beginPath();for(let q=0;q<arr.length;q++){const i=arr[q];if(hl.has(NODES[i].id))continue;const r=Math.max(1.2,radius(NODES[i])*rs);ctx.moveTo(SX[i]+r,SY[i]);ctx.arc(SX[i],SY[i],r,0,6.3);}ctx.fill();ctx.globalAlpha=1;}
  }

  [selected,hovered].forEach(n=>{if(!n||!vis(n))return;const i=n._i,r=Math.max(2,radius(n)*rs);ctx.strokeStyle=theme.ring;ctx.lineWidth=1.6;ctx.beginPath();ctx.arc(SX[i],SY[i],r+3,0,6.3);ctx.stroke();});

  drawLabels(hl,rs);
  drawMinimap();
}

function drawLabels(hl,rs){
  const minDeg=scale>0.6?1:scale>0.35?3:scale>0.2?7:14;
  const seen=new Set(),list=[];
  function add(n){if(!n||seen.has(n.id))return;seen.add(n.id);if(!vis(n))return;const i=n._i;if(SX[i]<-4||SX[i]>W+4||SY[i]<-4||SY[i]>H+4)return;if(hl&&!hl.has(n.id)&&n!==hovered)return;list.push(n);}
  // focus labels always available so you can read what you click/hover
  if(selected){add(selected);(adj[selected.id]||[]).forEach(a=>add(nodeMap[a[0]]));}
  add(hovered);
  // ambient note labels — only when enabled
  if(labelsOn&&!hl){for(let i=0;i<N;i++){if(NODES[i].degree>=minDeg)add(NODES[i]);}}
  list.sort((a,b)=>b.degree-a.degree);
  const cap=Math.min(70,list.length);
  ctx.font='500 11px -apple-system,system-ui,sans-serif';ctx.textBaseline='middle';
  for(let k=0;k<cap;k++){const n=list[k],i=n._i,r=Math.max(2,radius(n)*rs);const x=SX[i]+r+6,y=SY[i];let t=n.label;if(t.length>36)t=t.slice(0,35)+'…';const w=ctx.measureText(t).width;
    ctx.fillStyle=theme.labelBg;rrect(x-5,y-9,w+10,18,4);ctx.fill();ctx.strokeStyle=theme.labelBd;ctx.lineWidth=1;ctx.stroke();
    ctx.fillStyle=(n===selected||n===hovered)?theme.labelHi:theme.labelTx;ctx.fillText(t,x,y+0.5);
  }
}

function drawMinimap(){
  const x0=W-MM-MMP,y0=H-MM-MMP,s=(MM-12)/WORLD;
  ctx.fillStyle=theme.mmBg;rrect(x0,y0,MM,MM,8);ctx.fill();ctx.strokeStyle=theme.mmBd;ctx.lineWidth=1;ctx.stroke();
  for(let i=0;i<N;i+=2){const n=NODES[i];if(hiddenSec.has(n.section))continue;ctx.globalAlpha=0.55;ctx.fillStyle=n.color;ctx.fillRect(x0+6+WX[i]*s,y0+6+WY[i]*s,1.3,1.3);}
  ctx.globalAlpha=1;
  const a=fromScreen(0,0),b=fromScreen(W,H);
  const vx=Math.max(0,a[0]),vy=Math.max(0,a[1]),vw=Math.min(WORLD,b[0])-vx,vh=Math.min(WORLD,b[1])-vy;
  ctx.strokeStyle=theme.mmView;ctx.lineWidth=1.2;ctx.strokeRect(x0+6+vx*s,y0+6+vy*s,vw*s,vh*s);
  mmRect={x0:x0,y0:y0,s:s};
}

function requestDraw(){}
function loop(){draw();requestAnimationFrame(loop);}
loop();

const filtersEl=document.getElementById('filters');
Object.keys(SEC_NAMES).sort().forEach(sec=>{
  const count=NODES.filter(n=>n.section===sec).length;if(!count)return;
  const btn=document.createElement('button');btn.className='sec-btn';
  btn.innerHTML='<span class="sec-dot" style="background:'+(SEC_COLORS[sec]||'#888')+'"></span>'+esc(SEC_NAMES[sec]||sec)+'<span class="sec-count">'+count+'</span>';
  btn.onclick=()=>{if(hiddenSec.has(sec))hiddenSec.delete(sec);else hiddenSec.add(sec);btn.classList.toggle('off',hiddenSec.has(sec));updStats();requestDraw();};
  filtersEl.appendChild(btn);
});
function updStats(){const v=NODES.filter(vis).length;document.getElementById('stats').textContent=(META.total_chunks||N)+' чанков · '+(META.total_notes||'?')+' заметок · '+EDGES.length+' связей · видно '+v;}
updStats();
document.getElementById('searchbox').addEventListener('input',e=>{filterText=e.target.value.toLowerCase().trim();updStats();requestDraw();});

function showInfo(n){
  const el=document.getElementById('info');el.style.display='block';
  const neigh=(adj[n.id]||[]).slice().sort((a,b)=>b[1]-a[1]).slice(0,12);
  const noteLine=(n.note&&n.note!==n.label)?'<div class="meta">📄 '+esc(n.note)+'</div>':'';
  const linkLine=n.url?'<div class="meta">🔗 <a href="'+esc(n.url)+'" target="_blank" rel="noopener" style="color:#57D9A3;word-break:break-all">'+esc(n.url.replace(/^https?:\/\//,'').slice(0,64))+'</a></div>':'';
  let list='';
  neigh.forEach(a=>{const nn=nodeMap[a[0]];const same=nn&&nn.path===n.path;list+='<div class="nb" data-id="'+encodeURIComponent(a[0])+'"><span class="nbw">'+a[1].toFixed(2)+'</span><span class="nbc" style="background:'+(nn?nn.color:'#888')+'"></span>'+esc(nn?nn.label:a[0])+(same?' <span class="same">· та же заметка</span>':'')+'</div>';});
  const chunkText=n.text||n.snippet||'';
  const hasNote=NOTES&&NOTES[n.path]!=null;
  el.innerHTML='<h3>'+esc(n.label)+'</h3>'+noteLine+linkLine+'<div class="meta">'+esc(n.section_name)+' · '+n.degree+' связей</div><div class="nbh">Текст чанка</div><div class="snip">'+esc(chunkText)+'</div>'+(hasNote?'<button class="opennote" data-path="'+encodeURIComponent(n.path)+'">📄 Открыть полную заметку</button>':'')+(neigh.length?'<div class="nbh">Связанные чанки</div>'+list:'');
  el.querySelectorAll('.nb').forEach(d=>d.onclick=()=>{const nn=nodeMap[decodeURIComponent(d.dataset.id)];if(nn){selected=nn;showInfo(nn);focusNode(nn);requestDraw();}});
  const ob=el.querySelector('.opennote');if(ob)ob.onclick=()=>openNote(decodeURIComponent(ob.dataset.path),n.note);
}
function hideInfo(){document.getElementById('info').style.display='none';}
function focusNode(n){tScale=Math.max(scale,0.7);tTx=W/2-n.hx*tScale;tTy=H/2-n.hy*tScale;}
const reader=document.getElementById('reader');
function openNote(path,title){const t=NOTES&&NOTES[path];if(t==null)return;document.getElementById('rtitle').textContent=title||path;document.getElementById('rsub').textContent=path;const rb=document.getElementById('rbody');rb.textContent=t;rb.scrollTop=0;reader.classList.add('open');}
function closeNote(){reader.classList.remove('open');}
document.getElementById('rclose').onclick=closeNote;
reader.querySelector('.bg').onclick=closeNote;

function pick(mx,my){let best=null,bs=9;for(let i=0;i<N;i++){const n=NODES[i];if(!vis(n))continue;const sx=SX[i],sy=SY[i];if(sx<-20||sx>W+20||sy<-20||sy>H+20)continue;const r=radius(n)*rscale();const d=Math.hypot(sx-mx,sy-my)-r;if(d<bs){bs=d;best=n;}}return best;}
function inMM(mx,my){return mmRect&&mx>=mmRect.x0&&mx<=mmRect.x0+MM&&my>=mmRect.y0&&my<=mmRect.y0+MM;}

cv.addEventListener('mousedown',e=>{
  if(inMM(e.offsetX,e.offsetY)){const wx=(e.offsetX-mmRect.x0-6)/mmRect.s,wy=(e.offsetY-mmRect.y0-6)/mmRect.s;tTx=W/2-wx*tScale;tTy=H/2-wy*tScale;requestDraw();return;}
  moved=false;isPan=true;panSX=e.offsetX;panSY=e.offsetY;panTX=tx;panTY=ty;
});
cv.addEventListener('mousemove',e=>{
  if(isPan){const dx=e.offsetX-panSX,dy=e.offsetY-panSY;if(Math.abs(dx)+Math.abs(dy)>3)moved=true;tx=tTx=panTX+dx;ty=tTy=panTY+dy;requestDraw();return;}
  const n=pick(e.offsetX,e.offsetY);
  if(n!==hovered){hovered=n;cv.style.cursor=n?'pointer':'grab';requestDraw();}
  if(n){tip.style.opacity='1';tip.style.left=(e.offsetX+14)+'px';tip.style.top=(e.offsetY+14)+'px';const sub=(n.note&&n.note!==n.label)?esc(n.note):esc(n.section_name);tip.innerHTML='<div>'+esc(n.label)+'</div><div class="t-note">'+sub+' · '+n.degree+' связей</div>';}
  else tip.style.opacity='0';
});
cv.addEventListener('mouseup',e=>{
  isPan=false;
  if(!moved){const n=pick(e.offsetX,e.offsetY);if(n){selected=(selected===n)?null:n;if(selected)showInfo(selected);else hideInfo();}else{selected=null;hideInfo();}requestDraw();}
});
cv.addEventListener('mouseleave',()=>{isPan=false;hovered=null;tip.style.opacity='0';requestDraw();});
cv.addEventListener('dblclick',e=>{const n=pick(e.offsetX,e.offsetY);if(n){selected=n;showInfo(n);focusNode(n);requestDraw();}});
cv.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.12:0.89;const wx=e.offsetX,wy=e.offsetY,ns=Math.max(0.04,Math.min(4,tScale*f));tTx=wx-(wx-tTx)*(ns/tScale);tTy=wy-(wy-tTy)*(ns/tScale);tScale=ns;requestDraw();},{passive:false});

function zoomAt(f){const ns=Math.max(0.04,Math.min(4,tScale*f));tTx=W/2-(W/2-tTx)*(ns/tScale);tTy=H/2-(H/2-tTy)*(ns/tScale);tScale=ns;requestDraw();}
document.getElementById('zin').onclick=()=>zoomAt(1.3);
document.getElementById('zout').onclick=()=>zoomAt(0.77);
document.getElementById('cfit').onclick=()=>{selected=null;hideInfo();fit(true);requestDraw();};
const lb=document.getElementById('clabels');lb.onclick=()=>{labelsOn=!labelsOn;lb.classList.toggle('active',labelsOn);requestDraw();};
const tb=document.getElementById('ctheme');
function setTheme(name){theme=THEMES[name];document.body.classList.toggle('light',name==='light');tb.innerHTML=name==='light'?'&#9728;':'&#9790;';tb.classList.toggle('active',name==='light');requestDraw();}
tb.onclick=()=>setTheme(document.body.classList.contains('light')?'dark':'light');
const mb=document.getElementById('cmotion');mb.classList.toggle('active',motion);mb.onclick=()=>{motion=!motion;mb.classList.toggle('active',motion);requestDraw();};
const sb=document.getElementById('sidebar'),pb=document.getElementById('cpanel');
function togglePanel(){const hid=sb.classList.toggle('hidden');pb.innerHTML=hid?'&#8677;':'&#8676;';pb.classList.toggle('active',hid);requestDraw();}
pb.onclick=togglePanel;
window.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&reader.classList.contains('open')){closeNote();return;}
  if(e.key==='Tab'){e.preventDefault();togglePanel();return;}
  if(e.target.tagName==='INPUT')return;
  if(e.key==='Escape'){selected=null;hideInfo();}
  else if(e.key==='f'||e.key==='F'){fit(true);}
  else if(e.key==='l'||e.key==='L'){labelsOn=!labelsOn;lb.classList.toggle('active',labelsOn);}
  else if(e.key==='t'||e.key==='T'){setTheme(document.body.classList.contains('light')?'dark':'light');}
  else if(e.key===' '){e.preventDefault();motion=!motion;mb.classList.toggle('active',motion);}
  requestDraw();
});
</script></body></html>"""
    # Single-pass substitution: replace all placeholders in one scan so that
    # data inserted earlier (which may itself contain a literal "__EDGES__" etc.,
    # e.g. notes documenting this very template) is never re-scanned and corrupted.
    subs = {
        "__NODES__": nodes_json,
        "__EDGES__": edges_json,
        "__SEC_COLORS__": sec_colors,
        "__SEC_NAMES__": sec_names,
        "__META__": meta_json,
        "__NOTES__": notes_json,
    }
    return re.sub("|".join(re.escape(k) for k in subs), lambda m: subs[m.group(0)], tpl)


def cmd_stats(args):
    conn = get_conn()
    total_chunks = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    total_files = conn.execute("SELECT COUNT(DISTINCT path) FROM notes").fetchone()[0]
    last_updated = conn.execute("SELECT MAX(updated_at) FROM notes").fetchone()[0]
    conn.close()
    graph_exists = (GRAPH_DIR / "graph.json").exists()
    print(json.dumps({
        "indexed_files": total_files,
        "total_chunks": total_chunks,
        "last_updated": last_updated,
        "graph_built": graph_exists,
        "db_path": str(DB_PATH),
        "vault": str(VAULT),
        "model": MODEL_NAME
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    rest = sys.argv[2:]

    commands = {
        "index": cmd_index,
        "search": cmd_search,
        "hybrid": cmd_hybrid,
        "brainstorm": cmd_brainstorm,
        "build_graph": cmd_build_graph,
        "stats": cmd_stats,
    }

    if cmd in commands:
        commands[cmd](rest)
    else:
        print(json.dumps({"error": f"unknown command: {cmd}. Available: {list(commands)}"}))
        sys.exit(1)
