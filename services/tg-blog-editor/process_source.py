#!/usr/bin/env python3
"""
process_source — извлечь контент из источника и сгенерировать черновик поста.

Запускается ФРЕЙМВОРОЧНЫМ питоном (там yt-dlp, bs4, claude-local-api), а не
venv-питоном бота. Вход — JSON в stdin, выход — JSON в stdout.

Вход:  {"kind": "youtube|web|telegram|text", "url": "...", "text": "...", "model": "sonnet"}
Выход: {"text": "<html-пост>", "url": "<ссылка>", "kind": "...", "chars": N}
Ошибка: ненулевой код возврата, текст в stderr.

Типы источников:
  youtube  — субтитры через yt-dlp (ru→en, ручные→авто), парсинг VTT
  web      — статья: urllib + BeautifulSoup, текст из <article>/<p>
  telegram — текст пересланного поста (передаётся в "text")
  text     — произвольный текст
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

# claude-local-api клиент и форматтер поста живут в radar/
RADAR_DIR = Path(__file__).resolve().parent.parent / "radar"
sys.path.insert(0, str(RADAR_DIR))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
YT_RE = re.compile(r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)", re.I)


# ── Извлечение: YouTube ───────────────────────────────────────────────────────
def _parse_vtt(text: str) -> str:
    lines, prev = [], None
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "WEBVTT" or "-->" in s or s.isdigit():
            continue
        if s.startswith(("Kind:", "Language:", "NOTE")):
            continue
        s = re.sub(r"<[^>]+>", "", s)  # инлайн-теги тайминга
        s = re.sub(r"&nbsp;?", " ", s).strip()
        if s and s != prev:
            lines.append(s)
            prev = s
    return " ".join(lines)


def extract_youtube(url: str) -> str:
    import yt_dlp

    with tempfile.TemporaryDirectory() as td:
        opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["ru", "en", "ru-orig", "en-orig"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(td, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        title = ""
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "")
        vtts = sorted(glob.glob(os.path.join(td, "*.vtt")))
        # приоритет: ru ручные → en ручные → любые авто
        vtts.sort(key=lambda p: (".ru." not in p, "auto" in p.lower()))
        if not vtts:
            raise RuntimeError("у видео нет субтитров (ни ручных, ни авто)")
        transcript = _parse_vtt(Path(vtts[0]).read_text(encoding="utf-8", errors="ignore"))
    head = f"Заголовок видео: {title}\n\n" if title else ""
    return head + transcript


# ── Извлечение: веб-статья ────────────────────────────────────────────────────
def extract_web(url: str) -> str:
    from bs4 import BeautifulSoup

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    title = (soup.title.get_text(strip=True) if soup.title else "")
    root = soup.find("article") or soup.find("main") or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    body = "\n".join(p for p in paras if len(p) > 30)
    if len(body) < 200:  # fallback: весь видимый текст
        body = soup.get_text("\n", strip=True)
    head = f"Заголовок: {title}\n\n" if title else ""
    return head + body


# ── Главный конвейер ──────────────────────────────────────────────────────────
def detect_kind(url: str, text: str) -> str:
    if url and YT_RE.search(url):
        return "youtube"
    if url and url.startswith("http"):
        return "web"
    return "text"


def main() -> None:
    spec = json.loads(sys.stdin.read() or "{}")
    url = (spec.get("url") or "").strip()
    text = (spec.get("text") or "").strip()
    kind = spec.get("kind") or detect_kind(url, text)
    model = spec.get("model") or "sonnet"

    if kind == "youtube":
        content = extract_youtube(url)
    elif kind == "web":
        content = extract_web(url)
    else:  # telegram / text
        content = text
        kind = "telegram" if spec.get("kind") == "telegram" else "text"

    import channel_digest

    post = channel_digest.build_from_content(
        content, source_url=url, kind=kind, model=model)
    print(json.dumps({**post, "kind": kind, "chars": len(content)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(str(e), file=sys.stderr)
        sys.exit(1)
