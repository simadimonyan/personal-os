#!/usr/bin/env python3
"""
Пикабу — инструмент разведки, участия и публикаций.

Не «рассыльщик». Пикабу устроен так, что механическая отправка там даёт
обратный результат: пост уходит успешно, а дальше его либо минусуют в ноль
за час, либо снимает модератор сообщества из очереди — и в обоих случаях
автор видит свой пост живым. Поэтому здесь три слоя, и публикация — третий:

  1. Разведка   — какие сообщества и теги есть по теме, живые ли они,
                  что написано в правилах, чем там кончаются посты про своё.
  2. Участие    — поиск живых постов по теме и ответы в них. Основной канал:
                  развёрнутый комментарий не снимают и не репортят.
  3. Публикация — пост из файла-спеки, с преполётной проверкой и сверкой
                  через час, что он жив и не ушёл в минус.

Чем Пикабу отличается от Reddit (и почему тут другие проверки):
  • Ссылка в тексте. Редактор Пикабу отклоняет пост со ссылками, если не
    поднят рекламный флаг («Нельзя добавить пост со ссылками вне блога»),
    а рекламный флаг требует указать юрлицо. Инструмент ловит это до отправки.
  • Рейтинг вместо кармы. Минусы прилетают быстрее, чем баны: пост с −20
    формально жив, но мёртв. verify считает это отдельным исходом.
  • Модерация сообщества. Пост в сообщество может попасть в очередь и не
    выйти вовсе — verify проверяет и это.
  • Медленный режим. Свежим аккаунтам режут число комментариев в сутки;
    команда me показывает, включён ли он.

Гарды (config.json → guards):
  • не чаще N часов между постами и не больше N постов в сутки;
  • в одно сообщество — не чаще раза в N дней;
  • соотношение «комментарии : свои посты» (на Пикабу самопиар без участия
    минусуют системно);
  • минимальный рейтинг аккаунта.

Схема (pikabu.db):
  communities — рабочий список площадок: правила, вердикт проверки, заметки
  posts       — свои посты: что, куда, когда, жив ли, рейтинг, комментарии
  comments    — свои комментарии (знаменатель в соотношении участия)
  kv          — служебное

Вход — личный аккаунт, ничего регистрировать не надо:
  python3 pikabu.py login    — откроется системный Chrome, входишь руками,
                               сессия ложится в ~/.pikabu-session.
Чтение (find, comm, feed, search, story) работает и без входа, без браузера
вообще: обычные запросы к сайту. Браузер поднимается только под действия.

Команды:
  login                                  — войти руками в открывшемся Chrome
  me                                     — аккаунт: рейтинг, посты, ограничения
  find <запрос>                          — сообщества, теги и авторы по теме
  comm info <ссылка|id>                  — о сообществе: люди, посты, описание
  comm rules <ссылка>                    — правила сообщества текстом
  comm check <ссылка>                    — ПРЕПОЛЁТ: можно ли мне туда постить
  comm add <ссылка> [--note ...]         — в рабочий список
  comm list [--checked]                  — рабочий список с вердиктами
  feed [hot|new|best] [--community X] [--tag T] [--pages N]
                                         — лента: что заходит в нише
  search <запрос> [--pages N] [--days N] [--min-comments N]
                                         — посты по словам (кому отвечать)
  story <id|url> [--comments N]          — пост целиком и верхние комментарии
  comment <id|url> --file f.md | --text "…" [--parent ID] [--dry]
                                         — ответить в пост
  post <файл-спеки> [--dry] [--force]    — опубликовать пост из спеки
  verify [--id ID] [--all]               — жив ли пост, рейтинг, минусы
  stats                                  — свои посты/комментарии, соотношение
  plan                                   — что гарды разрешают сегодня

Формат спеки поста (JSON):
  {
    "community": "translation",       // ссылка или id, необязательно
    "title": "…",
    "text": "…",                      // либо "text_file": "post.md"
    "tags": ["Английский язык", "Изучаем английский"],
    "authors": true,                  // тег [моё]
    "adult": false,
    "comments_disabled": false,
    "advert": false,                  // рекламный пост: нужен advert_company
    "advert_company": ""              // юрлицо рекламодателя
  }

Только stdlib. Сеть на чтение — urllib, действия — driver.mjs, база — sqlite3.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "pikabu.db"
CONFIG_PATH = HERE / "config.json"
DRIVER = HERE / "driver.mjs"

BASE = "https://pikabu.ru"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Слова, по которым в правилах сообщества узнаётся запрет рекламы и ссылок.
PROMO_MARKERS = [
    "реклам", "самопиар", "пиар", "спам", "продвижен", "коммерч",
    "продаж", "продать", "заработок", "партнёрск", "партнерск",
    "реферал", "промокод", "опрос", "набор в", "приглаша",
]
LINK_MARKERS = ["ссылк", "ссылок", "линк", "переход на сайт"]

DEFAULT_GUARDS = {
    "min_hours_between_posts": 12,
    "max_posts_per_day": 2,
    "days_between_same_community": 30,
    "comment_to_post_ratio": 5,     # цель: столько комментариев на один свой пост
    "min_account_rating": 50,       # ниже — пост тонет и минусуется на автомате
    "min_account_age_days": 14,
}

# Лимиты редактора Пикабу (подтверждены разбором story-editor).
MAX_TITLE = 110
MAX_TEXT = 20000
MIN_TAGS = 1
MAX_TAGS = 15


# ─────────────────────────────── конфиг и база ───────────────────────────────

def load_config() -> dict:
    """Конфиг нужен только для гардов — доступы живут в сессии браузера."""
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    guards = dict(DEFAULT_GUARDS)
    guards.update(cfg.get("guards") or {})
    cfg["guards"] = guards
    return cfg


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS communities (
            link         TEXT PRIMARY KEY,   -- часть адреса: /community/<link>
            id           INTEGER,
            name         TEXT,
            subs         INTEGER,
            stories      INTEGER,
            description  TEXT,
            rules        TEXT,
            promo_rule   TEXT,               -- цитата правила про рекламу
            verdict      TEXT,               -- ok | careful | no
            verdict_note TEXT,
            note         TEXT,
            checked_at   REAL,
            added_at     REAL
        );
        CREATE TABLE IF NOT EXISTS posts (
            id           INTEGER PRIMARY KEY,  -- story_id
            community    TEXT,
            title        TEXT,
            url          TEXT,
            tags         TEXT,
            created_at   REAL,
            alive        INTEGER,              -- NULL не проверяли, 1 жив, 0 снят
            status       TEXT,                 -- alive | removed | in_queue | downvoted
            rating       INTEGER,
            comments     INTEGER,
            checked_at   REAL,
            spec         TEXT
        );
        CREATE TABLE IF NOT EXISTS comments (
            id           INTEGER PRIMARY KEY,  -- comment_id
            story_id     INTEGER,
            parent_id    INTEGER,
            url          TEXT,
            body         TEXT,
            created_at   REAL
        );
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()
    return conn


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value):
    conn.execute(
        "INSERT INTO kv(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


# ──────────────────────────────── разбор HTML ────────────────────────────────

def strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r" +([:;,.!?])", r"\1", s)   # «Запрещается :» после срезанных тегов
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_number(s: str) -> int | None:
    """«13&emsp14;596&emsp14;568», «17К», «2453» → число."""
    if not s:
        return None
    s = htmllib.unescape(s).replace(" ", "").replace(" ", "")
    s = re.sub(r"[\s ]", "", s)
    m = re.match(r"^(-?[\d.,]+)(КК|К|kk|k)?$", s, re.I)
    if not m:
        digits = re.sub(r"[^\d-]", "", s)
        return int(digits) if digits not in ("", "-") else None
    num = float(m.group(1).replace(",", "."))
    mult = {"к": 1_000, "k": 1_000, "кк": 1_000_000, "kk": 1_000_000}
    suffix = (m.group(2) or "").lower()
    return int(num * mult.get(suffix, 1))


def parse_datetime(body: str) -> int | None:
    """<time datetime="2026-08-13T12:14:36+03:00"> → unix-время."""
    m = re.search(r'<time datetime="([^"]+)"', body)
    if not m:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(m.group(1)).timestamp())
    except ValueError:
        return None


def parse_stories(html: str) -> list[dict]:
    """Ленты, поиск, страницы тегов и сообществ — везде один и тот же <article>."""
    out = []
    for m in re.finditer(r'<article class="story"([^>]*)>', html):
        attrs = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', m.group(1)))
        sid = attrs.get("story-id")
        if not sid:
            continue
        # тело поста — до следующей статьи
        nxt = html.find('<article class="story"', m.end())
        body = html[m.end(): nxt if nxt > 0 else m.end() + 20000]
        # Заголовок берём только у ссылки, ведущей на этот же пост: между
        # статьями попадаются рекламные и закреплённые обёртки, и без сверки
        # id в ленту просачивается чужой заголовок.
        title_m = re.search(
            r'<a href="(https://pikabu\.ru/story/[^"]*_%s)"[^>]*class="story__title-link">(.*?)</a>'
            % sid, body, re.S)
        title, url = (title_m.group(2), title_m.group(1)) if title_m else (None, None)
        if title is None and attrs.get("page") == "true":
            # На собственной странице поста заголовок — не ссылка, а <span>.
            own = re.search(r'<span class="story__title-link">(.*?)</span>', body, re.S)
            # По короткому адресу /story/_ID страница приходит без canonical,
            # но og:url в ней есть всегда.
            canon = (re.search(r'<meta property="og:url" content="([^"]+)"', html)
                     or re.search(r'<link rel="canonical" href="([^"]+)"', html))
            if own:
                title = own.group(1)
                url = canon.group(1) if canon else f"{BASE}/story/_{sid}"
        if title is None:
            continue
        comm_m = re.search(
            r'class="story__community-link" data-id="(\d+)" href="/community/([^"]+)"', body)
        comm_name = re.search(r'class="story__community-name">([^<]*)<', body)
        tags = re.findall(r'class="tags__tag[^"]*"[^>]*data-tag="([^"]*)"', body)
        out.append({
            "id": int(sid),
            "title": strip_tags(title),
            "url": url,
            "author": attrs.get("author-name"),
            "rating": int(attrs.get("rating") or 0),
            "comments": int(attrs.get("comments") or 0),
            # data-timestamp на страницах сообществ — время отрисовки страницы,
            # одинаковое у всех постов; настоящая дата живёт в <time datetime>.
            "timestamp": parse_datetime(body) or int(attrs.get("timestamp") or 0),
            "community_id": int(comm_m.group(1)) if comm_m else None,
            "community": comm_m.group(2) if comm_m else None,
            "community_name": strip_tags(comm_name.group(1)) if comm_name else None,
            "tags": [t for t in tags if t and t != "Моё"],
            "text_length": int(attrs.get("text-length") or 0),
        })
    return out


def story_text(html: str) -> str:
    """Текст поста со страницы: от начала контента до тегов/подвала."""
    start = html.find('class="story__content-inner')
    if start < 0:
        return ""
    start = html.index(">", start) + 1
    end = re.search(r'<div class="story__(?:tags|footer|read-more|author-panel)',
                    html[start:])
    return strip_tags(html[start: start + end.start()] if end else html[start:start + 20000])


def parse_community_page(html: str) -> dict:
    """Правила и админы живут только в HTML сообщества, в JSON их нет."""
    rules = re.search(
        r'class="community-info-block__rules-content">(.*?)</div>', html, re.S)
    desc = re.search(
        r'class="community-info-block__descriptions">(.*?)</div>', html, re.S)
    admins = re.findall(r'data-name="([^"]+)"[^>]*>.*?class="caption">([^<]*)<', html, re.S)
    return {
        "rules": strip_tags(rules.group(1)) if rules else "",
        "description": strip_tags(desc.group(1)) if desc else "",
        "admins": [{"name": n, "role": strip_tags(r)} for n, r in admins[:5]],
    }


def parse_profile(html: str) -> dict:
    """Рейтинг, подписчики, посты, «в горячем» — из шапки профиля."""
    out = {}
    for m in re.finditer(
            r'class="profile__digital[^"]*"(?:\s+aria-label="([^"]*)")?\s*>\s*'
            r'<b>([^<]*)</b>\s*<span>([^<]*)', html):
        aria, big, label = m.group(1), m.group(2), m.group(3)
        value = parse_number(aria.split(" ")[0]) if aria else parse_number(big)
        key = label.strip().lower()
        if "рейтинг" in key:
            out["rating"] = value
        elif "подписчик" in key:
            out["followers"] = value
        elif "пост" in key:
            out["stories"] = value
        elif "горяч" in key:
            out["hot"] = value
    pl = re.search(r'class="profile__pluses"[^>]*>([^<]*)<', html)
    mi = re.search(r'class="profile__minuses"[^>]*>([^<]*)<', html)
    if pl:
        out["pluses"] = parse_number(pl.group(1))
    if mi:
        out["minuses"] = parse_number(mi.group(1))
    reg = re.search(r'(?:На Пикабу с|Регистрация)[^<]*<[^>]*>?\s*([\d.]{8,10})', html)
    if reg:
        out["registered"] = reg.group(1)
    return out


def parse_comments(items: list[dict]) -> list[dict]:
    """get_story_comments отдаёт готовые куски HTML, разбираем их в данные."""
    out = []
    for it in items or []:
        h = it.get("html") or ""
        meta = dict(re.findall(r"([a-z]+)=([^;\"]*)", re.search(
            r'data-meta="([^"]*)"', h).group(1))) if 'data-meta="' in h else {}
        author = re.search(r'class="user__nick">([^<]*)<', h)
        # Текст комментария — до следующего служебного блока (эмоции,
        # кнопки, ветка ответов): по закрывающим </div> резать нельзя,
        # они вложены и цитата уводит регулярку в панель управления.
        body = None
        start = h.find('class="comment__content"')
        if start >= 0:
            start = h.index(">", start) + 1
            end = re.search(r'<div class="comment__(?:emotions|controls|children|tools)',
                            h[start:])
            body = h[start: start + end.start()] if end else h[start:]
        out.append({
            "id": it.get("id"),
            "parent_id": it.get("parent_id"),
            "author": strip_tags(author.group(1)) if author else meta.get("aid"),
            "rating": parse_number(meta.get("r", "")),
            "depth": int(meta.get("de") or 0),
            "date": meta.get("d"),
            "text": strip_tags(body) if body else strip_tags(h)[:400],
        })
    return out


def md_to_blocks(text: str) -> list[dict]:
    """
    Текст поста → блоки редактора. Пикабу хранит абзацы как HTML внутри
    блока type=text; markdown никто не разбирает, поэтому переводим сами.
    """
    def inline(s: str) -> str:
        s = htmllib.escape(s, quote=False)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
        return s

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    html_parts = []
    for p in paragraphs:
        lines = [inline(l.strip()) for l in p.split("\n") if l.strip()]
        html_parts.append("<p>" + "<br>".join(lines) + "</p>")
    return [{"id": 1, "type": "text", "body": "".join(html_parts)}]


def find_links(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>()\"]+", text or "")


# ──────────────────────────────── клиент ────────────────────────────────

class Pikabu:
    """
    Чтение — обычными запросами к сайту (быстро, без браузера, без входа).
    Действия — через driver.mjs под живой сессией системного Chrome.
    """

    def __init__(self, cfg: dict, conn: sqlite3.Connection):
        self.cfg = cfg
        self.conn = conn

    # — чтение —

    def page(self, path: str, **params) -> str:
        """HTML страницы. Сайт отдаёт windows-1251 — декодируем по заголовку."""
        url = path if path.startswith("http") else BASE + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            die(f"{url}: HTTP {e.code}")
        except urllib.error.URLError as e:
            die(f"{url}: {e.reason}")
        charset = "cp1251"
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        if m and "1251" not in m.group(1):
            charset = m.group(1)
        return raw.decode(charset, "replace")

    def ajax(self, path: str, **data) -> dict:
        """Анонимный ajax сайта. Часть действий требует входа — тогда _error."""
        url = BASE + path
        body = urllib.parse.urlencode(
            {k: v for k, v in data.items() if v is not None}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        })
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            return {"_error": str(e)}
        if not payload.get("result"):
            return {"_error": "refused", "message": payload.get("message", "")}
        return payload.get("data") or {}

    # — действия через браузер —

    _server = None
    _req_id = 0

    def _serve(self, tool: str, args: dict):
        if Pikabu._server is None:
            Pikabu._server = subprocess.Popen(
                ["node", str(DRIVER), "serve"],
                cwd=str(HERE), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
        Pikabu._req_id += 1
        rid = Pikabu._req_id
        srv = Pikabu._server
        try:
            srv.stdin.write(json.dumps({"id": rid, "tool": tool, "args": args}) + "\n")
            srv.stdin.flush()
            line = srv.stdout.readline()
        except (BrokenPipeError, ValueError):
            Pikabu._server = None
            return None
        if not line:
            Pikabu._server = None
            return None
        try:
            return json.loads(line).get("result")
        except json.JSONDecodeError:
            return None

    @staticmethod
    def stop_server():
        if Pikabu._server is not None:
            try:
                Pikabu._server.stdin.write(json.dumps({"id": 0, "tool": "quit"}) + "\n")
                Pikabu._server.stdin.flush()
                Pikabu._server.wait(timeout=15)
            except Exception:
                Pikabu._server.kill()
            Pikabu._server = None

    def _drive(self, tool: str, payload: dict | None = None):
        if tool in ("post", "get", "whoami", "params"):
            res = self._serve(tool, payload or {})
            if res is not None:
                self._check_session(res)
                return res
        cmd = ["node", str(DRIVER), tool]
        if payload is not None:
            cmd.append(json.dumps(payload, ensure_ascii=False))
        proc = subprocess.run(cmd, cwd=str(HERE), capture_output=True,
                              text=True, timeout=420)
        out = (proc.stdout or "").strip()
        if not out:
            err = (proc.stderr or "").strip()[-400:]
            die(f"драйвер молчит ({tool}). stderr: {err}")
        try:
            res = json.loads(out)
        except json.JSONDecodeError:
            die(f"драйвер вернул не JSON: {out[:300]}")
        self._check_session(res)
        return res

    @staticmethod
    def _check_session(res):
        if isinstance(res, dict) and res.get("_error") == "not_logged_in":
            die("сессия Пикабу не найдена или просрочена. Войди один раз:\n"
                f"    python3 pikabu.py login")

    def act(self, path: str, **data):
        """POST-действие под сессией. Отказ сайта возвращается как есть."""
        return self._drive("post", {"path": path, "data": data})

    def whoami(self) -> dict:
        return self._drive("whoami")


# ──────────────────────────────── команды ────────────────────────────────

def cmd_login(pk: Pikabu, args):
    res = pk._drive("login")
    if res.get("ok"):
        print(f"✓ вошёл как {res.get('user') or res.get('user_id')}")
        print("  сессия сохранена в ~/.pikabu-session")
    else:
        die(res.get("error", "вход не удался"))


def cmd_me(pk: Pikabu, args):
    who = pk.whoami()
    if not who.get("ok"):
        if who.get("error") == "not_logged_in":
            die("нет сессии Пикабу. Войди один раз: python3 pikabu.py login")
        die(who.get("error", "не вошёл"))
    nick = who.get("user")
    print(f"Аккаунт: {nick or who.get('user_id')}   (id {who.get('user_id')})")
    age_days = None
    if who.get("signup_date"):
        age_days = (time.time() - who["signup_date"]) / 86400
        when = time.strftime("%d.%m.%Y", time.localtime(who["signup_date"]))
        print(f"  зарегистрирован {when} "
              + (f"({age_days:.0f} дн назад)" if age_days >= 1 else "(сегодня)"))
    prof = {}
    if nick:
        prof = parse_profile(pk.page(f"/@{urllib.parse.quote(nick)}"))
        kv_set(pk.conn, "profile", {**prof, "nick": nick, "karma": who.get("karma"),
                                    "signup_date": who.get("signup_date"),
                                    "at": time.time()})
    if prof:
        print(f"  рейтинг {prof.get('rating')}   постов {prof.get('stories')}   "
              f"в горячем {prof.get('hot')}   подписчиков {prof.get('followers')}")
    if who.get("karma") is not None:
        print(f"  карма {who['karma']}")
    flags = []
    if who.get("is_banned"):
        flags.append("АККАУНТ ЗАБАНЕН")
    if who.get("is_new_user"):
        flags.append("новичок")
    if who.get("slow_mode"):
        flags.append("медленный режим (лимит комментариев в сутки)")
    if not who.get("is_confirmed"):
        flags.append("почта/телефон не подтверждены")
    if who.get("is_golden"):
        flags.append("золотой аккаунт")
    print("  особенности: " + (", ".join(flags) if flags else "нет"))

    guards = pk.cfg["guards"]
    rating = prof.get("rating")
    if rating is not None and rating < guards["min_account_rating"]:
        print(f"\n⚠️  рейтинг {rating} ниже порога {guards['min_account_rating']}: "
              "пост утонет, начинай с комментариев")
    if age_days is not None and age_days < guards["min_account_age_days"]:
        print(f"⚠️  аккаунту {age_days:.0f} дн при пороге "
              f"{guards['min_account_age_days']}: у свежих аккаунтов посты минусуют"
              " почти рефлекторно. Первую неделю — только комментарии.")
    if who.get("slow_mode"):
        print("⚠️  медленный режим: комментариев в сутки мало, трать их на"
              " содержательные ответы")


def cmd_find(pk: Pikabu, args):
    query = " ".join(args.query)
    data = pk.ajax("/ajax/search_actions.php",
                   action="find_tags_users_communities", query=query)
    if data.get("_error"):
        die(f"поиск не ответил: {data.get('message') or data['_error']}")

    comms = data.get("communities") or []
    tags = data.get("tags") or []
    users = data.get("users") or []

    if comms:
        print(f"Сообщества по «{query}»:")
        for c in comms[:args.limit]:
            info = pk.ajax("/ajax.php", route="communities/get-info", id=c.get("id"))
            subs = info.get("subs_count") if not info.get("_error") else None
            stories = info.get("stories_count") if not info.get("_error") else None
            print(f"  /community/{c.get('link') or c.get('link_name')}  {c.get('name')}")
            if subs is not None:
                print(f"      {subs} подписчиков · {stories} постов")
    else:
        print(f"Сообществ по «{query}» не нашлось")

    if tags:
        print(f"\nТеги (число постов — размер ниши):")
        for t in tags[:args.limit]:
            print(f"  {t.get('name')}  —  {t.get('count')}")
    if users and args.users:
        print(f"\nАвторы:")
        for u in users[:10]:
            print(f"  @{u.get('name') or u.get('user_name')}")


def community_snapshot(pk: Pikabu, link: str) -> dict:
    """Сводка по сообществу: JSON-инфо + правила со страницы."""
    html = pk.page(f"/community/{urllib.parse.quote(link)}")
    if not html:
        die(f"сообщества /community/{link} нет")
    page = parse_community_page(html)
    cid = None
    m = re.search(r'data-community-id="(\d+)"', html)
    if m:
        cid = int(m.group(1))
    info = pk.ajax("/ajax.php", route="communities/get-info", id=cid) if cid else {}
    if info.get("_error"):
        info = {}
    stories = parse_stories(html)
    return {
        "link": link,
        "id": cid,
        "name": info.get("name") or strip_tags(
            (re.search(r'<title>([^<]*)', html) or re.match("", "")).group(1)
            if re.search(r'<title>', html) else ""),
        "subs": info.get("subs_count"),
        "stories": info.get("stories_count"),
        "description": page["description"] or strip_tags(info.get("description") or ""),
        "rules": page["rules"],
        "admins": page["admins"],
        "recent": stories,
    }


def cmd_comm(pk: Pikabu, args):
    sub = args.subcommand
    if sub == "list":
        rows = pk.conn.execute(
            "SELECT * FROM communities ORDER BY COALESCE(subs,0) DESC").fetchall()
        if not rows:
            print("рабочий список пуст: pikabu.py comm add <ссылка>")
            return
        for r in rows:
            if args.checked and not r["verdict"]:
                continue
            mark = {"ok": "✓", "careful": "⚠️", "no": "✗"}.get(r["verdict"], "·")
            print(f"{mark} /community/{r['link']}  {r['name'] or ''}  "
                  f"{r['subs'] or '?'} подписчиков")
            if r["verdict_note"]:
                print(f"     {r['verdict_note']}")
            if r["note"]:
                print(f"     заметка: {r['note']}")
        print("\n✓ можно · ⚠️ с оговорками · ✗ реклама запрещена "
              "(обычный пост туда всё равно можно, про своё — нет)")
        return

    link = args.target
    if not link:
        die("нужна ссылка сообщества: comm " + sub + " <ссылка>")
    link = link.strip("/").split("/")[-1]
    snap = community_snapshot(pk, link)

    if sub == "info":
        print(f"{snap['name']}  (/community/{snap['link']}, id {snap['id']})")
        print(f"  {snap['subs']} подписчиков · {snap['stories']} постов")
        if snap["description"]:
            print(f"\n{snap['description']}")
        if snap["admins"]:
            print("\nУправление: " + ", ".join(
                f"{a['name']} ({a['role']})" for a in snap["admins"] if a["role"]))
        if snap["recent"]:
            print("\nПоследние посты:")
            for s in snap["recent"][:8]:
                print(f"  [{s['rating']:+d} · {s['comments']} комм.] {s['title'][:70]}")
        return

    if sub == "rules":
        print(f"Правила /community/{snap['link']} — {snap['name']}\n")
        print(snap["rules"] or "(правила не указаны)")
        return

    if sub == "add":
        pk.conn.execute(
            "INSERT INTO communities(link,id,name,subs,stories,description,rules,"
            "note,added_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(link) DO UPDATE SET id=excluded.id, name=excluded.name, "
            "subs=excluded.subs, stories=excluded.stories, rules=excluded.rules, "
            "note=COALESCE(excluded.note, communities.note)",
            (snap["link"], snap["id"], snap["name"], snap["subs"], snap["stories"],
             snap["description"], snap["rules"], args.note, time.time()))
        pk.conn.commit()
        print(f"✓ /community/{snap['link']} в рабочем списке")
        return

    if sub == "check":
        verdict, notes = community_verdict(pk, snap)
        mark = {"ok": "✓ можно", "careful": "⚠️ осторожно", "no": "✗ не стоит"}[verdict]
        print(f"{snap['name']}  (/community/{snap['link']})")
        print(f"  {snap['subs']} подписчиков · {snap['stories']} постов")
        print(f"\nВердикт: {mark}")
        for n in notes:
            print(f"  • {n}")
        promo = promo_rule_quote(snap["rules"])
        pk.conn.execute(
            "INSERT INTO communities(link,id,name,subs,stories,description,rules,"
            "promo_rule,verdict,verdict_note,checked_at,added_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(link) DO UPDATE SET id=excluded.id, name=excluded.name, "
            "subs=excluded.subs, stories=excluded.stories, rules=excluded.rules, "
            "promo_rule=excluded.promo_rule, verdict=excluded.verdict, "
            "verdict_note=excluded.verdict_note, checked_at=excluded.checked_at",
            (snap["link"], snap["id"], snap["name"], snap["subs"], snap["stories"],
             snap["description"], snap["rules"], promo, verdict, "; ".join(notes),
             time.time(), time.time()))
        pk.conn.commit()
        return

    die(f"неизвестная подкоманда comm {sub}")


FORBID_MARKERS = ["запрещ", "нельзя", "не допуск", "не приветств", "бан", "удаля"]


def promo_rule_quote(rules: str) -> str | None:
    """
    Первая строка правил про рекламу или ссылки — вместе с шапкой списка.
    Без шапки смысл теряется: «— Рекламного характера» само по себе выглядит
    разрешением, хотя стоит под заголовком «Запрещено размещать посты:».
    """
    lines = [l.strip() for l in re.split(r"[\n;]", rules or "")]
    hits = []
    for i, line in enumerate(lines):
        low = line.lower()
        if not (any(w in low for w in PROMO_MARKERS) or any(w in low for w in LINK_MARKERS)):
            continue
        # Ищем шапку списка выше по тексту, перешагивая соседние пункты:
        # запрет обычно объявлен один раз, а под ним десяток строк с тире.
        head = ""
        for prev in reversed(lines[max(0, i - 8): i]):
            if not prev or re.match(r"^[-—•*]", prev):
                continue
            if prev.endswith(":") or any(w in prev.lower() for w in FORBID_MARKERS):
                head = prev + " "
            break
        hits.append((head + line).strip()[:300])
    if not hits:
        return None
    # Запрет рекламы важнее всего: сообщество может сначала требовать ссылку
    # на оригинал и только ниже запрещать рекламу — показать надо второе.
    def rank(h: str) -> int:
        low = h.lower()
        forbids = any(w in low for w in FORBID_MARKERS)
        promo = any(w in low for w in PROMO_MARKERS)
        return 0 if (forbids and promo) else 1 if forbids else 2

    return sorted(hits, key=rank)[0]


def community_verdict(pk: Pikabu, snap: dict) -> tuple[str, list[str]]:
    """
    Преполёт по площадке. Смотрим не только правила: мёртвое сообщество
    вреднее строгого — пост там не увидит никто.
    """
    notes = []
    verdict = "ok"

    promo = promo_rule_quote(snap["rules"])
    if promo:
        low = promo.lower()
        forbids = any(w in low for w in FORBID_MARKERS)
        # «Запрещено рекламное» — это отказ. «Обязательна ссылка на оригинал» —
        # требование к оформлению, а не запрет: такое не должно закрывать площадку.
        if forbids and any(w in low for w in PROMO_MARKERS):
            verdict = "no"
            notes.append(f"правило прямо запрещает рекламу: «{promo}»")
        else:
            verdict = "careful"
            notes.append(f"правило про рекламу/ссылки: «{promo}»")
    elif snap["rules"]:
        notes.append("в правилах про рекламу ничего нет — значит, решает модератор")
    else:
        notes.append("правила не опубликованы")

    recent = snap["recent"]
    if not recent:
        verdict = "no" if verdict == "ok" else verdict
        notes.append("на первой странице нет постов — сообщество мёртвое")
    else:
        now = time.time()
        fresh = [s for s in recent if s["timestamp"] and now - s["timestamp"] < 14 * 86400]
        med_rating = sorted(s["rating"] for s in recent)[len(recent) // 2]
        med_comm = sorted(s["comments"] for s in recent)[len(recent) // 2]
        notes.append(f"на первой странице {len(recent)} постов, свежих за 2 недели "
                     f"{len(fresh)}; медиана рейтинга {med_rating}, комментариев {med_comm}")
        if len(fresh) <= 1:
            verdict = "careful" if verdict == "ok" else verdict
            notes.append("почти нет свежих постов — площадка тихая")
        if med_comm == 0:
            notes.append("комментариев почти нет: разговора не будет, только просмотры")

    if snap["subs"] is not None and snap["subs"] < 500:
        notes.append(f"мало подписчиков ({snap['subs']}) — охват в основном из «Свежего»")
    return verdict, notes


def cmd_feed(pk: Pikabu, args):
    if args.community:
        path = f"/community/{urllib.parse.quote(args.community.strip('/').split('/')[-1])}"
    elif args.tag:
        path = f"/tag/{urllib.parse.quote(args.tag)}/{args.mode}"
    else:
        path = "/" + args.mode
    seen = []
    for page in range(1, args.pages + 1):
        html = pk.page(path, page=page if page > 1 else None)
        chunk = parse_stories(html)
        if not chunk:
            break
        seen.extend(chunk)
    if not seen:
        die("лента пустая — проверь адрес сообщества или тег")
    label = (f"/community/{args.community}" if args.community
             else f"тег «{args.tag}»" if args.tag else "/" + args.mode)
    print(f"{label}: {len(seen)} постов\n")
    for s in seen:
        age = human_age(s["timestamp"])
        comm = f" · {s['community_name']}" if s["community_name"] else ""
        print(f"[{s['rating']:+d} · {s['comments']} комм. · {age}{comm}]")
        print(f"  {s['title'][:90]}")
        print(f"  {s['url']}")
        if s["tags"]:
            print(f"  теги: {', '.join(s['tags'][:6])}")
    stats_line(seen)


def human_age(ts: int) -> str:
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 3600:
        return f"{int(d // 60)} мин"
    if d < 86400:
        return f"{int(d // 3600)} ч"
    return f"{int(d // 86400)} дн"


def stats_line(stories: list[dict]):
    if not stories:
        return
    ratings = sorted(s["rating"] for s in stories)
    comments = sorted(s["comments"] for s in stories)
    print(f"\nмедиана: рейтинг {ratings[len(ratings)//2]}, "
          f"комментариев {comments[len(comments)//2]}; "
          f"в минусе {sum(1 for r in ratings if r < 0)} из {len(ratings)}")


def cmd_search(pk: Pikabu, args):
    query = " ".join(args.query)
    found = []
    for page in range(1, args.pages + 1):
        html = pk.page("/search", q=query, page=page if page > 1 else None)
        chunk = parse_stories(html)
        if not chunk:
            break
        found.extend(chunk)
    now = time.time()
    if args.days:
        found = [s for s in found if s["timestamp"] and now - s["timestamp"] < args.days * 86400]
    if args.min_comments:
        found = [s for s in found if s["comments"] >= args.min_comments]
    found.sort(key=lambda s: s["timestamp"], reverse=True)
    if not found:
        print("ничего не нашлось под эти условия")
        return
    print(f"«{query}»: {len(found)} постов\n")
    for s in found:
        print(f"[{s['rating']:+d} · {s['comments']} комм. · {human_age(s['timestamp'])}] "
              f"{s['community_name'] or 'без сообщества'}")
        print(f"  {s['title'][:90]}")
        print(f"  {s['url']}")
    print("\nОтвечать выгоднее в свежих постах с живым обсуждением:")
    print("    python3 pikabu.py story <url> --comments 15")


def story_id_of(target: str) -> int:
    m = re.search(r"_(\d+)\s*$", target) or re.search(r"(\d{5,})", target)
    if not m:
        die(f"не понял, где здесь id поста: {target}")
    return int(m.group(1))


def cmd_story(pk: Pikabu, args):
    sid = story_id_of(args.target)
    html = pk.page(f"/story/_{sid}")
    if not html:
        die(f"пост {sid} не открывается — удалён или адрес неверный")
    stories = parse_stories(html)
    head = stories[0] if stories else {}
    body = story_text(html)
    print(f"{head.get('title', '')}")
    print(f"  {head.get('url', '')}")
    print(f"  автор @{head.get('author')} · рейтинг {head.get('rating')} · "
          f"{head.get('comments')} комментариев · {human_age(head.get('timestamp', 0))} назад")
    if head.get("community_name"):
        print(f"  сообщество: {head['community_name']} (/community/{head['community']})")
    if head.get("tags"):
        print(f"  теги: {', '.join(head['tags'])}")
    if body:
        print("\n" + body[:3000])

    if args.comments:
        data = pk.ajax("/ajax/comments_actions.php",
                       action="get_story_comments", story_id=sid)
        if data.get("_error"):
            print("\n(комментарии не отдались)")
            return
        comments = parse_comments(data.get("comments"))
        comments.sort(key=lambda c: (c["rating"] or 0), reverse=True)
        print(f"\nКомментарии ({data.get('total')}), верхние {args.comments}:\n")
        for c in comments[:args.comments]:
            print(f"[{c['rating']:+d}] @{c['author']}  (id {c['id']})")
            print("  " + (c["text"][:500].replace("\n", "\n  ")))
            print()
        print("Ответить:  python3 pikabu.py comment "
              f"{sid} --file ответ.md [--parent <id комментария>]")


def cmd_comment(pk: Pikabu, args):
    sid = story_id_of(args.target)
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text or not text.strip():
        die("нечего отправлять: --text или --file")
    text = text.strip()

    links = find_links(text)
    if links and not args.force:
        print("⚠️  в комментарии есть ссылки: " + ", ".join(links))
        print("    На Пикабу ссылка в первом же комментарии — самый частый повод")
        print("    для минусов и жалоб. Если она по делу, повтори с --force.")
        return

    blocks = md_to_blocks(text)
    desc = blocks[0]["body"]
    print(f"Пост {sid}" + (f", ответ на комментарий {args.parent}" if args.parent else ""))
    print("─" * 60)
    print(text[:1500])
    print("─" * 60)
    if args.dry:
        print("(--dry: ничего не отправлено)")
        return

    res = pk.act("/ajax/comments_actions.php", action="create", story_id=sid,
                 parent_id=args.parent or None, desc=desc, images="[]")
    if isinstance(res, dict) and res.get("_error"):
        die(f"Пикабу отказал: {res.get('message') or res['_error']}")
    cid = (res or {}).get("id") or (res or {}).get("comment_id")
    pk.conn.execute(
        "INSERT OR REPLACE INTO comments(id,story_id,parent_id,url,body,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (cid, sid, args.parent, f"{BASE}/story/_{sid}#comment_{cid}", text, time.time()))
    pk.conn.commit()
    print(f"✓ отправлено, комментарий {cid}")
    print(f"  {BASE}/story/_{sid}#comment_{cid}")


# ─────────────────────────────── публикация ───────────────────────────────

def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("text_file"):
        f = Path(spec["text_file"])
        if not f.is_absolute():
            f = path.parent / f
        spec["text"] = f.read_text(encoding="utf-8")
    return spec


def preflight(pk: Pikabu, spec: dict, force: bool) -> list[str]:
    """
    Всё, что Пикабу отвергнет или накажет. Возвращает список блокеров:
    пусто — можно публиковать.
    """
    blockers, warns = [], []
    guards = pk.cfg["guards"]
    conn = pk.conn

    title = (spec.get("title") or "").strip()
    text = (spec.get("text") or "").strip()
    tags = [t.strip() for t in (spec.get("tags") or []) if t.strip()]

    if not title:
        blockers.append("нет заголовка")
    elif len(title) > MAX_TITLE:
        blockers.append(f"заголовок длиннее {MAX_TITLE} символов ({len(title)})")
    if not text:
        blockers.append("нет текста")
    elif len(text) > MAX_TEXT:
        blockers.append(f"текст длиннее {MAX_TEXT} символов ({len(text)})")
    if len(tags) < MIN_TAGS:
        blockers.append(f"нужен минимум {MIN_TAGS} тег")
    if len(tags) > MAX_TAGS:
        blockers.append(f"тегов больше {MAX_TAGS}")

    # Ссылки. Редактор отклоняет их только когда сам считает пост рекламным
    # («Нельзя добавить пост со ссылками вне блога») — проверено вживую:
    # обычный пост с двумя ссылками на опрос ушёл без флага. Но именно ссылки
    # собирают минусы, поэтому это предупреждение, а не запрет.
    links = find_links(text)
    if links and not spec.get("advert"):
        warns.append(
            "в тексте ссылки (" + ", ".join(links[:3]) + "). Пикабу может принять"
            " пост, но ссылка на свой продукт — первая причина минусов;"
            " если пост рекламный по сути, честнее \"advert\": true")
    if spec.get("advert") and not (spec.get("advert_company") or "").strip():
        blockers.append("рекламный пост без \"advert_company\" (юрлицо) не публикуется")

    # Площадка
    link = (spec.get("community") or "").strip("/").split("/")[-1]
    if link:
        row = conn.execute("SELECT * FROM communities WHERE link=?", (link,)).fetchone()
        if not row or not row["verdict"]:
            warns.append(f"сообщество {link} не проверено: "
                         f"python3 pikabu.py comm check {link}")
        else:
            # Запрет рекламы закрывает площадку только для рекламного поста.
            # Обычный рассказ туда можно — но без единого намёка на своё.
            if row["verdict"] == "no":
                promo_post = bool(spec.get("advert")) or bool(links)
                if promo_post:
                    blockers.append(f"сообщество {link} помечено «не стоит», а пост"
                                    f" рекламный: {row['verdict_note']}")
                else:
                    warns.append(f"в {link} реклама запрещена ({row['verdict_note']}); "
                                 "пост не рекламный — но и намёка на своё быть не должно")
            elif row["verdict"] == "careful":
                warns.append(f"сообщество {link} с оговорками: {row['verdict_note']}")
            if row["promo_rule"] and row["verdict"] != "no":
                warns.append(f"правило площадки: «{row['promo_rule']}»")
            if row["checked_at"] and time.time() - row["checked_at"] > 90 * 86400:
                warns.append("проверка сообщества старше трёх месяцев")

    # Гарды по своей истории
    now = time.time()
    last = conn.execute(
        "SELECT * FROM posts ORDER BY created_at DESC LIMIT 1").fetchone()
    if last and last["created_at"]:
        hours = (now - last["created_at"]) / 3600
        if hours < guards["min_hours_between_posts"]:
            blockers.append(f"прошлый пост был {hours:.1f} ч назад, "
                            f"порог {guards['min_hours_between_posts']} ч")
    today = conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE created_at > ?", (now - 86400,)).fetchone()["c"]
    if today >= guards["max_posts_per_day"]:
        blockers.append(f"за сутки уже {today} постов, потолок {guards['max_posts_per_day']}")
    if link:
        same = conn.execute(
            "SELECT created_at FROM posts WHERE community=? ORDER BY created_at DESC LIMIT 1",
            (link,)).fetchone()
        if same and same["created_at"]:
            days = (now - same["created_at"]) / 86400
            if days < guards["days_between_same_community"]:
                blockers.append(
                    f"в {link} постил {days:.0f} дн назад, "
                    f"порог {guards['days_between_same_community']} дн")

    # Соотношение участия
    posts_n = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    comm_n = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    need = guards["comment_to_post_ratio"]
    if posts_n and comm_n < posts_n * need:
        warns.append(f"комментариев {comm_n} на {posts_n} своих постов "
                     f"(цель — {need} на пост): без участия посты минусуют быстрее")

    # Рейтинг аккаунта
    prof = kv_get(pk.conn, "profile") or {}
    rating = prof.get("rating")
    if rating is not None and rating < guards["min_account_rating"]:
        warns.append(f"рейтинг аккаунта {rating} ниже порога "
                     f"{guards['min_account_rating']} — пост почти наверняка утонет")

    for w in warns:
        print(f"⚠️  {w}")
    if force and blockers:
        for b in blockers:
            print(f"✗ (обойдено --force) {b}")
        return []
    return blockers


def cmd_post(pk: Pikabu, args):
    spec_path = Path(args.spec)
    if not spec_path.exists():
        die(f"нет файла спеки {spec_path}")
    spec = load_spec(spec_path)

    blockers = preflight(pk, spec, args.force)
    if blockers:
        print("\nПубликация остановлена:")
        for b in blockers:
            print(f"  ✗ {b}")
        print("\nОбойти сознательно: --force")
        sys.exit(2)

    title = spec["title"].strip()
    text = spec["text"].strip()
    tags = [t.strip() for t in spec.get("tags", []) if t.strip()]
    blocks = md_to_blocks(text)

    community_id = None
    link = (spec.get("community") or "").strip("/").split("/")[-1]
    if link:
        row = pk.conn.execute(
            "SELECT id FROM communities WHERE link=?", (link,)).fetchone()
        community_id = row["id"] if row and row["id"] else None
        if not community_id:
            snap = community_snapshot(pk, link)
            community_id = snap["id"]

    data = {
        "title": title,
        "blocks": blocks,
        "tags": ",".join(tags),
        "is_authors": bool(spec.get("authors", True)),
        "is_author_content": bool(spec.get("authors", True)),
        "is_adult": bool(spec.get("adult", False)),
        "is_anonymous_story": bool(spec.get("anonymous", False)),
        "is_comments_disabled": bool(spec.get("comments_disabled", False)),
        "is_donations_disabled": bool(spec.get("donations_disabled", False)),
        "is_advert_blogs": bool(spec.get("advert", False)),
        "advert_company": spec.get("advert_company", ""),
        "is_not_advert": not bool(spec.get("advert", False)),
        "is_story_boost": False,
        "scheduled_time": None,
        "color_theme": 0,
        "community": community_id,
        "is_community": bool(community_id),
        "parent_story_id": 0,
    }

    print(f"Заголовок: {title}")
    print(f"Теги: {', '.join(tags)}")
    print(f"Сообщество: {link or '— (в общую ленту)'}"
          + (f" (id {community_id})" if community_id else ""))
    print(f"Флаги: моё={data['is_authors']}, 18+={data['is_adult']}, "
          f"реклама={data['is_advert_blogs']}")
    print("─" * 60)
    print(text[:2000])
    print("─" * 60)
    if args.dry:
        print("(--dry: ничего не отправлено)")
        return

    res = pk.act("/ajax/gtpost_actions.php", action="publish",
                 time=str(int(time.time())),
                 data=json.dumps(data, ensure_ascii=False),
                 story_id=0, parent_story_id=0)
    if isinstance(res, dict) and res.get("_error"):
        die(f"Пикабу отказал: {res.get('message') or res['_error']}")
    sid = (res or {}).get("story_id") or (res or {}).get("id")
    url = (res or {}).get("link") or (res or {}).get("url") or (
        f"{BASE}/story/_{sid}" if sid else None)
    if not sid:
        print("⚠️  ответ без id поста, проверь вручную:")
        print(json.dumps(res, ensure_ascii=False)[:500])
        return
    pk.conn.execute(
        "INSERT OR REPLACE INTO posts(id,community,title,url,tags,created_at,spec) "
        "VALUES(?,?,?,?,?,?,?)",
        (int(sid), link or None, title, url, ",".join(tags), time.time(),
         str(spec_path)))
    pk.conn.commit()
    print(f"✓ опубликовано: {url}")
    print("\nЧерез час обязательно:  python3 pikabu.py verify --all")
    print("Пост может быть жив, но уже в минусе или снят модератором сообщества —")
    print("автору это не показывают.")


def cmd_track(pk: Pikabu, args):
    """
    Взять под наблюдение пост, опубликованный руками. Без этого гарды и
    соотношение участия считаются по неполной картине, а verify его не видит.
    """
    sid = story_id_of(args.target)
    html = pk.page(f"/story/_{sid}")
    if not html:
        die(f"пост {sid} не открывается")
    stories = parse_stories(html)
    if not stories:
        die(f"не разобрал страницу поста {sid}")
    st = stories[0]
    pk.conn.execute(
        "INSERT INTO posts(id,community,title,url,tags,created_at,rating,comments) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "title=excluded.title, url=excluded.url, tags=excluded.tags, "
        "rating=excluded.rating, comments=excluded.comments",
        (st["id"], st["community"], st["title"], st["url"], ",".join(st["tags"]),
         st["timestamp"] or time.time(), st["rating"], st["comments"]))
    pk.conn.commit()
    print(f"✓ под наблюдением: {st['title'][:70]}")
    print(f"  {st['url']}")
    args.id, args.all = st["id"], False
    cmd_verify(pk, args)


def cmd_verify(pk: Pikabu, args):
    rows = []
    if args.id:
        rows = pk.conn.execute("SELECT * FROM posts WHERE id=?", (args.id,)).fetchall()
    elif args.all:
        rows = pk.conn.execute(
            "SELECT * FROM posts WHERE created_at > ? ORDER BY created_at DESC",
            (time.time() - 30 * 86400,)).fetchall()
    else:
        rows = pk.conn.execute(
            "SELECT * FROM posts ORDER BY created_at DESC LIMIT 1").fetchall()
    if not rows:
        print("нечего проверять — своих постов в базе нет")
        return

    for r in rows:
        sid = r["id"]
        html = pk.page(f"/story/_{sid}")
        status, rating, comments = "removed", None, None
        if html:
            stories = parse_stories(html)
            if stories:
                st = stories[0]
                rating, comments = st["rating"], st["comments"]
                status = "downvoted" if (rating or 0) < 0 else "alive"
            elif re.search(r"(?i)пост\s+(удал|скры)", html):
                status = "removed"
            else:
                status = "unknown"
        # Пост в сообществе может ждать в очереди модерации: он открывается,
        # но в ленте сообщества его нет.
        if status == "alive" and r["community"]:
            feed = parse_stories(pk.page(f"/community/{r['community']}/new"))
            if feed and all(s["id"] != sid for s in feed):
                age_h = (time.time() - (r["created_at"] or 0)) / 3600
                if age_h < 48:
                    status = "in_queue"

        pk.conn.execute(
            "UPDATE posts SET alive=?, status=?, rating=?, comments=?, checked_at=? "
            "WHERE id=?",
            (1 if status in ("alive", "downvoted", "in_queue") else 0,
             status, rating, comments, time.time(), sid))
        pk.conn.commit()

        label = {
            "alive": "✓ жив",
            "downvoted": "⚠️ в минусе",
            "in_queue": "⚠️ не видно в ленте сообщества (очередь модерации?)",
            "removed": "✗ СНЯТ",
            "unknown": "? не разобрал страницу",
        }[status]
        print(f"{label}  [{rating if rating is not None else '?'} · "
              f"{comments if comments is not None else '?'} комм.]  {r['title'][:60]}")
        print(f"    {r['url']}")
        if status == "downvoted":
            print("    Минусуют. Удалять и перезаливать нельзя — это читается как"
                  " обход; лучше ответить в комментариях по делу.")
        if status == "removed":
            print("    Писать администратору сообщества, а не публиковать заново.")


def cmd_stats(pk: Pikabu, args):
    conn = pk.conn
    posts = conn.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()
    comments_n = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    need = pk.cfg["guards"]["comment_to_post_ratio"]
    print(f"Своих постов: {len(posts)}   комментариев: {comments_n}")
    if posts:
        ratio = comments_n / len(posts)
        mark = "✓" if ratio >= need else "⚠️"
        print(f"{mark} соотношение {ratio:.1f} комментариев на пост (цель {need})")
        alive = sum(1 for p in posts if p["status"] in ("alive", "downvoted", "in_queue"))
        minus = sum(1 for p in posts if p["status"] == "downvoted")
        removed = sum(1 for p in posts if p["status"] == "removed")
        print(f"  живых {alive}, в минусе {minus}, снятых {removed}, "
              f"не проверено {sum(1 for p in posts if not p['status'])}")
        print("\nПоследние посты:")
        for p in posts[:10]:
            when = time.strftime("%d.%m", time.localtime(p["created_at"] or 0))
            print(f"  {when}  [{p['rating'] if p['rating'] is not None else '?'}] "
                  f"{p['community'] or '—'}  {p['title'][:50]}")
    prof = kv_get(conn, "profile") or {}
    if prof:
        print(f"\nПрофиль на {time.strftime('%d.%m', time.localtime(prof.get('at', 0)))}: "
              f"рейтинг {prof.get('rating')}, постов {prof.get('stories')}, "
              f"в горячем {prof.get('hot')}")


def cmd_plan(pk: Pikabu, args):
    conn = pk.conn
    guards = pk.cfg["guards"]
    now = time.time()
    print("Гарды сейчас:")
    last = conn.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT 1").fetchone()
    if last and last["created_at"]:
        hours = (now - last["created_at"]) / 3600
        left = guards["min_hours_between_posts"] - hours
        print(f"  последний пост {hours:.1f} ч назад — "
              + (f"ждать ещё {left:.1f} ч" if left > 0 else "пауза выдержана ✓"))
    else:
        print("  своих постов ещё не было")
    today = conn.execute("SELECT COUNT(*) c FROM posts WHERE created_at > ?",
                         (now - 86400,)).fetchone()["c"]
    print(f"  за сутки {today} из {guards['max_posts_per_day']}")
    posts_n = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    comm_n = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    need = posts_n * guards["comment_to_post_ratio"] - comm_n
    if need > 0:
        print(f"  для соотношения не хватает {need} комментариев")
    else:
        print("  соотношение участия в норме ✓")

    rows = conn.execute(
        "SELECT link, name, verdict, checked_at FROM communities "
        "WHERE verdict IS NOT NULL ORDER BY verdict").fetchall()
    if not rows:
        print("\nПроверенных площадок нет: python3 pikabu.py comm check <ссылка>")
        return
    print("\nПлощадки:")
    for r in rows:
        same = conn.execute(
            "SELECT created_at FROM posts WHERE community=? "
            "ORDER BY created_at DESC LIMIT 1", (r["link"],)).fetchone()
        days = (now - same["created_at"]) / 86400 if same and same["created_at"] else None
        if days is not None and days < guards["days_between_same_community"]:
            print(f"  ✗ {r['link']} — постил {days:.0f} дн назад")
        elif r["verdict"] == "no":
            # Запрет рекламы не закрывает площадку для обычного поста.
            print(f"  ⚠️ {r['link']}  {r['name'] or ''} — только не про своё")
        else:
            mark = "✓" if r["verdict"] == "ok" else "⚠️"
            print(f"  {mark} {r['link']}  {r['name'] or ''}")


# ──────────────────────────────── разбор аргументов ────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pikabu.py",
        description="Пикабу: разведка сообществ, участие в обсуждениях, публикации",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="войти руками в открывшемся Chrome")
    sub.add_parser("me", help="аккаунт: рейтинг, посты, ограничения")

    s = sub.add_parser("find", help="сообщества, теги и авторы по теме")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--users", action="store_true", help="показать и авторов")

    s = sub.add_parser("comm", help="сообщества: info | rules | check | add | list")
    s.add_argument("subcommand", choices=["info", "rules", "check", "add", "list"])
    s.add_argument("target", nargs="?")
    s.add_argument("--note")
    s.add_argument("--checked", action="store_true")

    s = sub.add_parser("feed", help="лента: что заходит в нише")
    s.add_argument("mode", nargs="?", default="hot", choices=["hot", "new", "best"])
    s.add_argument("--community")
    s.add_argument("--tag")
    s.add_argument("--pages", type=int, default=1)

    s = sub.add_parser("search", help="посты по словам")
    s.add_argument("query", nargs="+")
    s.add_argument("--pages", type=int, default=2)
    s.add_argument("--days", type=int)
    s.add_argument("--min-comments", type=int, dest="min_comments")

    s = sub.add_parser("story", help="пост целиком и верхние комментарии")
    s.add_argument("target")
    s.add_argument("--comments", type=int, default=10)

    s = sub.add_parser("comment", help="ответить в пост")
    s.add_argument("target")
    s.add_argument("--text")
    s.add_argument("--file")
    s.add_argument("--parent", type=int)
    s.add_argument("--dry", action="store_true")
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("post", help="опубликовать пост из файла-спеки")
    s.add_argument("spec")
    s.add_argument("--dry", action="store_true")
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("track", help="взять под наблюдение пост, сделанный руками")
    s.add_argument("target")

    s = sub.add_parser("verify", help="жив ли пост, рейтинг, минусы")
    s.add_argument("--id", type=int)
    s.add_argument("--all", action="store_true")

    sub.add_parser("stats", help="свои посты и комментарии, соотношение")
    sub.add_parser("plan", help="что гарды разрешают сегодня")
    return p


HANDLERS = {
    "login": cmd_login, "me": cmd_me, "find": cmd_find, "comm": cmd_comm,
    "feed": cmd_feed, "search": cmd_search, "story": cmd_story,
    "comment": cmd_comment, "post": cmd_post, "track": cmd_track,
    "verify": cmd_verify,
    "stats": cmd_stats, "plan": cmd_plan,
}


def main():
    args = build_parser().parse_args()
    cfg = load_config()
    conn = db()
    pk = Pikabu(cfg, conn)
    try:
        HANDLERS[args.cmd](pk, args)
    finally:
        Pikabu.stop_server()
        conn.close()


if __name__ == "__main__":
    main()
