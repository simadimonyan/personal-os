#!/usr/bin/env python3
"""
Context Store — визуальные референсы Personal OS.

Хранилище «бордов» (как доски Pinterest) с двумя полярностями:
  • Нравится   (like)  — хочу видеть так → do's
  • Не нравится (avoid) — НЕ хочу так, не делай как здесь → don'ts

Данные живут в Obsidian:
  08 — Шаблоны и ресурсы/Визуальные референсы/{slug}/
      board.json          — метаданные борда + карта источников картинок
      board.md            — заметка Obsidian (галерея + раздел «Визуальное ДНК»)
      Нравится/*.jpg
      Не нравится/*.jpg

Код — в репозитории (tools/context-store), данные — в vault. Зависимостей нет
(только stdlib), чтобы работать под супервизором pos без окружения.

CLI:
  store.py manifest
  store.py board-create <slug> --name "..." --category "..." [--desc "..."] [--tags a,b]
  store.py add-url <slug> <like|avoid> <url> [<url> ...]
  store.py add-file <slug> <like|avoid> <path> [<path> ...]
  store.py add-b64  <slug> <like|avoid> <name> <base64>        # для загрузки из UI
  store.py ingest-pinterest <slug> <like|avoid> <board_or_pin_url> [--limit N]
  store.py list
  store.py get <query>
  store.py delete-image <slug> <like|avoid> <file>
  store.py delete-board <slug>
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import mimetypes
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path


def _ssl_ctx(verify: bool) -> ssl.SSLContext:
    """Проверенный контекст: truststore (keychain) → certifi → системный.
    Под VPN certifi-only рвётся (MITM-серт не в бандле) — потому keychain первым.
    verify=False — запасной путь для скачивания публичных картинок."""
    if not verify:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

# ── корень хранилища (в Obsidian) ─────────────────────────────────────────────
import os

DEFAULT_VAULT = ("/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/"
                 "Knowledge base/Obsidian/Органон")
ROOT = Path(os.environ.get(
    "CONTEXT_STORE_ROOT",
    f"{DEFAULT_VAULT}/08 — Шаблоны и ресурсы/Визуальные референсы"))
MANIFEST = ROOT / "_store.json"

POLARITY = {"like": "Нравится", "avoid": "Не нравится"}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ── утилиты ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U).strip().lower()
    s = re.sub(r"[\s_]+", "-", s, flags=re.U)
    return re.sub(r"-{2,}", "-", s) or "board"


def board_dir(slug: str) -> Path:
    return ROOT / slug


def load_board(slug: str) -> dict | None:
    f = board_dir(slug) / "board.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_board(b: dict) -> None:
    d = board_dir(b["slug"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "board.json").write_text(json.dumps(b, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    _write_note(b)


def _write_note(b: dict) -> None:
    """board.md — читаемая заметка Obsidian с галереей и разделом ДНК."""
    d = board_dir(b["slug"])
    note = d / "board.md"
    dna = ""
    if note.exists():  # сохраняем уже написанное визуальное ДНК
        m = re.search(r"(## Визуальное ДНК.*)$", note.read_text(encoding="utf-8"),
                      re.S)
        if m:
            dna = m.group(1).rstrip() + "\n"
    if not dna:
        dna = ("## Визуальное ДНК\n\n"
               "> Заполняется дизайн-агентом или Claude по кнопке в mission-control.\n"
               "> Из «Нравится» → палитра, типографика, лейаут, настроение (do's).\n"
               "> Из «Не нравится» → чего избегать (don'ts).\n")

    def gallery(pol: str) -> str:
        imgs = b.get("images", {}).get(pol, [])
        if not imgs:
            return "_пусто_\n"
        return "\n".join(f"![[{it['file']}]]" for it in imgs) + "\n"

    tags = " ".join(f"#{t}" for t in b.get("tags", []))
    body = (
        f"---\ntype: visual-reference\ncategory: {b.get('category','')}\n"
        f"slug: {b['slug']}\n---\n\n"
        f"# {b.get('name', b['slug'])}\n\n"
        f"**Категория:** {b.get('category','—')}  ·  {tags}\n\n"
        f"{b.get('description','')}\n\n"
        f"## ✅ Нравится (хочу так)\n\n{gallery('like')}\n"
        f"## ⛔ Не нравится (НЕ делать как здесь)\n\n{gallery('avoid')}\n"
        f"{dna}")
    note.write_text(body, encoding="utf-8")


def _ext_from(url: str, ctype: str | None) -> str:
    if ctype:
        e = mimetypes.guess_extension(ctype.split(";")[0].strip())
        if e in (".jpe", ".jpeg"):
            return ".jpg"
        if e:
            return e
    m = re.search(r"\.(jpg|jpeg|png|webp|gif|avif)\b", url, re.I)
    return "." + (m.group(1).lower().replace("jpeg", "jpg")) if m else ".jpg"


def _looks_image(data: bytes, ctype: str | None) -> bool:
    """Настоящая картинка? Ловим HTML-страницы ошибок при скачивании по URL."""
    if not data or len(data) < 24:
        return False
    sig = data[:12]
    if (sig[:3] == b"\xff\xd8\xff" or sig[:8] == b"\x89PNG\r\n\x1a\n"
            or sig[:4] in (b"GIF8",) or sig[:2] == b"BM"
            or (sig[:4] == b"RIFF" and sig[8:12] == b"WEBP")
            or sig[4:8] == b"ftyp"):  # heic/avif
        return True
    return bool(ctype and ctype.split(";")[0].strip().startswith("image/"))


def _store_bytes(slug: str, pol: str, data: bytes, url: str, ctype: str | None,
                 hint: str | None = None) -> dict | None:
    if not _looks_image(data, ctype):  # мусор / HTML-ошибка / пустое
        return None
    d = board_dir(slug) / POLARITY[pol]
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(data).hexdigest()[:10]
    ext = _ext_from(hint or url, ctype)
    fname = f"img_{h}{ext}"
    fp = d / fname
    rel = f"{POLARITY[pol]}/{fname}"
    if not fp.exists():
        fp.write_bytes(data)
    b = load_board(slug) or _new_board(slug, slug, "")
    lst = b.setdefault("images", {}).setdefault(pol, [])
    if not any(it["file"] == rel for it in lst):
        lst.append({"file": rel, "source": url, "added": _now()})
    save_board(b)
    return {"file": rel, "source": url}


def _fetch(url: str, timeout: int = 25) -> tuple[bytes, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    for verify in (True, False):  # проверенный путь, затем запасной для картинок
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=_ssl_ctx(verify)) as r:
                return r.read(), r.headers.get("Content-Type")
        except (ssl.SSLError, urllib.error.URLError) as e:
            reason = getattr(e, "reason", e)
            if verify and isinstance(reason, ssl.SSLCertVerificationError):
                continue  # ретрай без верификации
            raise
    raise RuntimeError("unreachable")


# ── команды ───────────────────────────────────────────────────────────────────
def _new_board(slug: str, name: str, category: str, desc: str = "",
               tags: list[str] | None = None) -> dict:
    return {"slug": slug, "name": name or slug, "category": category or "Разное",
            "description": desc, "tags": tags or [], "created": _now(),
            "images": {"like": [], "avoid": []}}


def cmd_board_create(slug, name, category, desc, tags) -> dict:
    slug = slugify(slug)
    b = load_board(slug)
    if b:
        b["name"] = name or b["name"]
        b["category"] = category or b["category"]
        if desc:
            b["description"] = desc
        if tags:
            b["tags"] = tags
    else:
        b = _new_board(slug, name, category, desc, tags)
    save_board(b)
    rebuild_manifest()
    return {"ok": True, "slug": slug, "board": b}


def cmd_add_url(slug, pol, urls) -> dict:
    slug = slugify(slug)
    added, failed = [], []
    for u in urls:
        try:
            data, ct = _fetch(u)
            r = _store_bytes(slug, pol, data, u, ct)
            (added if r else failed).append(u)
        except Exception as e:
            failed.append(f"{u} :: {e}")
    rebuild_manifest()
    return {"ok": bool(added), "added": len(added), "failed": failed}


def cmd_add_file(slug, pol, paths) -> dict:
    slug = slugify(slug)
    added, failed = [], []
    for p in paths:
        try:
            data = Path(p).read_bytes()
            ct = mimetypes.guess_type(p)[0]
            r = _store_bytes(slug, pol, data, f"file://{p}", ct, hint=p)
            (added if r else failed).append(p)
        except Exception as e:
            failed.append(f"{p} :: {e}")
    rebuild_manifest()
    return {"ok": bool(added), "added": len(added), "failed": failed}


def cmd_add_b64(slug, pol, name, b64) -> dict:
    slug = slugify(slug)
    try:
        raw = b64.split(",", 1)[1] if b64.startswith("data:") else b64
        data = base64.b64decode(raw)
        ct = mimetypes.guess_type(name)[0]
        r = _store_bytes(slug, pol, data, f"upload://{name}", ct, hint=name)
        rebuild_manifest()
        return {"ok": bool(r), "file": r["file"] if r else None}
    except Exception as e:
        return {"ok": False, "out": str(e)}


def _pinterest_image_urls(page_url: str, limit: int) -> list[str]:
    """Best-effort извлечение оригиналов пинов из HTML публичного борда/пина.

    Pinterest рендерит через JS, но в HTML остаются ссылки i.pinimg.com. Тянем
    их, апгрейдим до /originals/, дедуплим. Хрупко — при нуле проси загрузку/URL.
    """
    html, _ = _fetch(page_url, timeout=30)
    text = html.decode("utf-8", "ignore")
    raw = re.findall(r"https://i\.pinimg\.com/[^\"'\\\s]+?\.(?:jpg|jpeg|png|webp)",
                     text, re.I)
    seen, out = set(), []
    for u in raw:
        # /236x/ /474x/ /564x/ /originals/ … → апгрейд до originals
        up = re.sub(r"/(\d+x\d*|\d+x)/", "/originals/", u)
        key = re.sub(r"^https://i\.pinimg\.com/(?:originals/)?", "", up)
        if key in seen:
            continue
        seen.add(key)
        out.append(up)
        if len(out) >= limit:
            break
    return out


def cmd_ingest_pinterest(slug, pol, url, limit) -> dict:
    slug = slugify(slug)
    try:
        urls = _pinterest_image_urls(url, limit)
    except Exception as e:
        return {"ok": False, "out": f"не смог открыть страницу: {e}"}
    if not urls:
        return {"ok": False, "out": "картинок не найдено (борд приватный/JS-only). "
                "Вставь прямые URL картинок или загрузи файлы."}
    added, failed = 0, []
    for u in urls:
        try:
            data, ct = _fetch(u)
            if _store_bytes(slug, pol, data, u, ct):
                added += 1
        except Exception as e:
            failed.append(str(e)[:80])
    rebuild_manifest()
    return {"ok": added > 0, "found": len(urls), "added": added, "failed": failed[:5]}


def cmd_delete_image(slug, pol, file) -> dict:
    slug = slugify(slug)
    b = load_board(slug)
    if not b:
        return {"ok": False, "out": "борд не найден"}
    lst = b.get("images", {}).get(pol, [])
    b["images"][pol] = [it for it in lst if it["file"] != file]
    fp = board_dir(slug) / file
    try:
        fp.unlink(missing_ok=True)
    except Exception:
        pass
    save_board(b)
    rebuild_manifest()
    return {"ok": True}


def cmd_delete_board(slug) -> dict:
    import shutil
    slug = slugify(slug)
    d = board_dir(slug)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    rebuild_manifest()
    return {"ok": True}


def _has_dna(slug: str) -> bool:
    note = board_dir(slug) / "board.md"
    if not note.exists():
        return False
    m = re.search(r"## Визуальное ДНК(.*)$", note.read_text(encoding="utf-8"), re.S)
    return bool(m) and "Заполняется дизайн-агентом" not in (m.group(1) or "")


def rebuild_manifest() -> dict:
    boards, cats = [], set()
    for bj in sorted(ROOT.glob("*/board.json")):
        try:
            b = json.loads(bj.read_text(encoding="utf-8"))
        except Exception:
            continue
        imgs = b.get("images", {})
        like, avoid = imgs.get("like", []), imgs.get("avoid", [])
        cover = (like[0]["file"] if like else (avoid[0]["file"] if avoid else None))
        cats.add(b.get("category", "Разное"))
        boards.append({
            "slug": b["slug"], "name": b.get("name", b["slug"]),
            "category": b.get("category", "Разное"),
            "description": b.get("description", ""), "tags": b.get("tags", []),
            "counts": {"like": len(like), "avoid": len(avoid)},
            "cover": f"{b['slug']}/{cover}" if cover else None,
            "has_dna": _has_dna(b["slug"]),
            "images": {"like": [{**it, "path": f"{b['slug']}/{it['file']}"} for it in like],
                       "avoid": [{**it, "path": f"{b['slug']}/{it['file']}"} for it in avoid]},
        })
    m = {"updated": _now(), "categories": sorted(cats),
         "total_boards": len(boards),
         "total_images": sum(x["counts"]["like"] + x["counts"]["avoid"] for x in boards),
         "boards": boards}
    ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def cmd_get(query: str) -> dict:
    q = query.lower()
    m = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() \
        else rebuild_manifest()
    hits = [b for b in m["boards"]
            if q in b["name"].lower() or q in b["slug"].lower()
            or q in b["category"].lower()
            or any(q in t.lower() for t in b["tags"])]
    return {"query": query, "matches": hits}


# ── CLI ───────────────────────────────────────────────────────────────────────
def _flag(args, name, default=None):
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def main():
    # под супервизором stdout может быть ascii → кириллица в JSON падает; форсим utf-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    cmd, rest = a[0], a[1:]
    if cmd == "manifest":
        out = rebuild_manifest()
    elif cmd == "board-create":
        tags = _flag(rest, "--tags")
        out = cmd_board_create(rest[0], _flag(rest, "--name"), _flag(rest, "--category"),
                               _flag(rest, "--desc", ""),
                               [t.strip() for t in tags.split(",")] if tags else [])
    elif cmd == "add-url":
        out = cmd_add_url(rest[0], rest[1], rest[2:])
    elif cmd == "add-file":
        out = cmd_add_file(rest[0], rest[1], rest[2:])
    elif cmd == "add-b64":
        out = cmd_add_b64(rest[0], rest[1], rest[2], rest[3])
    elif cmd == "ingest-pinterest":
        out = cmd_ingest_pinterest(rest[0], rest[1], rest[2],
                                   int(_flag(rest, "--limit", "30")))
    elif cmd == "delete-image":
        out = cmd_delete_image(rest[0], rest[1], rest[2])
    elif cmd == "delete-board":
        out = cmd_delete_board(rest[0])
    elif cmd == "list":
        out = rebuild_manifest()
    elif cmd == "get":
        out = cmd_get(rest[0] if rest else "")
    else:
        out = {"ok": False, "out": f"неизвестная команда: {cmd}"}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
