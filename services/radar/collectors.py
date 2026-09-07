"""
Сбор сырых материалов для агрегатора инфополя.

Два источника:
  - RSS/Atom ленты (веб-новости) — через urllib + xml.etree, stdlib-only.
  - Telegram-каналы — через ~/.claude/skills/telegram/driver.cjs (get_chat_history).

Каждый коллектор возвращает список item-ов:
  {"source": "...", "kind": "rss|telegram", "title": "...", "text": "...",
   "url": "...", "ts": <epoch|None>, "uid": "<стабильный id для дедупа>"}
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"
UA = "Mozilla/5.0 (Macintosh) personal-os-radar/1.0"

# теги-«мусор» (не текст статьи) и теги-носители основного текста
_SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer", "aside",
              "header", "form", "svg", "button", "figure"}
_CONTENT_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "article", "blockquote", "figcaption"}
_BLOCK_TAGS = _CONTENT_TAGS | {"section", "div", "tr", "br"}


class _Extract(HTMLParser):
    """Достаёт читаемый текст: сначала из контентных тегов, иначе — весь видимый."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []      # текст из <p>/<h*>/<li>/<article>…
        self.allparts: list[str] = []   # весь видимый текст (фолбэк)
        self._skip = 0
        self._content = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _CONTENT_TAGS:
            self._content += 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            self.allparts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in _CONTENT_TAGS and self._content:
            self._content -= 1

    def handle_data(self, data):
        if self._skip:
            return
        t = data.strip()
        if not t:
            return
        self.allparts.append(t + " ")
        if self._content:
            self.parts.append(t + " ")


def _clean(txt: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", txt)).strip()


def html_to_text(s: str, limit: int = 4000) -> str:
    """HTML → читаемый текст (stdlib). Для plain-текста вернёт его же, очищенным."""
    if not s:
        return ""
    p = _Extract()
    try:
        p.feed(s)
    except Exception:  # noqa: BLE001 — на битом HTML просто грубо сносим теги
        return _clean(re.sub(r"<[^>]+>", " ", s))[:limit]
    body = _clean("".join(p.parts))
    if len(body) < 200:  # контентных тегов мало — берём весь видимый текст
        body = _clean("".join(p.allparts))
    return body[:limit]


def fetch_article(url: str, limit: int = 4000) -> str:
    """Перейти по ссылке материала и вытащить полный текст страницы (best-effort)."""
    if not re.match(r"^https?://", url or ""):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if ctype and "html" not in ctype and "text" not in ctype:
                return ""  # не страница (pdf/картинка/видео) — пропускаем
            raw = r.read(1_500_000)  # cap ~1.5 МБ, чтобы не утянуть гигантскую страницу
    except Exception:  # noqa: BLE001 — сеть/таймаут/403: молча откатываемся к summary
        return ""
    charset = "utf-8"
    m = re.search(r"charset=([\w\-]+)", ctype)
    if m:
        charset = m.group(1)
    try:
        s = raw.decode(charset, errors="replace")
    except LookupError:
        s = raw.decode("utf-8", errors="replace")
    return html_to_text(s, limit)


# ── RSS / Atom ────────────────────────────────────────────────────────────────
def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(s: str) -> float | None:
    if not s:
        return None
    try:  # RSS: "Wed, 11 Jun 2026 10:00:00 GMT"
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        pass
    try:  # Atom: ISO 8601
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch_rss(name: str, url: str, max_items: int = 15, full: bool = False,
              skip_uids: set | None = None) -> list[dict]:
    """Собрать материалы из RSS/Atom.

    full=True → для НОВОГО материала переходим по его ссылке (<link>) и парсим
    ПОЛНЫЙ текст страницы; если не вышло (таймаут/не-HTML/блок) — откатываемся к
    summary из ленты. HTML в summary всегда очищается до читаемого текста.
    skip_uids → уже сохранённые материалы: по их ссылкам повторно не ходим
    (экономия сети/времени, чтобы ingest не упирался в таймаут).
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = urllib.request.urlopen(req, timeout=20).read()
    root = ET.fromstring(data)

    items: list[dict] = []
    # RSS 2.0: channel/item ; Atom: feed/entry
    nodes = root.findall(".//item") or root.findall(".//{*}entry")
    for node in nodes[:max_items]:
        title = link = summary = pub = ""
        for child in node:
            tag = _strip_ns(child.tag)
            if tag == "title":
                title = _text(child)
            elif tag == "link":
                link = _text(child) or child.attrib.get("href", "")
            elif tag in ("description", "summary", "content"):
                summary = summary or _text(child)
            elif tag in ("pubDate", "published", "updated"):
                pub = pub or _text(child)
        uid = link or f"{name}:{title}"
        summary_txt = html_to_text(summary, 600)
        text = summary_txt
        if full and re.match(r"^https?://", link) and (skip_uids is None or uid not in skip_uids):
            article = fetch_article(link, 4000)
            if len(article) > len(summary_txt):  # полнее, чем summary → берём её
                text = article
        items.append({
            "source": name,
            "kind": "rss",
            "title": title,
            "text": (text or title)[:4000],
            "url": link,
            "ts": _parse_date(pub),
            "uid": uid,
        })
    return items


# ── Telegram ──────────────────────────────────────────────────────────────────
def _driver(tool: str, args: dict) -> object:
    proc = subprocess.run(
        ["node", str(TELEGRAM_DRIVER), tool, json.dumps(args, ensure_ascii=False)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # gramjs пишет логи в stdout перед JSON; pretty-printed JSON открывается
    # строкой из одной «[» или «{» — от неё и парсим.
    lines = proc.stdout.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() in ("[", "{"):
            # после JSON могут идти лог-строки — берём только первый JSON-объект
            obj, _ = json.JSONDecoder().raw_decode("\n".join(lines[i:]))
            return obj
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError(f"telegram driver: не JSON: {(proc.stdout + proc.stderr)[:200]}")


def fetch_telegram(name: str, chat: str, max_items: int = 15) -> list[dict]:
    msgs = _driver("get_chat_history", {"chat_id": chat, "limit": max_items})
    items: list[dict] = []
    for m in msgs:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        ts = None
        if m.get("date"):
            try:
                ts = dt.datetime.fromisoformat(m["date"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        # Прямая ссылка на пост: https://t.me/<username>/<msg_id> для каналов с @username.
        # Для числовых chat_id публичной ссылки нет — оставляем пустую.
        msg_id = m.get("id")
        if chat.startswith("@") and msg_id:
            url = f"https://t.me/{chat[1:]}/{msg_id}"
        elif str(chat).startswith("https://t.me/"):
            url = chat
        else:
            url = ""
        items.append({
            "source": name,
            "kind": "telegram",
            "title": text.split("\n", 1)[0][:120],
            "text": text[:500],
            "url": url,
            "ts": ts,
            "uid": f"{chat}:{msg_id}",
        })
    return items


def discover_channels(limit: int = 60) -> list[dict]:
    """Список каналов, на которые подписан аккаунт — чтобы выбрать для sources.json."""
    dialogs = _driver("get_dialogs", {"limit": limit})
    out = []
    for d in dialogs:
        if d.get("isChannel"):
            out.append({
                "name": d.get("name"),
                "chat": ("@" + d["username"]) if d.get("username") else d.get("id"),
            })
    return out
