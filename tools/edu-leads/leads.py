#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Лиды образовательных учреждений РФ (ВО и СПО) для ESMS.

Реестр учреждений с официальными сайтами → обязательная по приказу Рособрнадзора
страница «Структура и органы управления» (/sveden/struct) с микроразметкой →
контакты ЛПР (ФИО, должность, email, телефон, подразделение).

Только stdlib. База — SQLite, WAL.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import re
import sqlite3
import ssl
import sys
import threading
import time
import unicodedata
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("EDU_LEADS_DB", os.path.join(HERE, "edu_leads.db"))

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

RANKING = {
    "ВО": "https://db-nica.ru/ratings/rejting-sajtov-obrazovatelnykh-organizaczij",
    "СПО": "https://db-nica.ru/ratings/rejting-sajtov-srednikh-speczialnykh-uchebnykh-zavedenij",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- ВО | СПО
    site        TEXT,                     -- официальный сайт
    host        TEXT,                     -- нормализованный хост (ключ дедупликации)
    region      TEXT,
    league      TEXT,
    sveden_url  TEXT,                     -- где реально нашлась структура
    status      TEXT DEFAULT 'new',       -- new | ok | empty | error | skip
    error       TEXT,
    persons     INTEGER DEFAULT 0,        -- всего людей записано
    lpr         INTEGER DEFAULT 0,        -- из них ЛПР (тир A/B)
    checked_at  TEXT,
    UNIQUE(name, kind)
);
CREATE TABLE IF NOT EXISTS persons (
    id          INTEGER PRIMARY KEY,
    inst_id     INTEGER NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    fio         TEXT NOT NULL,
    post        TEXT NOT NULL,
    division    TEXT,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    tier        TEXT,                     -- A (решает) | B (носитель боли) | C (прочие)
    role        TEXT,                     -- нормализованная роль
    source_url  TEXT,
    found_at    TEXT,
    UNIQUE(inst_id, fio, post)
);
CREATE TABLE IF NOT EXISTS org_contacts (
    inst_id     INTEGER PRIMARY KEY REFERENCES institutions(id) ON DELETE CASCADE,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    full_name   TEXT
);
CREATE INDEX IF NOT EXISTS idx_inst_status ON institutions(status);
CREATE INDEX IF NOT EXISTS idx_inst_region ON institutions(region);
CREATE INDEX IF NOT EXISTS idx_person_tier ON persons(tier);
"""


# ─────────────────────────────────────────────────────────── база

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    return con


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────── сеть

_ctx_verified = ssl.create_default_context()
try:  # системный трастстор — под VPN certifi-only рвётся (урок из tg-бота)
    import truststore  # type: ignore

    _ctx_verified = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:
    pass

_ctx_lax = ssl.create_default_context()
_ctx_lax.check_hostname = False
_ctx_lax.verify_mode = ssl.CERT_NONE
_ctx_lax.set_ciphers("DEFAULT@SECLEVEL=1")


def _decompress(raw: bytes, enc: str) -> bytes:
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception:
            return raw
    if "deflate" in enc:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            return raw
    return raw


def _decode(raw: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    cands = []
    if m:
        cands.append(m.group(1))
    head = raw[:4096].decode("latin-1", "ignore")
    m2 = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    if m2:
        cands.append(m2.group(1))
    cands += ["utf-8", "cp1251"]
    for c in cands:
        try:
            return raw.decode(c)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


# Резолвер провайдера (Грузия) не видит часть .ru-доменов — 1.1.1.1 видит.
# Патчим getaddrinfo: системный резолвер первый, при провале — запасной.
import socket  # noqa: E402

_dns_cache: dict[str, str | None] = {}
_dns_lock = threading.Lock()
_sys_getaddrinfo = socket.getaddrinfo


def _resolve_fallback(host: str) -> str | None:
    with _dns_lock:
        if host in _dns_cache:
            return _dns_cache[host]
    ip = None
    try:
        import subprocess

        for server in ("1.1.1.1", "9.9.9.9"):
            out = subprocess.run(
                ["dig", "+short", "+time=3", "+tries=1", f"@{server}", host, "A"],
                capture_output=True, text=True, timeout=8,
            ).stdout
            for line in out.splitlines():
                line = line.strip()
                if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", line):
                    ip = line
                    break
            if ip:
                break
    except Exception:
        ip = None
    with _dns_lock:
        _dns_cache[host] = ip
    return ip


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _sys_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        ip = _resolve_fallback(host) if isinstance(host, str) else None
        if not ip:
            raise
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 80))]


socket.getaddrinfo = _getaddrinfo

ANTIBOT = re.compile(
    r"(not a bot|checking your browser|cf-browser-verification|ddos-guard|"
    r"проверка браузера|_Incapsula_|attention required)",
    re.I,
)


def _is_ssl_problem(err: BaseException) -> bool:
    if isinstance(err, ssl.SSLError):
        return True
    reason = getattr(err, "reason", None)
    return isinstance(reason, ssl.SSLError) or "SSL" in str(err).upper()[:120]


def fetch(url: str, timeout: int = 15) -> tuple[str, str]:
    """Вернуть (html, final_url). Один заход; повтор только на битом TLS."""
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        },
    )
    for ctx in (_ctx_verified, _ctx_lax):
        try:
            with urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read(3_000_000)
                raw = _decompress(raw, r.headers.get("Content-Encoding", ""))
                return _decode(raw, r.headers.get("Content-Type", "")), r.geturl()
        except HTTPError:
            raise
        except (URLError, OSError, ValueError) as e:
            if ctx is _ctx_verified and url.startswith("https://") and _is_ssl_problem(e):
                continue  # самоподписанный/просроченный сертификат — обычное дело у .ru edu
            raise RuntimeError(str(e)[:160] or "fetch failed") from None
    raise RuntimeError("fetch failed")


# ─────────────────────────────────────────────────────────── микроразметка

VOID = {"meta", "link", "br", "img", "input", "hr", "source", "col"}


class Node:
    __slots__ = ("prop", "text", "children", "attrs")

    def __init__(self, prop=None, attrs=None):
        self.prop = prop
        self.text = []
        self.children = []
        self.attrs = attrs or {}

    def value(self) -> str:
        return clean(" ".join(self.text))


class Microdata(HTMLParser):
    """Дерево элементов с itemprop. Терпимо к незакрытым тегам."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [{"tag": None, "node": self.root}]

    # --- вспомогательное
    def _open_node(self) -> Node:
        for f in reversed(self.stack):
            if f["node"] is not None:
                return f["node"]
        return self.root

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        prop = a.get("itemprop")
        node = None
        if prop:
            node = Node(prop.strip(), a)
            self._open_node().children.append(node)
            # значение может жить в атрибуте
            if tag == "meta" and a.get("content"):
                node.text.append(a["content"])
            elif tag == "a" and a.get("href", "").lower().startswith("mailto:"):
                node.text.append(a["href"][7:].split("?")[0])
            elif tag == "a" and a.get("href", "").lower().startswith("tel:"):
                node.text.append(a["href"][4:])
        if tag not in VOID:
            self.stack.append({"tag": tag, "node": node})
        elif node is not None:
            pass  # void с itemprop уже учтён

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1]["tag"] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data and not data.isspace():
            for f in reversed(self.stack):
                if f["node"] is not None:
                    f["node"].text.append(data)
                    break


def clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", s).strip(" \t\n\r·—-–,;")


def collect_records(node: Node, out: list[dict]) -> None:
    """Запись = узел, среди прямых потомков которого есть fio и post."""
    props = {c.prop for c in node.children}
    if "fio" in props and "post" in props:
        rec: dict[str, str] = {}
        for c in node.children:
            v = c.value()
            if v and c.prop not in rec:
                rec[c.prop] = v
        out.append(rec)
        return
    for c in node.children:
        collect_records(c, out)


def flat_records(root: Node) -> list[dict]:
    """Фолбэк: плоская разметка без контейнера — режем по появлению fio."""
    flat: list[tuple[str, str]] = []

    def walk(n: Node):
        for c in n.children:
            if not c.children:
                v = c.value()
                if v:
                    flat.append((c.prop, v))
            else:
                walk(c)

    walk(root)
    out, cur = [], {}
    for prop, val in flat:
        if prop == "fio" and cur.get("fio"):
            out.append(cur)
            cur = {}
        if prop in cur and prop == "post":
            out.append(cur)
            cur = {}
        cur[prop] = val
    if cur.get("fio") and cur.get("post"):
        out.append(cur)
    return [r for r in out if r.get("fio") and r.get("post")]


def parse_sveden(html: str) -> list[dict]:
    p = Microdata()
    try:
        p.feed(html)
    except Exception:
        pass
    recs: list[dict] = []
    collect_records(p.root, recs)
    if not recs:
        recs = flat_records(p.root)
    return recs


# ─────────────────────────────────────────────────────────── классификатор ЛПР

# Tier A — подписывает и платит. Tier B — носитель боли расписания и внедренец.

# явный мусор — подразделения, к расписанию отношения не имеющие
NOISE = re.compile(
    r"(общежити|столов|питани|библиотек|бухгалтер|кадр|юридическ|правов|охран|безопасност|"
    r"пожарн|хозяйств|ахч|ахр|снабжен|закупк|гражданск\w+\s+оборон|медицин|здравпункт|"
    r"воспитательн|социальн|спорт|культур|музе|издательск|полиграф|типограф|транспорт|гараж|"
    r"склад|общественн|профсоюз|архив|мобилизацион|военн|волонтер|добровольч|"
    r"столярн|ремонтн|энергетическ\w+\s+служб|клиник|ветеринарн\w+\s+клиник)",
    re.I,
)

# подразделение, руководитель которого = первое лицо организации
HEAD_DIV = re.compile(
    r"(администрац|руководств|дирекц|ректорат|колледж|техникум|училищ|лице|"
    r"филиал|образовательн\w*\s+организац|учреждени|школ|академи|университет)",
    re.I,
)
# подразделение, руководитель которого — НЕ первое лицо (директор чего-то мелкого)
SUB_DIV = re.compile(
    r"(центр|комбинат|клуб|городок|музе|нии|лаборатори|факультет|кафедр|"
    r"обособленн|бассейн|санатор|база\b|полигон|мастерск|бизнес-инкубатор|"
    r"технопарк|издательств|редакци|общежит)",
    re.I,
)

EDU = r"(учебн|образовательн|методич|академич|учебно-методич|учебно-производств)"
DIGI = r"(цифров|информатизац|информационн\w*\s*технолог|трансформац|\bасу\b|\bцит\b|\bикт\b)"
UNIT = r"(управлени|отдел|часть|департамент|служб|сектор|бюро|центр|группа)"


def _n(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


# «секретарь ректора», «помощник директора» — не ЛПР, хотя должность звучит рядом
ATTENDANT = re.compile(
    r"^(и\.?\s*о\.?\s+)?(секретар|помощник|советник|референт|ассистент|"
    r"пресс-секретар|водител|делопроизводител|специалист приемной)",
    re.I,
)


def classify(post: str, division: str = "") -> tuple[str | None, str | None]:
    p, d = _n(post), _n(division)
    both = f"{p} | {d}"
    if ATTENDANT.match(p.strip()):
        return None, None

    # ── тир A: первое лицо и его профильные замы
    if re.search(r"\bи?\.?о?\.?\s*ректор", p) and "прорект" not in p:
        return "A", "ректор"
    if re.search(r"президент", p) and re.search(r"университет|академи|институт", both):
        return "A", "президент"
    if "прорект" in p:
        if re.search(EDU, p):
            return "A", "проректор по учебной работе"
        if re.search(DIGI, p):
            return "A", "проректор по цифровизации"
        if re.search(r"перв(ый|ого)", p):
            return "A", "первый проректор"
        return None, None  # по науке, АХЧ, воспитательной — не наш ЛПР
    if re.search(r"замест|^зам\.?\s|\bзам\.\s*директора", p):
        if re.search(EDU, p) or re.search(r"учебно-производств|практическ", p):
            return "A", "зам. директора по учебной работе"
        if re.search(DIGI, p):
            return "A", "зам. директора по цифровизации"
        return None, None
    if re.search(r"\bдиректор\b", p) and not NOISE.search(both):
        if re.search(r"филиал", p):
            return "A", "директор филиала"
        if SUB_DIV.search(d):
            return None, None
        if not d or HEAD_DIV.search(d):
            return "A", "директор"
        return None, None
    if re.search(r"начальник", p) and re.search(r"училищ|техникум|колледж", p):
        return "A", "директор"

    if NOISE.search(both):
        return None, None

    # ── тир B: у кого расписание болит и кто внедряет
    if re.search(r"расписани|диспетчер", both):
        return "B", "расписание"
    if re.search(EDU, both) and re.search(UNIT, both):
        return "B", "учебная часть / УМУ"
    if re.search(DIGI, both) and re.search(UNIT + r"|начальник|руководител", both):
        return "B", "ИТ / цифровизация"
    if re.search(r"^декан\b|декан\s+факультета", p):
        return "B", "декан"
    if re.search(r"заведующ", p) and re.search(r"отделени|учебн", both):
        return "B", "заведующий отделением"
    return None, None


EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RX = re.compile(r"(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{2,3}[\s\-()]*\d{2}[\s\-()]*\d{2}")


def pick_email(rec: dict) -> str | None:
    for k in ("email", "emailOrg", "e-mail"):
        v = rec.get(k)
        if v:
            m = EMAIL_RX.search(v)
            if m:
                return m.group(0).lower()
    return None


def pick_phone(rec: dict) -> str | None:
    for k in ("telephone", "phone", "tel"):
        v = rec.get(k)
        if v:
            m = PHONE_RX.search(v)
            if m:
                return clean(m.group(0))
            return clean(v)[:40] or None
    return None


# токен ФИО: Иванов | ИВАНОВ | Иванов-Петров | И. | И.О.
FIO_TOKEN = re.compile(r"^(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?|[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?|[А-ЯЁ]\.(?:[А-ЯЁ]\.)?)$")
NOT_FIO = re.compile(
    r"(вакансия|вакантн|не\s*назначен|отсутств|нет\s+данных|временно|исполняющ|"
    r"должность|сведени|информац|не\s+предусмотрен|не\s+имеет)",
    re.I,
)


def looks_like_fio(s: str) -> bool:
    s = clean(s)
    if not s or len(s) > 80 or re.search(r"\d|@|http|«|»|\"", s):
        return False
    if NOT_FIO.search(s):
        return False
    toks = [t for t in s.replace(",", " ").split() if t]
    if not 2 <= len(toks) <= 4:
        return False
    return all(FIO_TOKEN.match(t) for t in toks)


# ─────────────────────────────────────────────────────────── реестр

TR_RX = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
TD_RX = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RX = re.compile(r"<[^>]+>")


def norm_host(site: str) -> str | None:
    s = clean(site)
    if not s:
        return None
    if not re.match(r"^https?://", s, re.I):
        s = "http://" + s
    try:
        h = urlparse(s).hostname or ""
    except ValueError:
        return None
    h = h.lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return h or None


def norm_site(site: str) -> str | None:
    s = clean(site)
    if not s:
        return None
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    try:
        u = urlparse(s)
    except ValueError:
        return None
    if not u.hostname:
        return None
    host = u.hostname
    if not host.isascii():  # кириллические .рф — в punycode, иначе рвётся заголовок
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            return None
    return f"{u.scheme}://{host}"


def cmd_registry(args) -> None:
    con = db()
    kinds = ["ВО", "СПО"] if args.kind == "all" else [args.kind]
    regions = load_regions(RANKING["СПО"])
    added = updated = 0
    for kind in kinds:
        base = RANKING[kind]
        for league in range(args.from_league, 6):
            for rcode, rname in regions:
                url = f"{base}?group={league}&region={rcode}"
                try:
                    html, _ = fetch(url, timeout=90)
                except Exception as e:
                    print(f"  ! {kind} лига{league} {rname}: {e}", file=sys.stderr)
                    continue
                rows = parse_ranking(html)
                for name, site, lg in rows:
                    a, u = upsert_inst(con, name, kind, site, rname, lg or f"Лига {league}")
                    added += a
                    updated += u
                con.commit()
                print(f"  {kind} · лига {league} · {rname}: {len(rows)} (всего +{added})", flush=True)
    total = con.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
    print(f"реестр: +{added} новых, {updated} обновлено, всего {total}")


def load_regions(url: str) -> list[tuple[int, str]]:
    html, _ = fetch(url, timeout=60)
    m = re.search(r'<select[^>]+id="region".*?</select>', html, re.S | re.I)
    if not m:
        return [(0, "")]
    opts = re.findall(r'<option value="(\d+)"[^>]*>\s*([^<]*?)\s*</option>', m.group(0))
    return [(int(c), clean(n)) for c, n in opts if int(c) != 0]


def parse_ranking(html: str) -> list[tuple[str, str, str]]:
    out = []
    for tr in TR_RX.findall(html):
        cells = [clean(TAG_RX.sub(" ", c)) for c in TD_RX.findall(tr)]
        if len(cells) < 3:
            continue
        league, name, site = cells[0], cells[1], cells[2]
        if not name or name.lower() in ("вуз", "поо", "сайт"):
            continue
        if not re.search(r"[a-zа-я]", site, re.I):
            continue
        out.append((name, site, league))
    return out


def upsert_inst(con, name, kind, site, region, league) -> tuple[int, int]:
    name = clean(name)
    site_n = norm_site(site)
    host = norm_host(site)
    row = con.execute(
        "SELECT id, region, site FROM institutions WHERE name=? AND kind=?", (name, kind)
    ).fetchone()
    if row:
        if not row["region"] and region:
            con.execute("UPDATE institutions SET region=? WHERE id=?", (region, row["id"]))
            return 0, 1
        return 0, 0
    con.execute(
        "INSERT OR IGNORE INTO institutions(name,kind,site,host,region,league) VALUES(?,?,?,?,?,?)",
        (name, kind, site_n, host, region, league),
    )
    return 1, 0


# ─────────────────────────────────────────────────────────── обход sveden

SVEDEN_PATHS = ["/sveden/struct/", "/sveden/struct", "/sveden/"]
BUDGET = 70  # секунд на одно учреждение — иначе один мёртвый сайт держит поток

_print_lock = threading.Lock()


def find_sveden_link(html: str, base: str) -> str | None:
    for m in re.finditer(r'href=["\']([^"\']*sveden/struct[^"\']*)["\']', html, re.I):
        return urljoin(base, m.group(1))
    for m in re.finditer(r'href=["\']([^"\']*/sveden[^"\']*)["\']', html, re.I):
        return urljoin(base, m.group(1))
    return None


def crawl_one(inst: sqlite3.Row) -> dict:
    res = {"id": inst["id"], "status": "error", "error": None, "url": None, "recs": [], "org": {}}
    site = inst["site"]
    if not site:
        res["status"] = "skip"
        res["error"] = "нет сайта"
        return res

    deadline = time.time() + BUDGET
    tried: list[str] = []
    html = final = None
    root = site.rstrip("/")
    bases = [root]
    if root.startswith("https://"):
        bases.append("http://" + root[len("https://") :])
    else:
        bases.append("https://" + root[len("http://") :])
    scheme, rest = root.split("://", 1)
    alt = rest[4:] if rest.startswith("www.") else "www." + rest
    bases.append(f"{scheme}://{alt}")

    dead_host = False
    for base in bases:
        for path in SVEDEN_PATHS:
            if time.time() > deadline:
                tried.append("бюджет времени исчерпан")
                dead_host = True
                break
            try:
                html, final = fetch(base + path, timeout=15)
                if "itemprop" in html:
                    break
                if ANTIBOT.search(html[:4000]):
                    tried.append(f"{path}: антибот-заслон")
                    html = None
                    dead_host = True
                    break
                tried.append(f"{path}: без микроразметки")
                html = None
            except HTTPError as e:
                tried.append(f"{path}: HTTP {e.code}")
                if e.code in (401, 403):  # антибот — дальше по этому хосту смысла нет
                    dead_host = True
                    break
            except Exception as e:
                tried.append(f"{path}: {str(e)[:50]}")
                if "Name or service not known" in str(e) or "nodename nor servname" in str(e):
                    dead_host = True
                    break
        if html is not None or dead_host:
            break

    # последняя попытка — найти ссылку на структуру с главной
    if html is None and not dead_host and time.time() < deadline:
        try:
            home, hurl = fetch(bases[0], timeout=15)
            link = find_sveden_link(home, hurl)
            if link:
                html, final = fetch(link, timeout=15)
        except Exception as e:
            tried.append(f"главная: {str(e)[:50]}")

    if html is None:
        res["error"] = "; ".join(tried[:3])[:250]
        return res

    recs = parse_sveden(html)
    res["url"] = final
    res["recs"] = recs

    # «Основные сведения» — руководитель организации и контакты приёмной
    if time.time() < deadline:
        common_base = final.split("/sveden/")[0] if "/sveden/" in (final or "") else bases[0]
        try:
            chtml, curl = fetch(common_base + "/sveden/common/", timeout=12)
            res["org"] = parse_common(chtml)
            for r in parse_sveden(chtml):
                if r not in recs:
                    recs.append(r)
        except Exception:
            pass

    res["status"] = "ok" if recs else "empty"
    if not recs:
        res["error"] = "структура без ФИО/должностей"
    return res


COMMON_PROPS = {
    "email": ("email", "emailOrg", "emailRuk"),
    "phone": ("telephone", "telephoneOrg", "telephoneRuk"),
    "address": ("addressStr", "address"),
    "full_name": ("fullname", "nameOrg", "shortname"),
}


def parse_common(html: str) -> dict:
    p = Microdata()
    try:
        p.feed(html)
    except Exception:
        pass
    flat: dict[str, str] = {}

    def walk(n: Node):
        for c in n.children:
            v = c.value()
            if v and c.prop not in flat:
                flat[c.prop] = v
            walk(c)

    walk(p.root)
    out = {}
    for key, props in COMMON_PROPS.items():
        for prop in props:
            if flat.get(prop):
                val = flat[prop]
                if key == "email":
                    m = EMAIL_RX.search(val)
                    val = m.group(0).lower() if m else None
                elif key == "phone":
                    m = PHONE_RX.search(val)
                    val = clean(m.group(0)) if m else clean(val)[:40]
                if val:
                    out[key] = val[:200]
                    break
    return out


def cmd_crawl(args) -> None:
    con = db()
    where = "status IN ('new')" if not args.retry else "status IN ('new','error','empty')"
    params: list = []
    if args.kind != "all":
        where += " AND kind=?"
        params.append(args.kind)
    if args.region:
        where += " AND region LIKE ?"
        params.append(f"%{args.region}%")
    q = f"SELECT * FROM institutions WHERE {where} ORDER BY kind DESC, id"
    if args.limit:
        q += f" LIMIT {int(args.limit)}"
    todo = con.execute(q, params).fetchall()
    print(f"к обходу: {len(todo)} учреждений, потоков {args.workers}")
    if not todo:
        return

    done = {"n": 0, "ok": 0, "persons": 0}
    t0 = time.time()

    def work(inst):
        try:
            return crawl_one(inst)
        except Exception as e:  # ничто не должно ронять пул
            return {"id": inst["id"], "status": "error", "error": str(e)[:200],
                    "url": None, "recs": [], "org": {}}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(work, todo):
            save_result(con, res)
            con.commit()  # короткие транзакции — иначе параллельный registry ловит locked
            done["n"] += 1
            if res["status"] == "ok":
                done["ok"] += 1
            if done["n"] % 25 == 0:
                el = time.time() - t0
                rate = done["n"] / el if el else 0
                left = (len(todo) - done["n"]) / rate / 60 if rate else 0
                cnt, lp = con.execute(
                    "SELECT COUNT(*), SUM(tier IN ('A','B')) FROM persons").fetchone()
                print(f"  {done['n']}/{len(todo)} · с контактами {done['ok']} · "
                      f"людей {cnt} (ЛПР {lp or 0}) · ~{left:.0f} мин", flush=True)

    con.commit()
    cnt, lp = con.execute("SELECT COUNT(*), SUM(tier IN ('A','B')) FROM persons").fetchone()
    print(f"обход завершён: {done['n']} учреждений, с контактами {done['ok']}, "
          f"людей в базе {cnt}, из них ЛПР {lp or 0}")


def save_result(con, res) -> None:
    """Пишем всех найденных людей; ЛПР помечаем тиром A/B, остальных — C."""
    kept = lpr = 0
    for rec in res["recs"]:
        fio = clean(rec.get("fio", ""))
        post = clean(rec.get("post", ""))
        division = clean(rec.get("name", "") or rec.get("nameOp", ""))
        if not fio or not post or not looks_like_fio(fio):
            continue
        tier, role = classify(post, division)
        if not tier:
            tier, role = "C", None
        else:
            lpr += 1
        con.execute(
            """INSERT OR IGNORE INTO persons
               (inst_id,fio,post,division,email,phone,address,tier,role,source_url,found_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (res["id"], fio, post[:200], division[:200], pick_email(rec), pick_phone(rec),
             clean(rec.get("addressStr", ""))[:200] or None, tier, role, res["url"], now()),
        )
        kept += 1
    org = res.get("org") or {}
    if org:
        con.execute(
            """INSERT OR REPLACE INTO org_contacts(inst_id,email,phone,address,full_name)
               VALUES(?,?,?,?,?)""",
            (res["id"], org.get("email"), org.get("phone"),
             org.get("address"), org.get("full_name")),
        )
    status = res["status"]
    if status == "ok" and kept == 0:
        status = "empty"
        res["error"] = "структура есть, ФИО не распознаны"
    con.execute(
        """UPDATE institutions SET status=?, error=?, sveden_url=?, persons=?, lpr=?, checked_at=?
           WHERE id=?""",
        (status, res.get("error"), res.get("url"), kept, lpr, now(), res["id"]),
    )


# ─────────────────────────────────────────────────────────── отчёты

def cmd_reclassify(args) -> None:
    """Пересчитать тиры по уже собранным должностям — без повторного обхода."""
    con = db()
    rows = con.execute("SELECT id, post, division, tier FROM persons").fetchall()
    changed = 0
    for r in rows:
        tier, role = classify(r["post"], r["division"] or "")
        tier = tier or "C"
        if tier != r["tier"]:
            con.execute("UPDATE persons SET tier=?, role=? WHERE id=?", (tier, role, r["id"]))
            changed += 1
        elif tier != "C":
            con.execute("UPDATE persons SET role=? WHERE id=?", (role, r["id"]))
    con.execute(
        """UPDATE institutions SET lpr = (
               SELECT COUNT(*) FROM persons p WHERE p.inst_id = institutions.id
               AND p.tier IN ('A','B'))"""
    )
    con.commit()
    print(f"пересчитано {len(rows)} записей, тир изменён у {changed}")


def cmd_stats(args) -> None:
    con = db()
    print("Учреждения")
    for r in con.execute(
        "SELECT kind, status, COUNT(*) n FROM institutions GROUP BY kind, status ORDER BY kind, n DESC"
    ):
        print(f"  {r['kind']:4} {r['status']:7} {r['n']:6}")
    tot = con.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
    withp = con.execute("SELECT COUNT(*) FROM institutions WHERE persons>0").fetchone()[0]
    withl = con.execute("SELECT COUNT(*) FROM institutions WHERE lpr>0").fetchone()[0]
    print(f"  всего {tot}, с контактами {withp}, из них с ЛПР {withl}")
    print("\nЛюди по тирам")
    names = {"A": "A · решает", "B": "B · носитель боли", "C": "C · прочие"}
    for r in con.execute(
        "SELECT tier, COUNT(*) n, SUM(email IS NOT NULL) e, SUM(phone IS NOT NULL) t "
        "FROM persons GROUP BY tier ORDER BY tier"
    ):
        print(f"  {names.get(r['tier'], r['tier']):20} {r['n']:7} (email {r['e']}, тел {r['t']})")
    print("\nРоли ЛПР")
    for r in con.execute(
        "SELECT role, tier, COUNT(*) n FROM persons WHERE tier IN ('A','B') "
        "GROUP BY role ORDER BY n DESC LIMIT 15"
    ):
        print(f"  {r['tier']} {r['role'] or '—':34} {r['n']:6}")
    print("\nТоп регионов (учреждений с ЛПР)")
    for r in con.execute(
        "SELECT region, COUNT(*) n FROM institutions WHERE lpr>0 GROUP BY region ORDER BY n DESC LIMIT 12"
    ):
        print(f"  {r['region'] or '—':38} {r['n']:5}")


def cmd_export_csv(args) -> None:
    con = db()
    path = args.path or os.path.join(HERE, "edu_leads.csv")
    q = """SELECT i.kind, i.region, i.name AS institution, i.site,
                  p.tier, p.role, p.fio, p.post, p.division, p.email, p.phone, p.source_url
           FROM persons p JOIN institutions i ON i.id = p.inst_id
           WHERE 1=1"""
    params: list = []
    if args.tier:
        q += " AND p.tier=?"
        params.append(args.tier)
    if args.lpr:
        q += " AND p.tier IN ('A','B')"
    if args.kind != "all":
        q += " AND i.kind=?"
        params.append(args.kind)
    if args.with_email:
        q += " AND p.email IS NOT NULL"
    q += " ORDER BY i.kind, i.region, i.name, p.tier"
    rows = con.execute(q, params).fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Тип", "Регион", "Учреждение", "Сайт", "Тир", "Роль", "ФИО",
                    "Должность", "Подразделение", "Email", "Телефон", "Источник"])
        for r in rows:
            w.writerow([r[k] or "" for k in r.keys()])
    print(f"выгружено {len(rows)} строк → {path}")


def cmd_show(args) -> None:
    con = db()
    rows = con.execute(
        """SELECT i.name, i.kind, i.region, i.site, p.tier, p.role, p.fio, p.post, p.email, p.phone
           FROM persons p JOIN institutions i ON i.id=p.inst_id
           WHERE i.name LIKE ? ORDER BY p.tier, p.role""",
        (f"%{args.query}%",),
    ).fetchall()
    if not rows:
        print("ничего не найдено")
        return
    cur = None
    for r in rows:
        if r["name"] != cur:
            cur = r["name"]
            print(f"\n{cur} · {r['kind']} · {r['region']} · {r['site']}")
        print(f"  [{r['tier']}] {r['fio']:34} {r['post'][:60]:60} {r['email'] or ''} {r['phone'] or ''}")


# ─────────────────────────────────────────────────────────── Органон

VAULT = os.environ.get(
    "VAULT_DIR",
    os.path.expanduser(
        "~/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
    ),
)
SECTION = "08 — Социальный капитал"
BASE_DIR = os.path.join(VAULT, SECTION, "Учебные заведения РФ")

TIER_LABEL = {"A": "A · решает", "B": "B · носитель боли", "C": "C · прочие"}


def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:|#\[\]^]', "-", s).strip() or "Без региона"


def cmd_obsidian(args) -> None:
    con = db()
    os.makedirs(os.path.join(BASE_DIR, "Регионы"), exist_ok=True)
    regions = [r[0] for r in con.execute(
        "SELECT DISTINCT region FROM institutions WHERE region IS NOT NULL ORDER BY region")]

    written = 0
    for region in regions:
        rows = con.execute(
            """SELECT i.name inst, i.kind, i.site, i.status, i.lpr,
                      p.tier, p.role, p.fio, p.post, p.division, p.email, p.phone
               FROM institutions i LEFT JOIN persons p
                    ON p.inst_id = i.id AND p.tier IN ('A','B')
               WHERE i.region = ?
               ORDER BY i.kind DESC, i.name, p.tier, p.role""",
            (region,),
        ).fetchall()
        if not rows:
            continue
        insts = {}
        for r in rows:
            insts.setdefault((r["inst"], r["kind"], r["site"], r["status"]), []).append(r)

        n_lpr = sum(1 for r in rows if r["fio"])
        out = [
            "---",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            "tags: [лиды, esms, образование, спо, во]",
            f"регион: {region}",
            "---",
            "",
            f"# {region} — вузы и колледжи",
            "",
            f"Учреждений: **{len(insts)}** · ЛПР найдено: **{n_lpr}**. "
            "Источник контактов — обязательный раздел «Сведения об образовательной организации» "
            "(`/sveden/struct`) на официальных сайтах.",
            "",
            "Полная база (включая прочие должности) — `tools/edu-leads/edu_leads.db`.",
            "",
        ]
        for (inst, kind, site, status), people in sorted(insts.items(), key=lambda x: (-len(x[1]), x[0][0])):
            out.append(f"## {inst}")
            meta = [f"тип: {kind}"]
            if site:
                meta.append(f"сайт: {site}")
            out.append(" · ".join(meta))
            out.append("")
            real = [p for p in people if p["fio"]]
            if not real:
                why = {"error": "сайт недоступен", "empty": "структура без контактов",
                       "new": "ещё не обходили", "skip": "нет сайта"}.get(status, status)
                out.append(f"> ЛПР не найдены — {why}.")
                out.append("")
                continue
            out.append("| Тир | ФИО | Должность | Подразделение | Email | Телефон |")
            out.append("|---|---|---|---|---|---|")
            for p in real:
                out.append("| {} | {} | {} | {} | {} | {} |".format(
                    p["tier"], md_escape(p["fio"]), md_escape(p["post"]),
                    md_escape(p["division"] or ""), p["email"] or "", p["phone"] or ""))
            out.append("")
        path = os.path.join(BASE_DIR, "Регионы", f"{safe_name(region)}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        written += 1

    # MOC
    tot = con.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM institutions WHERE status!='new'").fetchone()[0]
    withl = con.execute("SELECT COUNT(*) FROM institutions WHERE lpr>0").fetchone()[0]
    ppl, lpr = con.execute("SELECT COUNT(*), SUM(tier IN ('A','B')) FROM persons").fetchone()
    by_kind = {r["kind"]: r["n"] for r in con.execute(
        "SELECT kind, COUNT(*) n FROM institutions GROUP BY kind")}
    roles = con.execute(
        "SELECT role, tier, COUNT(*) n FROM persons WHERE tier IN ('A','B') "
        "GROUP BY role ORDER BY n DESC").fetchall()
    reg_rows = con.execute(
        """SELECT region, COUNT(*) n, SUM(lpr>0) c, SUM(lpr) l
           FROM institutions WHERE region IS NOT NULL GROUP BY region ORDER BY l DESC"""
    ).fetchall()

    moc = [
        "---",
        f"date: {datetime.now().strftime('%Y-%m-%d')}",
        "tags: [moc, лиды, esms, образование]",
        "---",
        "",
        "# Учебные заведения РФ — база лидов ESMS",
        "",
        "Контакты руководства вузов и колледжей для предложения пилота системы "
        "управления электронным расписанием ([[Видение|ESMS]]).",
        "",
        "## Что внутри",
        "",
        f"- Учреждений в реестре: **{tot}** (ВО {by_kind.get('ВО', 0)} · СПО {by_kind.get('СПО', 0)})",
        f"- Обойдено сайтов: **{done}**, контакты ЛПР найдены у **{withl}**",
        f"- Людей записано: **{ppl}**, из них помечено как ЛПР: **{lpr or 0}**",
        "",
        "## Тиры",
        "",
        "| Тир | Кто это | Зачем |",
        "|---|---|---|",
        "| **A** | ректор, директор, проректор/зам по учебной работе и цифровизации | подписывает и платит |",
        "| **B** | учебная часть, УМУ, диспетчерская расписания, ИТ-управление, деканы | у них болит и они внедряют |",
        "| **C** | остальные должности с сайта | резерв, для поиска входа |",
        "",
        "Тир C в заметки по регионам не выводится — он только в базе "
        "`tools/edu-leads/edu_leads.db`.",
        "",
        "## Роли ЛПР",
        "",
        "| Тир | Роль | Людей |",
        "|---|---|---|",
    ]
    for r in roles:
        moc.append(f"| {r['tier']} | {r['role'] or '—'} | {r['n']} |")
    moc += [
        "",
        "## Регионы",
        "",
        "| Регион | Учреждений | С контактами | ЛПР |",
        "|---|---|---|---|",
    ]
    for r in reg_rows:
        link = f"[[{safe_name(r['region'])}]]"
        moc.append(f"| {link} | {r['n']} | {r['c'] or 0} | {r['l'] or 0} |")
    moc += [
        "",
        "## Как пользоваться",
        "",
        "```bash",
        'cd "~/Desktop/personal os/tools/edu-leads"',
        "python3 leads.py stats                      # сводка",
        'python3 leads.py show "Кубанский"           # контакты одного учреждения',
        "python3 leads.py export-csv --lpr --with-email",
        'python3 leads.py promote --region "Краснодарский край" --tier A   # в соцкапитал',
        "```",
        "",
        "Перенос в [[MOC — 08 — Социальный капитал|соцкапитал]] делается точечно "
        "командой `promote` — массово карточки не заводим, иначе база людей утонет.",
        "",
        "> Контакты взяты из официально раскрываемых по приказу Рособрнадзора № 831 "
        "сведений на сайтах учреждений. Для рассылки — соблюдать 152-ФЗ и требования "
        "к рекламным сообщениям.",
    ]
    with open(os.path.join(BASE_DIR, "MOC — Учебные заведения РФ.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(moc))
    print(f"Органон: {written} заметок по регионам + MOC → {SECTION}/Учебные заведения РФ/")


# ─────────────────────────────────────────────────────────── перенос в соцкапитал

CAPITAL = os.path.join(os.path.dirname(HERE), "social-capital", "capital.py")
CAT = {"ВО": "leads-vuzy", "СПО": "leads-spo"}


def cmd_promote(args) -> None:
    import subprocess

    con = db()
    q = """SELECT p.fio, p.post, p.email, p.phone, p.tier, p.role,
                  i.name inst, i.kind, i.region, i.site
           FROM persons p JOIN institutions i ON i.id=p.inst_id
           WHERE p.tier IN ('A','B')"""
    params: list = []
    if args.tier:
        q = q.replace("p.tier IN ('A','B')", "p.tier=?")
        params.append(args.tier)
    if args.region:
        q += " AND i.region LIKE ?"
        params.append(f"%{args.region}%")
    if args.inst:
        q += " AND i.name LIKE ?"
        params.append(f"%{args.inst}%")
    if args.kind != "all":
        q += " AND i.kind=?"
        params.append(args.kind)
    if not args.no_email:
        q += " AND p.email IS NOT NULL"
    q += " ORDER BY i.kind DESC, i.name, p.tier"
    if args.limit:
        q += f" LIMIT {int(args.limit)}"
    rows = con.execute(q, params).fetchall()
    print(f"к переносу: {len(rows)} контактов")
    if args.dry:
        for r in rows[:40]:
            print(f"  [{r['tier']}] {r['fio']} · {r['post'][:50]} · {r['inst'][:45]} · {r['email']}")
        if len(rows) > 40:
            print(f"  … ещё {len(rows) - 40}")
        return

    subprocess.run([sys.executable, CAPITAL, "category", "add", "leads-spo", "Колледжи",
                    "--parent", "leads"], capture_output=True)
    ok = fail = 0
    for r in rows:
        cmd = [sys.executable, CAPITAL, "add", r["fio"],
               "--category", CAT[r["kind"]],
               "--org", r["inst"], "--role", r["post"][:120],
               "--city", r["region"] or "",
               "--source", "edu-leads / sveden",
               "--tags", f"{r['kind'].lower()},лпр,тир-{r['tier']},esms",
               "--notes", f"ЛПР тир {r['tier']} ({r['role'] or '—'}) · {r['site'] or ''}"]
        if r["email"]:
            cmd += ["--email", r["email"]]
        if r["phone"]:
            cmd += ["--phone", r["phone"]]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f"  ! {r['fio']}: {(p.stderr or p.stdout).strip()[:150]}")
    print(f"перенесено {ok}, ошибок {fail}. Дальше: capital.py sync (карточки в Обсидиан)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Лиды вузов и колледжей РФ (ЛПР) для ESMS")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("registry", help="собрать реестр учреждений с сайтами")
    p.add_argument("--kind", default="all", choices=["all", "ВО", "СПО"])
    p.add_argument("--from-league", type=int, default=1, help="продолжить с лиги N")
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser("crawl", help="обойти /sveden/struct и собрать ЛПР")
    p.add_argument("--kind", default="all", choices=["all", "ВО", "СПО"])
    p.add_argument("--region", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--retry", action="store_true", help="повторить error/empty")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("stats", help="сводка")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("reclassify", help="пересчитать тиры без повторного обхода")
    p.set_defaults(func=cmd_reclassify)

    p = sub.add_parser("export-csv", help="плоская выгрузка")
    p.add_argument("path", nargs="?")
    p.add_argument("--tier", choices=["A", "B", "C"])
    p.add_argument("--lpr", action="store_true", help="только тиры A и B")
    p.add_argument("--kind", default="all", choices=["all", "ВО", "СПО"])
    p.add_argument("--with-email", action="store_true")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser("show", help="контакты одного учреждения")
    p.add_argument("query")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("obsidian", help="заметки по регионам + MOC в Органон")
    p.set_defaults(func=cmd_obsidian)

    p = sub.add_parser("promote", help="перенести выбранных ЛПР в соцкапитал")
    p.add_argument("--tier", choices=["A", "B"])
    p.add_argument("--kind", default="all", choices=["all", "ВО", "СПО"])
    p.add_argument("--region")
    p.add_argument("--inst")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-email", action="store_true", help="брать и тех, у кого нет email")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(func=cmd_promote)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
