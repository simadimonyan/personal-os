#!/usr/bin/env python3
"""
Социальный капитал — база личных контактов и связей.

Два лица одной базы:
  • SQLite ``social_capital.db``  — машинная сторона: запросы, граф, экспорт,
    потенциальная CRM/база клиентов.
  • Obsidian ``08 — Социальный капитал/Люди/*.md`` — человеческая сторона:
    карточка на человека с YAML-свойствами (их же читает таблица Bases).

Синхронизация двусторонняя и явная:
  sync   БД → карточки (перезаписывает frontmatter и авто-блок,
         свободный текст ниже маркера ``<!-- /capital:auto -->`` не трогает)
  pull   карточки → БД (правки, сделанные руками в Obsidian)
  sync --pull   сначала pull, потом sync

Категории (клиенты / друзья / партнёры / …) живут в таблице ``categories`` и
образуют дерево: у категории могут быть подкатегории (лиды → тёплые / холодные /
по рекомендации). Это и поле фильтрации в базе, и свойства ``категория`` +
``подкатегория`` в карточке, и раскраска узлов графа (подкатегория — оттенок
родительского цвета).

Источники контактов: ручной ``add``, Telegram, vCard (.vcf — Контакты
macOS/iOS, Google), CSV (Google Contacts, LinkedIn, выгрузка CRM, своя табличка)
и правка карточек прямо в Obsidian.

Команды:
  init                              — создать базу и категории по умолчанию
  add "Имя" [--category k] [...] [поле=значение ...]
  set <кого> поле=значение ...      — <кого> = slug, имя или его часть
  show <кого>                       — досье целиком
  list [--category k] [--tag t] [--q текст] [--due] [--json]
  rm <кого>                         — удалить контакт (и его карточку с --card)
  channel <кого> add|rm <вид> <значение> [--label L] [--primary]
  tag <кого> add|rm <вид> <значение...>   — вид: interest|skill|tag|project|org
  link <A> <B> [--kind знакомы] [--strength 1..5] [--note ...]
  unlink <A> <B> [--kind ...]
  log <кого> --kind встреча --summary "..." [--date 2026-08-04] [--next "..."]
  category list | add <key> "Имя" [--parent лиды] [--color #RRGGBB] [--desc ...]
           | rm <key> [--move-to другая]
  org list | set "Название" [--kind ...] [--industry ...] [--city ...] [--site ...]
  sync [--pull] [--prune] [--dry]   — БД → Obsidian (карточки + MOC + .base)
  pull [--dry]                      — Obsidian → БД
  dedupe [--dry] [--show 20]        — слить дубли (имя + общий канал/организация)
  graph [--thr 0.3] [--no-orgs]     — граф связей → graph/graph.html
  stats                             — сводка по базе
  export-csv [файл]                 — плоская выгрузка (база клиентов/связей)
  import-telegram [--limit 600] [--category inbox] [--contacts-only] [--dry]
  import-vcf <файл.vcf> [--category inbox] [--source ...] [--dry]
  import-csv <файл.csv> [--category inbox] [--source ...] [--dry]

Только stdlib + PyYAML (frontmatter). Граф переиспользует раскладку и HTML
семантического графа Obsidian — вид тот же, что у остальных графов Personal OS.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DB_PATH = HERE / "social_capital.db"
GRAPH_DIR = HERE / "graph"

VAULT = Path(os.environ.get(
    "VAULT_DIR",
    "/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/"
    "Knowledge base/Obsidian/Органон"))
SECTION = "08 — Социальный капитал"
CARDS_REL = f"{SECTION}/Люди"
CARDS_DIR = VAULT / CARDS_REL
MOC_PATH = VAULT / SECTION / f"MOC — {SECTION}.md"
BASE_PATH = VAULT / SECTION / "Контакты.base"
TEMPLATE_PATH = VAULT / SECTION / "_Шаблон — карточка контакта.md"

# сколько людей в разделе MOC печатается списком (дальше — счётчик и указатель
# на таблицу: массовые импорты лидов раздували оглавление до мегабайтов)
MOC_MAX = 200

AUTO_OPEN = "<!-- capital:auto -->"
AUTO_CLOSE = "<!-- /capital:auto -->"
TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"

# (key, имя, цвет, описание, сортировка, родитель)
# Дерево: у категории может быть подкатегория (лиды → тёплые/холодные и т.д.).
# Цвет подкатегории можно не задавать — берётся осветлённый цвет родителя.
DEFAULT_CATEGORIES = [
    ("family",    "Семья",              "#E57373", "Родные и близкие", 10, None),
    ("friends",   "Друзья",             "#FFB74D", "Личный круг", 20, None),
    ("partners",  "Партнёры",           "#81C784", "Совместные проекты и бизнес", 30, None),
    ("clients",   "Клиенты",            "#4FC3F7", "Платят или платили", 40, None),
    ("clients-active", "Активные",      None, "Работаем сейчас", 41, "clients"),
    ("clients-past",   "Бывшие",        None, "Работали раньше", 42, "clients"),
    ("leads",     "Лиды",               "#26A69A", "Потенциальные клиенты и сделки", 50, None),
    ("leads-warm", "Тёплые",            None, "Проявили интерес, диалог идёт", 51, "leads"),
    ("leads-cold", "Холодные",          None, "Ещё не касались или молчат", 52, "leads"),
    ("leads-ref",  "По рекомендации",   None, "Пришли через знакомых", 53, "leads"),
    ("colleagues", "Коллеги",           "#9575CD", "Работа, учёба, команда", 60, None),
    ("mentors",   "Менторы и эксперты", "#F06292", "У кого учусь и с кем советуюсь", 70, None),
    ("community", "Сообщество",         "#4DD0E1", "Нетворк, чаты, конференции", 80, None),
    ("service",   "Сервисные",          "#A1887F", "Врачи, юристы, мастера, подрядчики", 90, None),
    ("inbox",     "Неразобранное",      "#B0BEC5", "Импорт, ещё не размечено", 95, None),
    ("other",     "Другое",             "#78909C", "Всё остальное", 100, None),
]

# ── поля контакта: колонка → (свойство в карточке, тип) ───────────────────────
# тип: str | int | num | date | bool
FIELDS = [
    ("name",         "имя",                "str"),
    ("aka",          "псевдоним",          "str"),
    ("category",     "категория",          "str"),
    ("status",       "статус",             "str"),
    ("org",          "организация",        "str"),
    ("role",         "должность",          "str"),
    ("industry",     "сфера",              "str"),
    ("seniority",    "уровень",            "str"),
    ("country",      "страна",             "str"),
    ("city",         "город",              "str"),
    ("address",      "адрес",              "str"),
    ("timezone",     "часовой пояс",       "str"),
    ("birthday",     "день рождения",      "date"),
    ("languages",    "языки",              "str"),
    ("relation",     "кто он мне",         "str"),
    ("closeness",    "близость",           "int"),
    ("trust",        "доверие",            "int"),
    ("influence",    "влияние",            "int"),
    ("value_score",  "ценность",           "int"),
    ("how_met",      "как познакомились",  "str"),
    ("met_place",    "где познакомились",  "str"),
    ("met_date",     "дата знакомства",    "date"),
    ("intro_by",     "кто познакомил",     "str"),
    ("last_contact", "последний контакт",  "date"),
    ("next_touch",   "следующий контакт",  "date"),
    ("cadence_days", "периодичность",      "int"),
    ("can_help",     "чем полезен",        "str"),
    ("i_can_offer",  "чем могу помочь",    "str"),
    ("ask",          "о чём попросить",    "str"),
    ("deal_stage",   "стадия сделки",      "str"),
    ("deal_value",   "сумма",              "num"),
    ("currency",     "валюта",             "str"),
    ("notes",        "заметка",            "str"),
    ("source",       "источник",           "str"),
    ("archived",     "архив",              "bool"),
]
COL_BY_PROP = {prop: col for col, prop, _ in FIELDS}
PROP_BY_COL = {col: prop for col, prop, _ in FIELDS}
TYPE_BY_COL = {col: t for col, _, t in FIELDS}
# английские синонимы — чтобы в CLI можно было писать и category=, и категория=
ALIASES = {col: col for col, _, _ in FIELDS}
ALIASES.update(COL_BY_PROP)
ALIASES.update({
    "имя": "name", "ник": "aka", "компания": "org", "роль": "role",
    "индустрия": "industry", "company": "org", "position": "role",
    "заметки": "notes", "note": "notes", "телефоны": "phone",
})

# ── многозначные свойства ─────────────────────────────────────────────────────
CHANNEL_KINDS = {
    "email": "email", "почта": "email",
    "phone": "phone", "телефон": "phone",
    "telegram": "telegram", "тг": "telegram",
    "whatsapp": "whatsapp", "signal": "signal",
    "instagram": "instagram", "linkedin": "linkedin", "github": "github",
    "site": "site", "сайт": "site", "x": "x", "twitter": "x",
    "vk": "vk", "facebook": "facebook", "discord": "discord",
    "youtube": "youtube", "behance": "behance", "other": "other",
}
CHANNEL_PROPS = ["email", "phone", "telegram", "whatsapp", "instagram",
                 "linkedin", "github", "site", "x", "vk", "facebook",
                 "discord", "youtube", "behance", "signal", "other"]
CHANNEL_LABEL = {"phone": "телефон", "site": "сайт", "email": "email"}

ATTR_KINDS = {
    "interest": "interest", "интерес": "interest", "интересы": "interest",
    "skill": "skill", "навык": "skill", "навыки": "skill",
    "tag": "tag", "тег": "tag", "теги": "tag",
    "project": "project", "проект": "project", "проекты": "project",
    # ещё места работы: остаются после слияния дублей (человек числится
    # сразу в головной организации и в её филиалах)
    "org": "org", "организации": "org", "ещё организации": "org",
}
ATTR_PROPS = {"interest": "интересы", "skill": "навыки",
              "tag": "теги", "project": "проекты", "org": "организации"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
  key TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT,
  description TEXT, sort INTEGER DEFAULT 100, parent TEXT);

CREATE TABLE IF NOT EXISTS orgs(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT,
  industry TEXT, city TEXT, country TEXT, website TEXT, notes TEXT);

CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  aka TEXT,
  category TEXT NOT NULL DEFAULT 'other',
  status TEXT DEFAULT 'активный',
  org_id INTEGER REFERENCES orgs(id),
  role TEXT, industry TEXT, seniority TEXT,
  country TEXT, city TEXT, address TEXT, timezone TEXT,
  birthday TEXT, languages TEXT, relation TEXT,
  closeness INTEGER, trust INTEGER, influence INTEGER, value_score INTEGER,
  how_met TEXT, met_place TEXT, met_date TEXT, intro_by TEXT,
  last_contact TEXT, next_touch TEXT, cadence_days INTEGER,
  can_help TEXT, i_can_offer TEXT, ask TEXT,
  deal_stage TEXT, deal_value REAL, currency TEXT,
  notes TEXT, source TEXT, tg_id TEXT, avatar TEXT,
  note_path TEXT, archived INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, value TEXT NOT NULL, label TEXT,
  is_primary INTEGER DEFAULT 0,
  UNIQUE(contact_id, kind, value));

CREATE TABLE IF NOT EXISTS attrs(
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, value TEXT NOT NULL,
  UNIQUE(contact_id, kind, value));

CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY,
  a INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  b INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  kind TEXT DEFAULT 'знакомы', strength INTEGER DEFAULT 2,
  since TEXT, note TEXT,
  UNIQUE(a, b, kind));

CREATE TABLE IF NOT EXISTS interactions(
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  ts TEXT NOT NULL, kind TEXT, channel TEXT, summary TEXT,
  sentiment TEXT, next_step TEXT);

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE INDEX IF NOT EXISTS idx_contacts_cat ON contacts(category);
CREATE INDEX IF NOT EXISTS idx_channels_c ON channels(contact_id);
CREATE INDEX IF NOT EXISTS idx_attrs_c ON attrs(contact_id);
CREATE INDEX IF NOT EXISTS idx_inter_c ON interactions(contact_id, ts);
"""


# ── база ──────────────────────────────────────────────────────────────────────
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    # миграции старых баз (колонки добавляем после executescript — иначе
    # индексы по ним падают на схеме, где колонки ещё нет)
    if "parent" not in [c[1] for c in con.execute("PRAGMA table_info(categories)")]:
        con.execute("ALTER TABLE categories ADD COLUMN parent TEXT")
    cur = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if not cur:
        con.executemany(
            "INSERT INTO categories(key,name,color,description,sort,parent)"
            " VALUES(?,?,?,?,?,?)", DEFAULT_CATEGORIES)
    else:                       # добить подкатегории по умолчанию, если их нет
        con.executemany(
            "INSERT OR IGNORE INTO categories(key,name,color,description,sort,parent)"
            " VALUES(?,?,?,?,?,?)",
            [c for c in DEFAULT_CATEGORIES if c[5]])
    con.commit()
    return con


# ── дерево категорий ──────────────────────────────────────────────────────────
def cat_parent(con, key: str) -> str | None:
    row = con.execute("SELECT parent FROM categories WHERE key=?", (key,)).fetchone()
    return row["parent"] if row else None


def cat_root(con, key: str) -> str:
    """Корневая категория (сама категория, если родителя нет)."""
    seen = set()
    while key and key not in seen:
        seen.add(key)
        p = cat_parent(con, key)
        if not p:
            return key
        key = p
    return key


def cat_tree(con, key: str) -> list[str]:
    """Категория вместе со всеми потомками — для срезов «все лиды»."""
    out, queue = [key], [key]
    while queue:
        cur = queue.pop()
        for r in con.execute("SELECT key FROM categories WHERE parent=?", (cur,)):
            if r["key"] not in out:
                out.append(r["key"])
                queue.append(r["key"])
    return out


def cat_find(con, val: str) -> str | None:
    """Ключ категории по ключу или имени. Сравнение регистронезависимое делаем
    в Python: sqlite-функция lower() не понимает кириллицу."""
    val = (val or "").strip()
    if not val:
        return None
    if con.execute("SELECT 1 FROM categories WHERE key=?", (val,)).fetchone():
        return val
    v = val.casefold()
    for r in con.execute("SELECT key, name FROM categories"):
        if (r["name"] or "").casefold() == v or r["key"].casefold() == v:
            return r["key"]
    return None


def cat_resolve(con, val: str) -> str:
    key = cat_find(con, val)
    if key:
        return key
    sys.exit(f"нет категории «{val}». Список: capital.py category list")


def cat_ensure(con, name: str, parent_key: str | None = None) -> str:
    """Ключ категории по имени; если такой нет — заводит её (в том числе
    подкатегорией). Так новая категория, придуманная прямо в карточке Obsidian,
    появляется в базе сама, а не роняет `pull`."""
    name = (name or "").strip()
    if not name:
        return parent_key or "other"
    found = cat_find(con, name)
    if found:
        return found
    key = slugify(name)
    if parent_key:
        key = f"{parent_key}-{key}"
    base, i = key, 2
    while con.execute("SELECT 1 FROM categories WHERE key=?", (key,)).fetchone():
        key, i = f"{base}-{i}", i + 1
    sort = (con.execute("SELECT COALESCE(MAX(sort),100)+1 FROM categories"
                        " WHERE COALESCE(parent,'')=?",
                        (parent_key or "",)).fetchone()[0])
    con.execute("INSERT INTO categories(key,name,color,description,sort,parent)"
                " VALUES(?,?,?,?,?,?)",
                (key, name, None if parent_key else "#78909C",
                 "заведена из карточки Obsidian", sort, parent_key))
    return key


def _shade(color: str | None, depth: int) -> str:
    """Осветлённый оттенок родительского цвета для подкатегорий без своего."""
    color = color or "#78909C"
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return color
    f = min(0.45, 0.22 * max(1, depth))
    return "#%02X%02X%02X" % tuple(int(c + (255 - c) * f) for c in (r, g, b))


def cat_color(con, key: str) -> str:
    """Цвет категории; у подкатегории без своего — оттенок родителя."""
    depth, cur = 0, key
    while cur:
        row = con.execute("SELECT color, parent FROM categories WHERE key=?",
                          (cur,)).fetchone()
        if not row:
            break
        if row["color"]:
            return _shade(row["color"], depth) if depth else row["color"]
        cur, depth = row["parent"], depth + 1
    return "#78909C"


def cat_label(con, key: str) -> str:
    """«Лиды · Тёплые» для подкатегории, просто «Лиды» для корневой."""
    row = con.execute("SELECT name, parent FROM categories WHERE key=?",
                      (key,)).fetchone()
    if not row:
        return key
    if not row["parent"]:
        return row["name"]
    return f"{cat_label(con, row['parent'])} · {row['name']}"


def today() -> str:
    return dt.date.today().isoformat()


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def slugify(name: str) -> str:
    """Транслит + kebab-case: имя файла карточки остаётся кириллицей, а slug —
    стабильный машинный ключ (его же удобно набирать в CLI)."""
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    s = unicodedata.normalize("NFKC", (name or "").strip().lower())
    s = "".join(table.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "contact"


def unique_slug(con, base: str, exclude_id: int | None = None) -> str:
    slug, i = base, 2
    while True:
        row = con.execute("SELECT id FROM contacts WHERE slug=?", (slug,)).fetchone()
        if not row or (exclude_id and row["id"] == exclude_id):
            return slug
        slug, i = f"{base}-{i}", i + 1


def org_id(con, name: str | None) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    row = con.execute("SELECT id FROM orgs WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    return con.execute("INSERT INTO orgs(name) VALUES(?)", (name,)).lastrowid


def org_name(con, oid) -> str | None:
    if not oid:
        return None
    row = con.execute("SELECT name FROM orgs WHERE id=?", (oid,)).fetchone()
    return row["name"] if row else None


def resolve(con, ref: str) -> sqlite3.Row:
    """Находит контакт по slug, точному имени, части имени или id."""
    ref = (ref or "").strip()
    for sql, arg in (("SELECT * FROM contacts WHERE slug=?", ref),
                     ("SELECT * FROM contacts WHERE name=?", ref),
                     ("SELECT * FROM contacts WHERE id=?", ref if ref.isdigit() else -1)):
        row = con.execute(sql, (arg,)).fetchone()
        if row:
            return row
    rows = con.execute("SELECT * FROM contacts WHERE name LIKE ? OR aka LIKE ?",
                       (f"%{ref}%", f"%{ref}%")).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        sys.exit(f"не найден контакт: {ref}")
    sys.exit("неоднозначно — уточни: " + ", ".join(f"{r['name']} ({r['slug']})" for r in rows[:8]))


def cast(col: str, val):
    """Приводит значение к типу поля (пустое → None)."""
    t = TYPE_BY_COL.get(col, "str")
    if val is None:
        return None
    if isinstance(val, str) and not val.strip():
        return None
    try:
        if t == "int":
            return int(str(val).strip())
        if t == "num":
            return float(str(val).replace(" ", "").replace(",", "."))
        if t == "bool":
            return 1 if str(val).strip().lower() in ("1", "да", "true", "yes") else 0
        if t == "date":
            if isinstance(val, (dt.date, dt.datetime)):
                return val.isoformat()[:10]
            return str(val).strip()[:10]
    except ValueError:
        sys.exit(f"поле {col}: не разобрать значение «{val}»")
    return str(val).strip()


def parse_kv(pairs: list[str]) -> dict:
    """`категория=клиенты` / `city=Тбилиси` → {колонка: значение}."""
    out = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"ожидается поле=значение, получено: {p}")
        k, v = p.split("=", 1)
        col = ALIASES.get(k.strip().lower())
        if not col:
            sys.exit(f"неизвестное поле: {k}. Доступные: "
                     + ", ".join(sorted({*PROP_BY_COL.values(), *PROP_BY_COL.keys()})))
        out[col] = v
    return out


def apply_fields(con, cid: int, data: dict) -> None:
    """Пишет поля контакта; `org` разворачивается в orgs.id."""
    sets, args = [], []
    for col, val in data.items():
        if col == "org":
            sets.append("org_id=?")
            args.append(org_id(con, val))
        elif col == "category":
            sets.append("category=?")
            args.append(cat_resolve(con, str(val)))
        elif col in TYPE_BY_COL:
            sets.append(f"{col}=?")
            args.append(cast(col, val))
    if not sets:
        return
    sets.append("updated_at=?")
    args += [now(), cid]
    con.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args)


def split_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    else:
        items = re.split(r"[,;]", str(v))
    return [i.strip() for i in items if str(i).strip()]


def set_channels(con, cid: int, kind: str, values: list[str]) -> None:
    con.execute("DELETE FROM channels WHERE contact_id=? AND kind=?", (cid, kind))
    for i, v in enumerate(values):
        con.execute("INSERT OR IGNORE INTO channels(contact_id,kind,value,is_primary)"
                    " VALUES(?,?,?,?)", (cid, kind, v, 1 if i == 0 else 0))


def set_attrs(con, cid: int, kind: str, values: list[str]) -> None:
    con.execute("DELETE FROM attrs WHERE contact_id=? AND kind=?", (cid, kind))
    for v in values:
        con.execute("INSERT OR IGNORE INTO attrs(contact_id,kind,value) VALUES(?,?,?)",
                    (cid, kind, v))


def channels_of(con, cid: int) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in con.execute("SELECT kind,value FROM channels WHERE contact_id=?"
                         " ORDER BY is_primary DESC, id", (cid,)):
        out.setdefault(r["kind"], []).append(r["value"])
    return out


def attrs_of(con, cid: int) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in con.execute("SELECT kind,value FROM attrs WHERE contact_id=? ORDER BY value",
                         (cid,)):
        out.setdefault(r["kind"], []).append(r["value"])
    return out


def links_of(con, cid: int) -> list[tuple[str, str, str, int]]:
    """[(имя, slug, вид связи, сила)] — в обе стороны."""
    q = """SELECT c.name, c.slug, r.kind, r.strength FROM relations r
             JOIN contacts c ON c.id = CASE WHEN r.a=? THEN r.b ELSE r.a END
            WHERE r.a=? OR r.b=? ORDER BY r.strength DESC, c.name"""
    return [(r["name"], r["slug"], r["kind"] or "знакомы", r["strength"] or 2)
            for r in con.execute(q, (cid, cid, cid))]


def cat_map(con) -> dict[str, sqlite3.Row]:
    return {r["key"]: r for r in con.execute("SELECT * FROM categories ORDER BY sort")}


# ── команды: CRUD ─────────────────────────────────────────────────────────────
def cmd_init(a):
    con = db()
    n = con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    for p in (VAULT / SECTION, CARDS_DIR):
        p.mkdir(parents=True, exist_ok=True)
    print(f"init: {DB_PATH} · категорий {n} · папка «{SECTION}» создана")


def cmd_add(a):
    con = db()
    data = parse_kv(a.field)
    data["name"] = a.name
    for key in ("category", "status", "org", "role", "city", "country",
                "notes", "source", "relation"):
        v = getattr(a, key, None)
        if v:
            data[key] = v
    cat = cat_resolve(con, data.get("category", "other"))   # ключ или имя
    data["category"] = cat
    slug = unique_slug(con, a.slug or slugify(a.name))
    cid = con.execute(
        "INSERT INTO contacts(slug,name,category,created_at,updated_at,source)"
        " VALUES(?,?,?,?,?,?)",
        (slug, a.name, cat, now(), now(), data.get("source") or "manual")).lastrowid
    apply_fields(con, cid, data)
    for prop, flag in (("email", a.email), ("phone", a.phone), ("telegram", a.tg),
                       ("site", a.site), ("linkedin", a.linkedin),
                       ("github", a.github), ("instagram", a.instagram)):
        if flag:
            set_channels(con, cid, prop, split_list(flag))
    for kind, flag in (("interest", a.interests), ("skill", a.skills),
                       ("tag", a.tags), ("project", a.projects)):
        if flag:
            set_attrs(con, cid, kind, split_list(flag))
    con.commit()
    print(f"+ {a.name} ({slug}) · категория {cat}")


def cmd_set(a):
    con = db()
    row = resolve(con, a.ref)
    data = parse_kv(a.field)
    apply_fields(con, row["id"], data)
    con.commit()
    print(f"~ {row['name']}: " + ", ".join(f"{k}={v}" for k, v in data.items()))


def cmd_rm(a):
    con = db()
    row = resolve(con, a.ref)
    con.execute("DELETE FROM contacts WHERE id=?", (row["id"],))
    # организация без людей — мусор в графе (узел-хаб без связей)
    con.execute("DELETE FROM orgs WHERE id NOT IN"
                " (SELECT org_id FROM contacts WHERE org_id IS NOT NULL)")
    con.commit()
    if a.card and row["note_path"]:
        p = VAULT / row["note_path"]
        if p.exists():
            p.unlink()
            print(f"  карточка удалена: {row['note_path']}")
    print(f"- {row['name']} ({row['slug']})")


def cmd_channel(a):
    con = db()
    row = resolve(con, a.ref)
    kind = CHANNEL_KINDS.get(a.kind.lower(), a.kind.lower())
    if a.action == "add":
        con.execute("INSERT OR IGNORE INTO channels(contact_id,kind,value,label,is_primary)"
                    " VALUES(?,?,?,?,?)",
                    (row["id"], kind, a.value, a.label, 1 if a.primary else 0))
        if a.primary:
            con.execute("UPDATE channels SET is_primary=0 WHERE contact_id=? AND kind=?"
                        " AND value<>?", (row["id"], kind, a.value))
    else:
        con.execute("DELETE FROM channels WHERE contact_id=? AND kind=? AND value=?",
                    (row["id"], kind, a.value))
    con.commit()
    print(f"{'+' if a.action == 'add' else '-'} {row['name']} · {kind}: {a.value}")


def cmd_tag(a):
    con = db()
    row = resolve(con, a.ref)
    kind = ATTR_KINDS.get(a.kind.lower(), a.kind.lower())
    for v in split_list(",".join(a.value)):
        if a.action == "add":
            con.execute("INSERT OR IGNORE INTO attrs(contact_id,kind,value) VALUES(?,?,?)",
                        (row["id"], kind, v))
        else:
            con.execute("DELETE FROM attrs WHERE contact_id=? AND kind=? AND value=?",
                        (row["id"], kind, v))
    con.commit()
    print(f"{row['name']} · {kind}: {', '.join(a.value)}")


def cmd_link(a):
    con = db()
    x, y = resolve(con, a.a), resolve(con, a.b)
    lo, hi = sorted([x["id"], y["id"]])
    con.execute("INSERT INTO relations(a,b,kind,strength,since,note) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(a,b,kind) DO UPDATE SET strength=excluded.strength,"
                " note=COALESCE(excluded.note, relations.note)",
                (lo, hi, a.kind, a.strength, a.since, a.note))
    con.commit()
    print(f"⇄ {x['name']} — {y['name']} ({a.kind}, сила {a.strength})")


def cmd_unlink(a):
    con = db()
    x, y = resolve(con, a.a), resolve(con, a.b)
    lo, hi = sorted([x["id"], y["id"]])
    if a.kind:
        con.execute("DELETE FROM relations WHERE a=? AND b=? AND kind=?", (lo, hi, a.kind))
    else:
        con.execute("DELETE FROM relations WHERE a=? AND b=?", (lo, hi))
    con.commit()
    print(f"⇄̸ {x['name']} — {y['name']}")


def cmd_log(a):
    con = db()
    row = resolve(con, a.ref)
    ts = a.date or today()
    con.execute("INSERT INTO interactions(contact_id,ts,kind,channel,summary,sentiment,next_step)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["id"], ts, a.kind, a.channel, a.summary, a.sentiment, a.next))
    con.execute("UPDATE contacts SET last_contact=?, updated_at=? WHERE id=?",
                (ts, now(), row["id"]))
    if row["cadence_days"]:
        nxt = (dt.date.fromisoformat(ts) + dt.timedelta(days=row["cadence_days"])).isoformat()
        con.execute("UPDATE contacts SET next_touch=? WHERE id=?", (nxt, row["id"]))
    con.commit()
    print(f"✓ {row['name']} · {ts} · {a.kind or 'контакт'}: {a.summary or ''}")


def cmd_category(a):
    con = db()
    if a.action == "list":
        def walk(parent, depth):
            for r in con.execute(
                    "SELECT * FROM categories WHERE COALESCE(parent,'')=? ORDER BY sort",
                    (parent or "",)):
                n = con.execute(
                    "SELECT COUNT(*) FROM contacts WHERE category IN (%s)"
                    % ",".join("?" * len(cat_tree(con, r["key"]))),
                    tuple(cat_tree(con, r["key"]))).fetchone()[0]
                pad = "  " * depth
                print(f"  {pad}{r['key']:<{max(4, 18 - 2 * depth)}} {r['name']:<20}"
                      f" {cat_color(con, r['key']):<9} {n:>4} — {r['description'] or ''}")
                walk(r["key"], depth + 1)
        walk(None, 0)
        return
    if not a.key:
        sys.exit("нужен ключ категории: capital.py category add <key> \"Имя\"")
    if a.action == "rm":
        key = cat_resolve(con, a.key)
        dest = cat_resolve(con, a.move_to) if a.move_to else (cat_parent(con, key) or "other")
        moved = con.execute("UPDATE contacts SET category=? WHERE category IN (%s)"
                            % ",".join("?" * len(cat_tree(con, key))),
                            (dest, *cat_tree(con, key))).rowcount
        con.execute("UPDATE categories SET parent=? WHERE parent=?",
                    (cat_parent(con, key), key))
        con.execute("DELETE FROM categories WHERE key=?", (key,))
        con.commit()
        print(f"- категория {key} удалена · {moved} контакт(ов) → {dest}")
        return
    parent = cat_resolve(con, a.parent) if a.parent else None
    con.execute("INSERT INTO categories(key,name,color,description,sort,parent)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET name=excluded.name,"
                " color=COALESCE(excluded.color, categories.color),"
                " description=excluded.description, sort=excluded.sort,"
                " parent=excluded.parent",
                (a.key, a.name or a.key, a.color or (None if parent else "#78909C"),
                 a.desc, a.sort, parent))
    con.commit()
    print(f"категория {a.key} — {cat_label(con, a.key)} · цвет {cat_color(con, a.key)}")


def cmd_org(a):
    con = db()
    if a.action == "list":
        for r in con.execute(
                "SELECT o.*, (SELECT COUNT(*) FROM contacts c WHERE c.org_id=o.id) n"
                " FROM orgs o ORDER BY n DESC, o.name"):
            print(f"  {r['name']:<32} {r['n']:>3} чел · {r['industry'] or '—'} · {r['city'] or '—'}")
        return
    oid = org_id(con, a.name)
    for col, val in (("kind", a.kind), ("industry", a.industry), ("city", a.city),
                     ("country", a.country), ("website", a.site), ("notes", a.notes)):
        if val:
            con.execute(f"UPDATE orgs SET {col}=? WHERE id=?", (val, oid))
    con.commit()
    print(f"организация: {a.name}")


# ── команды: чтение ───────────────────────────────────────────────────────────
def contact_dict(con, row) -> dict:
    ch, at = channels_of(con, row["id"]), attrs_of(con, row["id"])
    d = {c: row[c] for c in row.keys()}
    d["org"] = org_name(con, row["org_id"])
    d["channels"] = ch
    d["attrs"] = at
    d["links"] = [{"name": n, "slug": s, "kind": k, "strength": w}
                  for n, s, k, w in links_of(con, row["id"])]
    d["interactions"] = [dict(r) for r in con.execute(
        "SELECT ts,kind,channel,summary,sentiment,next_step FROM interactions"
        " WHERE contact_id=? ORDER BY ts DESC LIMIT 20", (row["id"],))]
    return d


def cmd_show(a):
    con = db()
    d = contact_dict(con, resolve(con, a.ref))
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    cats = cat_map(con)
    print(f"\n{d['name']}  ({d['slug']})")
    print(f"  категория : {cat_label(con, d['category'])}"
          f" · статус {d['status'] or '—'}")
    if d["org"] or d["role"]:
        print(f"  работа    : {d['role'] or '—'} @ {d['org'] or '—'}"
              f" · {d['industry'] or '—'}")
    loc = " · ".join(x for x in (d["city"], d["country"], d["address"]) if x)
    if loc:
        print(f"  где       : {loc}")
    for k, vs in d["channels"].items():
        print(f"  {CHANNEL_LABEL.get(k, k):<10}: {', '.join(vs)}")
    for k, vs in d["attrs"].items():
        print(f"  {ATTR_PROPS.get(k, k):<10}: {', '.join(vs)}")
    scores = [f"{lbl} {d[c]}" for c, lbl in
              (("closeness", "близость"), ("trust", "доверие"),
               ("influence", "влияние"), ("value_score", "ценность")) if d[c]]
    if scores:
        print("  оценки    : " + " · ".join(scores))
    if d["can_help"] or d["i_can_offer"]:
        print(f"  чем полезен: {d['can_help'] or '—'}")
        print(f"  что дам я  : {d['i_can_offer'] or '—'}")
    if d["last_contact"] or d["next_touch"]:
        print(f"  контакт   : последний {d['last_contact'] or '—'}"
              f" · следующий {d['next_touch'] or '—'}")
    if d["notes"]:
        print(f"  заметка   : {d['notes']}")
    if d["links"]:
        print("  связи     : " + ", ".join(f"{l['name']} ({l['kind']})" for l in d["links"]))
    if d["interactions"]:
        print("  история   :")
        for i in d["interactions"][:8]:
            print(f"    {i['ts']} · {i['kind'] or '—'} · {i['summary'] or ''}")
    print()


def cmd_list(a):
    con = db()
    q = ("SELECT c.*, o.name org FROM contacts c LEFT JOIN orgs o ON o.id=c.org_id"
         " WHERE 1=1")
    args: list = []
    if a.category:                        # срез включает подкатегории
        keys = cat_tree(con, cat_resolve(con, a.category))
        q += " AND c.category IN (%s)" % ",".join("?" * len(keys))
        args += keys
    if a.q:
        q += (" AND (c.name LIKE ? OR c.aka LIKE ? OR c.notes LIKE ? OR o.name LIKE ?"
              " OR c.role LIKE ? OR c.city LIKE ?)")
        args += [f"%{a.q}%"] * 6
    if a.tag:
        q += (" AND c.id IN (SELECT contact_id FROM attrs WHERE value LIKE ?)")
        args.append(f"%{a.tag}%")
    if a.due:
        q += " AND c.next_touch IS NOT NULL AND c.next_touch <= ?"
        args.append(today())
    if not a.archived:
        q += " AND COALESCE(c.archived,0)=0"
    q += " ORDER BY c.category, c.name LIMIT ?"
    args.append(a.limit)
    rows = con.execute(q, args).fetchall()
    if a.json:
        print(json.dumps([contact_dict(con, r) for r in rows], ensure_ascii=False, indent=2))
        return
    cats = cat_map(con)
    cur = None
    for r in rows:
        if r["category"] != cur:
            cur = r["category"]
            print(f"\n▸ {cat_label(con, cur)}")
        ch = channels_of(con, r["id"])
        contact = (ch.get("telegram") or ch.get("email") or ch.get("phone") or ["—"])[0]
        print(f"  {r['name']:<26} {(r['role'] or '—')[:20]:<21}"
              f" {(r['org'] or '—')[:18]:<19} {contact}")
    print(f"\n{len(rows)} контакт(ов)")


def cmd_stats(a):
    con = db()
    total = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    print(f"контактов: {total}")
    counts = {r[0]: r[1] for r in con.execute(
        "SELECT category, COUNT(*) FROM contacts GROUP BY category")}

    def walk(parent, depth):
        for r in con.execute("SELECT * FROM categories WHERE COALESCE(parent,'')=?"
                             " ORDER BY sort", (parent or "",)):
            n = sum(counts.get(k, 0) for k in cat_tree(con, r["key"]))
            if n:
                print(f"  {'  ' * depth}{r['name']:<{max(6, 22 - 2 * depth)}} {n}")
            walk(r["key"], depth + 1)
    walk(None, 0)
    for tbl, lbl in (("orgs", "организаций"), ("channels", "каналов связи"),
                     ("attrs", "интересов/навыков/тегов"), ("relations", "связей"),
                     ("interactions", "взаимодействий")):
        print(f"{lbl}: {con.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]}")
    due = con.execute("SELECT COUNT(*) FROM contacts WHERE next_touch<=?",
                      (today(),)).fetchone()[0]
    print(f"пора написать: {due}")


def cmd_export_csv(a):
    con = db()
    path = Path(a.path) if a.path else HERE / "contacts.csv"
    cols = [c for c, _, _ in FIELDS if c != "org"] + ["slug", "org"]
    extra = CHANNEL_PROPS + list(ATTR_PROPS)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols + extra + ["связи"])
        for row in con.execute("SELECT * FROM contacts ORDER BY category, name"):
            d = contact_dict(con, row)
            line = [d.get(c) for c in cols]
            line[cols.index("org")] = d["org"]
            line += [", ".join(d["channels"].get(k, [])) for k in CHANNEL_PROPS]
            line += [", ".join(d["attrs"].get(k, [])) for k in ATTR_PROPS]
            line.append(", ".join(f"{l['name']} ({l['kind']})" for l in d["links"]))
            w.writerow(line)
    print(f"csv: {path}")


# ── Obsidian: карточки ────────────────────────────────────────────────────────
FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def card_name(name: str) -> str:
    """Имя файла карточки: имя человека без символов, которые ломают путь или
    wiki-ссылку (ники из Telegram бывают со слэшами и скобками)."""
    s = FORBIDDEN_RE.sub("-", name or "")
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s or "контакт"


def clip(s: str, limit: int = 110) -> str:
    """Обрезает имя файла: в кириллице символ — два байта, а лимит файловой
    системы — 255 байт на имя."""
    s = s.strip()
    return s if len(s) <= limit else s[:limit].rstrip(" .-") + "…"


# slug → имя файла карточки. Тёзки (а их в базе сотни: одно и то же имя
# приезжает из разных организаций) получают уточнение в скобках, иначе карточки
# затирали бы друг друга — именно из-за этого в Obsidian оказывалось меньше
# людей, чем в базе.
_CARD_NAMES: dict[str, str] | None = None


def card_names(con, refresh: bool = False) -> dict[str, str]:
    global _CARD_NAMES
    if _CARD_NAMES is not None and not refresh:
        return _CARD_NAMES
    rows = con.execute("SELECT id, slug, name, org_id, city FROM contacts"
                       " ORDER BY id").fetchall()
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(card_name(r["name"]).lower(), []).append(r)
    out: dict[str, str] = {}
    taken: set[str] = set()
    for group in buckets.values():          # сначала одиночки занимают своё имя
        if len(group) == 1:
            nm = clip(card_name(group[0]["name"]))
            out[group[0]["slug"]] = nm
            taken.add(nm.lower())
    for group in buckets.values():
        if len(group) == 1:
            continue
        for r in group:
            base = card_name(r["name"])
            hints = [x for x in (org_name(con, r["org_id"]), r["city"]) if x]
            cands = [clip(f"{base} ({card_name(h)})") for h in hints]
            cands.append(clip(f"{base} ({r['slug']})"))
            for cand in cands:
                if cand.lower() not in taken:
                    out[r["slug"]], _ = cand, taken.add(cand.lower())
                    break
            else:                            # совсем упрямый случай — по id
                nm = clip(f"{base} ({r['id']})")
                out[r["slug"]], _ = nm, taken.add(nm.lower())
    _CARD_NAMES = out
    return out


def wikilink(name: str) -> str:
    safe = card_name(name)
    return f"[[{safe}]]" if safe == name else f"[[{safe}|{name}]]"


def wikilink_of(con, slug: str, name: str) -> str:
    """Ссылка на карточку с учётом уточнения у тёзок."""
    safe = card_names(con).get(slug) or card_name(name)
    return f"[[{safe}]]" if safe == name else f"[[{safe}|{name}]]"


def card_path(con, row) -> Path:
    nm = card_names(con).get(row["slug"]) or card_name(row["name"])
    return CARDS_DIR / f"{nm}.md"


def build_frontmatter(con, row) -> dict:
    d = contact_dict(con, row)
    cats = cat_map(con)
    fm: dict = {"type": "contact"}
    tags = ["контакт", "социальный-капитал",
            "категория/" + (cats.get(d["category"], {"name": d["category"]})["name"]
                            .lower().replace(" ", "-"))]
    root = cat_root(con, d["category"])
    if root != d["category"]:              # тег и по корню, и по подкатегории
        tags.insert(2, "категория/" + cats[root]["name"].lower().replace(" ", "-"))
    for t in d["attrs"].get("tag", []):
        tags.append(t.strip().replace(" ", "-"))
    fm["tags"] = tags
    for col, prop, _ in FIELDS:
        if col in ("name", "notes", "archived"):
            continue
        if col == "org":
            v = d["org"]
        elif col == "category":       # в карточке — корневая категория именем,
            v = cats[root]["name"] if root in cats else d["category"]  # под — ниже
        else:
            v = d.get(col)
        if v not in (None, "", 0):
            # даты — объектами: Obsidian опознаёт их как свойство-дату, а не текст
            if TYPE_BY_COL.get(col) == "date":
                try:
                    v = dt.date.fromisoformat(str(v)[:10])
                except ValueError:
                    pass
            fm[prop] = v
    fm["имя"] = d["name"]
    if root != d["category"]:     # подкатегория — отдельным свойством для таблицы
        fm["подкатегория"] = cats.get(d["category"],
                                      {"name": d["category"]})["name"]
    for k in CHANNEL_PROPS:
        vs = d["channels"].get(k)
        if vs:
            fm[CHANNEL_LABEL.get(k, k)] = vs if len(vs) > 1 else vs[0]
    for kind, prop in ATTR_PROPS.items():
        vs = d["attrs"].get(kind)
        if vs:
            fm[prop] = vs
    if d["links"]:
        fm["связи"] = [wikilink_of(con, l["slug"], l["name"]) for l in d["links"]]
    if d["notes"]:
        fm["заметка"] = d["notes"]
    if d["archived"]:
        fm["архив"] = True
    fm["slug"] = d["slug"]
    fm["обновлено"] = today()
    # порядок: сначала опознавательные, потом всё прочее
    head = ["type", "tags", "имя", "категория", "подкатегория", "статус",
            "организация", "должность"]
    ordered = {k: fm[k] for k in head if k in fm}
    ordered.update({k: v for k, v in fm.items() if k not in ordered})
    return ordered


def build_auto_body(con, row) -> str:
    d = contact_dict(con, row)
    cats = cat_map(con)
    L: list[str] = [f"# {d['name']}", ""]
    sub = " · ".join(x for x in (d["role"], d["org"], d["city"]) if x)
    if sub:
        L += [f"> {sub}", ""]
    L += [AUTO_OPEN, ""]

    def rows(pairs):
        out = [f"| {k} | {v} |" for k, v in pairs if v not in (None, "", 0)]
        return (["| Поле | Значение |", "|---|---|"] + out + [""]) if out else []

    L += ["## Досье", ""]
    L += rows([
        ("Категория", cat_label(con, d["category"])),
        ("Статус", d["status"]),
        ("Кто он мне", d["relation"]),
        ("Организация", d["org"]),
        ("Должность", d["role"]),
        ("Сфера", d["industry"]),
        ("Где", " · ".join(x for x in (d["city"], d["country"]) if x)),
        ("Адрес", d["address"]),
        ("День рождения", d["birthday"]),
        ("Языки", d["languages"]),
        ("Близость / доверие / влияние / ценность",
         " · ".join(str(d[c] or "—") for c in
                    ("closeness", "trust", "influence", "value_score"))
         if any(d[c] for c in ("closeness", "trust", "influence", "value_score"))
         else None),
        ("Как познакомились", d["how_met"]),
        ("Где познакомились", d["met_place"]),
        ("Дата знакомства", d["met_date"]),
        ("Кто познакомил", d["intro_by"]),
        ("Последний контакт", d["last_contact"]),
        ("Следующий контакт", d["next_touch"]),
        ("Периодичность (дней)", d["cadence_days"]),
        ("Стадия сделки", d["deal_stage"]),
        ("Сумма", f"{d['deal_value']} {d['currency'] or ''}".strip()
         if d["deal_value"] else None),
    ])
    if d["channels"]:
        L += ["## Контакты", ""]
        for k, vs in d["channels"].items():
            L.append(f"- **{CHANNEL_LABEL.get(k, k)}** — " + ", ".join(vs))
        L.append("")
    if any(d["attrs"].values()):
        L += ["## Интересы и навыки", ""]
        for kind, prop in ATTR_PROPS.items():
            if d["attrs"].get(kind):
                L.append(f"- **{prop}** — " + ", ".join(d["attrs"][kind]))
        L.append("")
    if d["can_help"] or d["i_can_offer"] or d["ask"]:
        L += ["## Обмен ценностью", ""]
        if d["can_help"]:
            L.append(f"- **Чем полезен мне** — {d['can_help']}")
        if d["i_can_offer"]:
            L.append(f"- **Чем полезен я** — {d['i_can_offer']}")
        if d["ask"]:
            L.append(f"- **О чём попросить** — {d['ask']}")
        L.append("")
    if d["links"]:
        L += ["## Связи", ""]
        for l in d["links"]:
            L.append(f"- {wikilink_of(con, l['slug'], l['name'])}"
                     f" — {l['kind']} (сила {l['strength']})")
        L.append("")
    if d["interactions"]:
        L += ["## Взаимодействия", ""]
        for i in d["interactions"]:
            tail = f" → {i['next_step']}" if i["next_step"] else ""
            L.append(f"- **{i['ts']}** · {i['kind'] or 'контакт'} — {i['summary'] or ''}{tail}")
        L.append("")
    if d["notes"]:
        L += ["## Заметка", "", d["notes"], ""]
    L += [AUTO_CLOSE, ""]
    return "\n".join(L)


def dump_frontmatter(fm: dict) -> str:
    body = yaml.dump(fm, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=1000)
    return f"---\n{body}---\n"


def split_card(text: str) -> tuple[dict, str, str]:
    """(frontmatter, авто-часть, свободный хвост после закрывающего маркера)."""
    fm, rest = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                fm = {}
            rest = text[end + 4:].lstrip("\n")
    idx = rest.find(AUTO_CLOSE)
    if idx == -1:
        return fm, "", rest
    return fm, rest[:idx], rest[idx + len(AUTO_CLOSE):].lstrip("\n")


# ── слияние дублей ────────────────────────────────────────────────────────────
# Массовые импорты плодят одного и того же человека по разу на организацию:
# сайты филиалов перечисляют руководство головного вуза, и в базе появляется
# 28 «Прокофьевых» с одной и той же почтой. Слияние идёт по совпадению имени
# И хотя бы одного канала связи — фамилия-тёзка без общей почты не сливается.
BRANCH_WORDS = ("филиал", "колледж", "техникум", "институт ", "представительств")


def norm_person(name: str) -> str:
    s = unicodedata.normalize("NFKC", (name or "")).lower().replace("ё", "е")
    return re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).strip()


def dupe_groups(con) -> list[list[sqlite3.Row]]:
    rows = con.execute("SELECT * FROM contacts ORDER BY id").fetchall()
    chans: dict[int, set[str]] = {}
    for r in con.execute("SELECT contact_id, kind, value FROM channels"):
        chans.setdefault(r["contact_id"], set()).add(
            f"{r['kind']}:{(r['value'] or '').strip().lower()}")
    byname: dict[str, list] = {}
    for r in rows:
        byname.setdefault(norm_person(r["name"]), []).append(r)
    out = []
    for name, group in byname.items():
        if len(group) < 2 or not name:
            continue
        buckets: list[tuple[set[str], list]] = []   # по пересечению признаков
        for r in group:
            # признак совпадения — общий канал связи ИЛИ та же организация
            # (полный тёзка с отчеством в одной организации — это один человек;
            # тёзка в разных организациях остаётся отдельной записью)
            mine = set(chans.get(r["id"], ()))
            if r["org_id"]:
                mine.add(f"org:{r['org_id']}")
            if not mine:                    # ни канала, ни организации — не трогаем
                continue
            hit = next((b for b in buckets if b[0] & mine), None)
            if hit:
                hit[0].update(mine)
                hit[1].append(r)
            else:
                buckets.append((set(mine), [r]))
        out += [b[1] for b in buckets if len(b[1]) > 1]
    return out


def pick_keeper(con, group):
    """Ведущей делаем запись при головной организации: у филиалов и колледжей
    в названии есть характерные слова, а человек числится в головной."""
    def score(r):
        org = (org_name(con, r["org_id"]) or "").lower()
        branch = any(w in org for w in BRANCH_WORDS)
        return (0 if branch else 1, -r["id"])
    return max(group, key=score)


def merge_group(con, group) -> tuple[int, list[str]]:
    keeper = pick_keeper(con, group)
    losers = [r for r in group if r["id"] != keeper["id"]]
    kid, cards = keeper["id"], []
    cols = [c for c in keeper.keys()
            if c not in ("id", "slug", "name", "note_path", "created_at")]
    patch = {}
    for col in cols:
        if keeper[col] in (None, "", 0):
            val = next((r[col] for r in losers if r[col] not in (None, "", 0)), None)
            if val is not None:
                patch[col] = val
    keep_org = org_name(con, keeper["org_id"])
    for r in losers:
        con.execute("INSERT OR IGNORE INTO channels(contact_id,kind,value,label,"
                    "is_primary) SELECT ?,kind,value,label,0 FROM channels"
                    " WHERE contact_id=?", (kid, r["id"]))
        con.execute("INSERT OR IGNORE INTO attrs(contact_id,kind,value)"
                    " SELECT ?,kind,value FROM attrs WHERE contact_id=?",
                    (kid, r["id"]))
        con.execute("UPDATE interactions SET contact_id=? WHERE contact_id=?",
                    (kid, r["id"]))
        for rel in con.execute("SELECT * FROM relations WHERE a=? OR b=?",
                               (r["id"], r["id"])).fetchall():
            other = rel["b"] if rel["a"] == r["id"] else rel["a"]
            if other == kid or other in {x["id"] for x in losers}:
                continue
            con.execute("INSERT OR IGNORE INTO relations(a,b,kind,strength,since,"
                        "note) VALUES(?,?,?,?,?,?)",
                        (kid, other, rel["kind"], rel["strength"], rel["since"],
                         rel["note"]))
        org = org_name(con, r["org_id"])
        if org and org != keep_org:      # где ещё числится — сохраняем текстом
            con.execute("INSERT OR IGNORE INTO attrs(contact_id,kind,value)"
                        " VALUES(?,'org',?)", (kid, org))
        if r["note_path"]:
            cards.append(r["note_path"])
    if patch:
        patch["updated_at"] = now()
        con.execute(f"UPDATE contacts SET {','.join(k + '=?' for k in patch)}"
                    " WHERE id=?", (*patch.values(), kid))
    con.execute(f"DELETE FROM contacts WHERE id IN"
                f" ({','.join('?' * len(losers))})", [r["id"] for r in losers])
    return kid, cards


def cmd_dedupe(a):
    con = db()
    groups = dupe_groups(con)
    total = sum(len(g) - 1 for g in groups)
    if a.dry:
        for g in groups[:a.show]:
            keeper = pick_keeper(con, g)
            print(f"\n  {keeper['name']} — {len(g)} записей → 1")
            for r in g:
                mark = "→" if r["id"] == keeper["id"] else " "
                print(f"    {mark} #{r['id']:5d} {(org_name(con, r['org_id']) or '—')[:60]}")
        print(f"\ndedupe [dry]: групп {len(groups)} · схлопнется {total} записей"
              f" · останется {count(con)- total} контактов")
        return
    for g in groups:
        merge_group(con, g)
    con.commit()
    print(f"dedupe: слито {total} записей в {len(groups)} человек"
          f" · контактов осталось {count(con)}"
          f"\n  дальше: capital.py sync --prune (карточки в Obsidian)")


def count(con) -> int:
    return con.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]


def cmd_sync(a):
    con = db()
    if a.pull:
        cmd_pull(argparse.Namespace(dry=a.dry))
        con = db()
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    card_names(con, refresh=True)
    written, renamed = 0, 0
    rows = con.execute("SELECT * FROM contacts ORDER BY name").fetchall()
    paths = {r["id"]: card_path(con, r) for r in rows}
    wanted = set(paths.values())
    for row in rows:
        path = paths[row["id"]]
        old = VAULT / row["note_path"] if row["note_path"] else None
        tail = ""
        # старый файл забираем только если он больше никому не принадлежит:
        # раньше тёзки делили одну карточку, и слепой unlink стёр бы соседа
        if old and old.exists() and old != path and old not in wanted:
            tail = split_card(old.read_text(encoding="utf-8"))[2]
            if not a.dry:
                old.unlink()
            renamed += 1
        elif path.exists():
            tail = split_card(path.read_text(encoding="utf-8"))[2]
        if not tail.strip():
            tail = ("## Мои заметки\n\n"
                    "_Свободная часть карточки — синхронизация её не трогает._\n")
        text = (dump_frontmatter(build_frontmatter(con, row)) + "\n"
                + build_auto_body(con, row) + "\n" + tail)
        if a.dry:
            print(f"  [dry] {path.relative_to(VAULT)}")
        else:
            path.write_text(text, encoding="utf-8")
            con.execute("UPDATE contacts SET note_path=? WHERE id=?",
                        (str(path.relative_to(VAULT)), row["id"]))
        written += 1
    dropped = prune_cards(con, wanted, a.dry) if a.prune else 0
    if not a.dry:
        con.commit()
        write_moc(con)
        write_base()
        write_template(con)
    print(f"sync: карточек {written} (переименовано {renamed}"
          + (f", удалено лишних {dropped}" if a.prune else "")
          + f") → {CARDS_REL}")


def prune_cards(con, wanted: set[Path], dry: bool = False) -> int:
    """Убирает карточки-сироты: файлы с `type: contact`, за которыми в базе
    больше нет контакта (удалённые, слитые дубли, остатки старых имён).
    Чужие заметки без такого frontmatter не трогает."""
    dropped = 0
    for path in sorted(CARDS_DIR.glob("*.md")):
        if path in wanted or path.name.startswith("_"):
            continue
        fm = split_card(path.read_text(encoding="utf-8"))[0]
        if str(fm.get("type") or "") != "contact":
            continue
        if dry:
            print(f"  [dry] удалить сироту: {path.name}")
        else:
            path.unlink()
        dropped += 1
    return dropped


def cmd_pull(a):
    """Читает карточки и переносит правки в базу (по slug, иначе по имени файла)."""
    con = db()
    if not CARDS_DIR.exists():
        print("pull: папки карточек ещё нет")
        return
    seen, created, updated = 0, 0, 0
    for path in sorted(CARDS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        fm = split_card(path.read_text(encoding="utf-8"))[0]
        if not fm:
            continue
        seen += 1
        name = str(fm.get("имя") or path.stem)
        slug = str(fm.get("slug") or "").strip()
        row = None
        if slug:
            row = con.execute("SELECT * FROM contacts WHERE slug=?", (slug,)).fetchone()
        if row is None:
            row = con.execute("SELECT * FROM contacts WHERE name=?", (name,)).fetchone()
        if row is None:
            if a.dry:
                print(f"  [dry] новый контакт из карточки: {name}")
                created += 1
                continue
            slug = unique_slug(con, slug or slugify(name))
            cid = con.execute(
                "INSERT INTO contacts(slug,name,category,created_at,updated_at,source)"
                " VALUES(?,?,?,?,?,?)",
                (slug, name, "other", now(), now(), "obsidian")).lastrowid
            row = con.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
            created += 1
        else:
            updated += 1
        if a.dry:
            continue
        data = {"name": name}
        for prop, val in fm.items():
            col = COL_BY_PROP.get(str(prop).lower())
            if col and col not in ("name", "category"):
                data[col] = val
        # категория + подкатегория: чего нет в базе — заводится на лету,
        # подкатегория привязывается к своей корневой категории
        root = cat_ensure(con, str(fm.get("категория") or "")) if fm.get("категория") else None
        sub = str(fm.get("подкатегория") or "").strip()
        if sub:
            data["category"] = cat_ensure(con, sub, root)
        elif root:
            data["category"] = root
        apply_fields(con, row["id"], data)
        for prop in CHANNEL_PROPS:
            key = CHANNEL_LABEL.get(prop, prop)
            if key in fm or prop in fm:
                set_channels(con, row["id"], prop, split_list(fm.get(key, fm.get(prop))))
        for kind, prop in ATTR_PROPS.items():
            if prop in fm:
                set_attrs(con, row["id"], kind, split_list(fm.get(prop)))
        con.execute("UPDATE contacts SET note_path=? WHERE id=?",
                    (str(path.relative_to(VAULT)), row["id"]))
    if not a.dry:
        con.commit()
    print(f"pull: карточек {seen} · новых {created} · обновлено {updated}")


def write_moc(con) -> None:
    cats = cat_map(con)
    L = ["---", "type: moc", "tags: [MOC, социальный-капитал, контакты]",
         f"обновлено: {today()}", "---", "",
         f"# {SECTION}", "",
         "> Люди как капитал: кто есть в круге, чем силён, где пересекаемся, "
         "когда касались в последний раз.", "",
         "**Таблица со всеми полями** — [[Контакты.base|Контакты]] "
         "(плагин Bases: виды «Все контакты», «Клиенты и лиды», «Пора написать», "
         "«Чем полезны»). Если колонка не подхватилась — поправь вид прямо в "
         "интерфейсе, но помни: файл перезаписывается при `sync`, свои виды "
         "лучше заводить отдельным `.base`.", "",
         "**Машинная сторона** — `tools/social-capital/capital.py` "
         "(SQLite + граф связей). Правки в карточках подхватываются командой "
         "`pull`, правки в базе раскатываются командой `sync`.", ""]
    def people_of(key):
        return con.execute(
            "SELECT c.*, o.name org FROM contacts c LEFT JOIN orgs o ON o.id=c.org_id"
            " WHERE c.category=? AND COALESCE(c.archived,0)=0 ORDER BY c.name",
            (key,)).fetchall()

    def bullet(r):
        bits = [x for x in (r["role"], r["org"], r["city"]) if x]
        return (f"- {wikilink_of(con, r['slug'], r['name'])}"
                + (f" — {' · '.join(bits)}" if bits else ""))

    def listing(rows):
        """Массовые импорты (тысячи лидов) списком не печатаются: MOC — это
        оглавление круга, а не выгрузка базы. Для них — счётчик и указатель."""
        if len(rows) > MOC_MAX:
            return [f"_{len(rows)} записей — списком не печатаются, смотри "
                    f"[[Контакты.base|таблицу]] или папку `Люди`._", ""]
        return [bullet(r) for r in rows] + ([""] if rows else [])

    total = 0
    for key, c in cats.items():
        if c["parent"]:                     # подкатегории печатаются под корнем
            continue
        subs = [k for k in cat_tree(con, key)[1:]]
        own = people_of(key)
        cnt = len(own) + sum(len(people_of(s)) for s in subs)
        if not cnt:
            continue
        total += cnt
        L += [f"## {c['name']} ({cnt})", ""]
        L += listing(own)
        for s in subs:
            rows = people_of(s)
            if not rows:
                continue
            L += [f"### {cats[s]['name']} ({len(rows)})", ""]
            L += listing(rows)
    due = con.execute(
        "SELECT name, slug, next_touch FROM contacts WHERE next_touch IS NOT NULL"
        " AND next_touch<=? ORDER BY next_touch", (today(),)).fetchall()
    if due:
        L += ["## Пора написать", ""]
        L += [f"- {wikilink_of(con, r['slug'], r['name'])} — с {r['next_touch']}"
              for r in due] + [""]
    L += ["---", f"Всего в круге: **{total}**. Обновлено автоматически "
          f"(`capital.py sync`) {today()}."]
    MOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOC_PATH.write_text("\n".join(L), encoding="utf-8")


BASE_YAML = """# Таблица контактов для плагина Bases (Obsidian 1.9+).
# Источник данных — свойства карточек в папке «08 — Социальный капитал/Люди».
# Свойства из двух слов пишутся в скобочной форме note["последний контакт"] —
# точечная запись их не берёт. Колонки и фильтры удобнее донастраивать прямо в
# интерфейсе, но файл перезаписывается командой `capital.py sync`, поэтому свои
# виды лучше заводить отдельным .base-файлом.
filters:
  and:
    - file.inFolder("08 — Социальный капитал/Люди")
    - note.type == "contact"
views:
  - type: table
    name: Все контакты
    order:
      - file.name
      - note.категория
      - note.подкатегория
      - note.статус
      - note.организация
      - note.должность
      - note.город
      - note.email
      - note.telegram
      - note.интересы
      - note.близость
      - note["последний контакт"]
    sort:
      - property: note.категория
        direction: ASC
      - property: note.подкатегория
        direction: ASC
      - property: file.name
        direction: ASC
  - type: table
    name: Клиенты и лиды
    filters:
      or:
        - note.категория == "Клиенты"
        - note.категория == "Лиды"
    order:
      - file.name
      - note.категория
      - note.подкатегория
      - note.организация
      - note.должность
      - note["стадия сделки"]
      - note.сумма
      - note.email
      - note.телефон
      - note["следующий контакт"]
  - type: table
    name: Пора написать
    filters:
      and:
        - note["следующий контакт"] != null
    sort:
      - property: note["следующий контакт"]
        direction: ASC
    order:
      - file.name
      - note.категория
      - note["последний контакт"]
      - note["следующий контакт"]
      - note.telegram
  - type: table
    name: Чем полезны
    order:
      - file.name
      - note.категория
      - note["чем полезен"]
      - note["чем могу помочь"]
      - note.навыки
      - note.интересы
      - note.близость
"""


def write_base() -> None:
    BASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASE_PATH.write_text(BASE_YAML, encoding="utf-8")


def write_template(con) -> None:
    cats = ", ".join(r["key"] for r in con.execute(
        "SELECT key FROM categories ORDER BY sort"))
    fm = {"type": "contact", "tags": ["контакт", "социальный-капитал"],
          "имя": "", "категория": "other", "статус": "активный",
          "организация": "", "должность": "", "сфера": "", "город": "",
          "страна": "", "адрес": "", "email": [], "телефон": [], "telegram": "",
          "linkedin": "", "github": "", "сайт": "", "интересы": [], "навыки": [],
          "теги": [], "языки": "", "близость": 3, "доверие": 3, "влияние": 3,
          "ценность": 3, "кто он мне": "", "как познакомились": "",
          "дата знакомства": "", "последний контакт": "", "следующий контакт": "",
          "периодичность": 90, "чем полезен": "", "чем могу помочь": "",
          "о чём попросить": "", "заметка": ""}
    text = (dump_frontmatter(fm) + "\n# Имя\n\n"
            f"> Категории: {cats}\n\n"
            "Заполни свойства выше и запусти `capital.py pull` — карточка "
            "попадёт в базу, граф и таблицу Bases.\n\n"
            "## Мои заметки\n\n")
    TEMPLATE_PATH.write_text(text, encoding="utf-8")


# ── граф связей ───────────────────────────────────────────────────────────────
def _load_vsearch():
    path = REPO / "tools" / "obsidian" / "tools" / "obsidian_vsearch.py"
    spec = importlib.util.spec_from_file_location("ov_capital", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_graph(a):
    """Узлы — люди (и организации-хабы), рёбра — явные связи, общая работа,
    общие интересы/навыки/теги, общий город. Раскраска по категории."""
    con = db()
    rows = con.execute("SELECT * FROM contacts WHERE COALESCE(archived,0)=0").fetchall()
    if not rows:
        sys.exit("база пуста — добавь контакты (capital.py add) или импортируй")
    cats = cat_map(con)
    by_id = {r["id"]: r for r in rows}
    W: dict[tuple[str, str], float] = {}
    LBL: dict[tuple[str, str], list[str]] = {}

    def add(u: str, v: str, w: float, why: str):
        if u == v:
            return
        k = (u, v) if u < v else (v, u)
        W[k] = min(1.0, W.get(k, 0.0) + w)
        LBL.setdefault(k, []).append(why)

    nid = {r["id"]: f"c:{r['slug']}" for r in rows}
    for r in con.execute("SELECT * FROM relations"):
        if r["a"] in by_id and r["b"] in by_id:
            add(nid[r["a"]], nid[r["b"]],
                0.45 + 0.11 * (r["strength"] or 2), r["kind"] or "знакомы")
    # общая организация
    org_members: dict[int, list[int]] = {}
    for r in rows:
        if r["org_id"]:
            org_members.setdefault(r["org_id"], []).append(r["id"])
    for oid, members in org_members.items():
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                add(nid[x], nid[y], 0.55, f"вместе: {org_name(con, oid)}")
    # общие интересы / навыки / теги / проекты
    by_attr: dict[tuple[str, str], list[int]] = {}
    for r in con.execute("SELECT contact_id,kind,value FROM attrs"):
        if r["contact_id"] in by_id:
            by_attr.setdefault((r["kind"], r["value"].lower()), []).append(r["contact_id"])
    for (kind, val), members in by_attr.items():
        if len(members) > 12:            # слишком общий тег связывает всех — шум
            continue
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                add(nid[x], nid[y], 0.22 if kind in ("interest", "skill") else 0.18, val)
    # общий город — слабый штрих, сам по себе ребро не создаёт
    by_city: dict[str, list[int]] = {}
    for r in rows:
        if r["city"]:
            by_city.setdefault(r["city"].strip().lower(), []).append(r["id"])
    for city, members in by_city.items():
        if len(members) > 15:
            continue
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                add(nid[x], nid[y], 0.12, f"город: {city}")

    edges = [{"source": u, "target": v, "weight": round(w, 3)}
             for (u, v), w in W.items() if w >= a.thr]
    ids = list(nid.values())

    # организации как узлы-хабы (по умолчанию включены — так граф читается)
    org_nodes = []
    if not a.no_orgs:
        for oid, members in org_members.items():
            oname = org_name(con, oid)
            gid = f"o:{slugify(oname)}"
            org_nodes.append((gid, oname, oid, len(members)))
            ids.append(gid)
            for m in members:
                edges.append({"source": gid, "target": nid[m], "weight": 0.6})

    ov = _load_vsearch()
    # легенда графа = категории и подкатегории; у подкатегории — оттенок родителя
    ov.SECTION_COLORS = {k: cat_color(con, k) for k in cats}
    ov.SECTION_NAMES = {k: cat_label(con, k) for k in cats}
    ov.SECTION_COLORS["_org"] = "#607D8B"
    ov.SECTION_NAMES["_org"] = "Организации"

    positions = ov._compute_positions(ids, edges)
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1

    nodes, notes = [], {}
    for r in rows:
        gid = nid[r["id"]]
        d = contact_dict(con, r)
        px, py = positions.get(gid, (1500, 1500))
        label = d["name"] if len(d["name"]) <= 28 else d["name"][:27] + "…"
        sub = " · ".join(x for x in (d["role"], d["org"], d["city"]) if x)
        nodes.append({
            "id": gid, "label": label, "note": sub or d["name"],
            "path": d["note_path"] or f"{CARDS_REL}/{card_name(d['name'])}.md",
            "url": None,
            "section": d["category"],
            "section_name": cat_label(con, d["category"]),
            "color": cat_color(con, d["category"]),
            "degree": degree.get(gid, 0),
            "snippet": sub or (d["notes"] or ""),
            "x": px, "y": py,
        })
        notes[gid] = _node_note(con, d, cats)
    for gid, oname, oid, n in org_nodes:
        px, py = positions.get(gid, (1500, 1500))
        o = con.execute("SELECT * FROM orgs WHERE id=?", (oid,)).fetchone()
        nodes.append({
            "id": gid, "label": oname[:28], "note": f"организация · {n} чел.",
            "path": gid, "url": o["website"],
            "section": "_org", "section_name": "Организации", "color": "#607D8B",
            "degree": degree.get(gid, 0),
            "snippet": " · ".join(x for x in (o["industry"], o["city"]) if x),
            "x": px, "y": py,
        })
        who = [f"• {m['name']} — {m['role'] or '—'}" for m in con.execute(
            "SELECT name, role FROM contacts WHERE org_id=? ORDER BY name", (oid,))]
        notes[gid] = "\n".join([f"{oname} — организация",
                                " · ".join(x for x in (o["kind"], o["industry"],
                                                       o["city"], o["country"]) if x),
                                o["website"] or "", "", f"── Люди ({n}) ──", *who])

    data = {"nodes": nodes, "edges": edges, "meta": {
        "total_notes": len(nodes), "total_chunks": len(rows),
        "total_edges": len(edges), "threshold": a.thr, "top_k": 0,
        "built_at": int(dt.datetime.now().timestamp())}}
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    (GRAPH_DIR / "graph.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (GRAPH_DIR / "graph.html").write_text(
        ov._generate_html(data, notes), encoding="utf-8")
    print(f"graph: людей={len(rows)} организаций={len(org_nodes)} "
          f"связей={len(edges)} -> {GRAPH_DIR / 'graph.html'}")


def _node_note(con, d: dict, cats: dict) -> str:
    """Полное досье для правой панели графа (+ свободный текст карточки)."""
    P = [f"{d['name']}", " · ".join(x for x in (d["role"], d["org"], d["city"]) if x),
         f"Категория: {cat_label(con, d['category'])}"
         f" · статус: {d['status'] or '—'}", ""]
    for k, vs in d["channels"].items():
        P.append(f"{CHANNEL_LABEL.get(k, k)}: {', '.join(vs)}")
    if d["address"]:
        P.append(f"адрес: {d['address']}")
    for kind, prop in ATTR_PROPS.items():
        if d["attrs"].get(kind):
            P.append(f"{prop}: {', '.join(d['attrs'][kind])}")
    scores = [f"{lbl} {d[c]}" for c, lbl in (("closeness", "близость"),
              ("trust", "доверие"), ("influence", "влияние"),
              ("value_score", "ценность")) if d[c]]
    if scores:
        P.append("оценки: " + " · ".join(scores))
    if d["can_help"]:
        P.append(f"чем полезен: {d['can_help']}")
    if d["i_can_offer"]:
        P.append(f"чем полезен я: {d['i_can_offer']}")
    if d["how_met"] or d["met_date"]:
        P.append(f"знакомство: {d['how_met'] or '—'} ({d['met_date'] or '—'})")
    if d["last_contact"] or d["next_touch"]:
        P.append(f"контакт: последний {d['last_contact'] or '—'}"
                 f" · следующий {d['next_touch'] or '—'}")
    if d["notes"]:
        P += ["", d["notes"]]
    if d["links"]:
        P += ["", f"── Связи ({len(d['links'])}) ──"]
        P += [f"• {l['name']} — {l['kind']}" for l in d["links"]]
    if d["interactions"]:
        P += ["", f"── Взаимодействия ({len(d['interactions'])}) ──"]
        P += [f"• {i['ts']} · {i['kind'] or '—'} — {i['summary'] or ''}"
              for i in d["interactions"]]
    if d["note_path"]:
        p = VAULT / d["note_path"]
        if p.exists():
            free = split_card(p.read_text(encoding="utf-8"))[2].strip()
            if free and "Свободная часть карточки" not in free:
                P += ["", "── Из карточки ──", free[:4000]]
    return "\n".join(P)[:40000]


# ── импорт из Telegram ────────────────────────────────────────────────────────
def cmd_import_telegram(a):
    if not TELEGRAM_DRIVER.exists():
        sys.exit(f"нет драйвера Telegram: {TELEGRAM_DRIVER}")
    p = subprocess.run(["node", str(TELEGRAM_DRIVER), "get_dialogs",
                        json.dumps({"limit": a.limit})],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        sys.exit(f"драйвер Telegram: {p.stderr.strip()[-300:]}")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit("не разобрать ответ драйвера (см. tools/social-capital/README.md)")
    dialogs = data if isinstance(data, list) else data.get("result", [])
    people = [d for d in dialogs if d.get("isUser") and (d.get("name") or "").strip()]
    if not a.bots:
        # боты в личных диалогах — половина списка; отсекаем по флагу Telegram,
        # а на старом драйвере (без isBot) — по имени/нику
        people = [d for d in people if not (
            d.get("isBot") or re.search(r"bot$", (d.get("username") or ""), re.I)
            or re.search(r"\bbot\b", d.get("name") or "", re.I))]
    if a.contacts_only:
        people = [d for d in people if d.get("isContact")]
    me = subprocess.run(["node", str(TELEGRAM_DRIVER), "get_me", "{}"],
                        capture_output=True, text=True, timeout=60)
    try:                                   # свой же аккаунт в списке диалогов
        my_id = str((json.loads(me.stdout) or {}).get("id"))
        people = [d for d in people if str(d.get("id")) != my_id]
    except (json.JSONDecodeError, AttributeError):
        pass
    con = db()
    added, skipped = 0, 0
    for d in people:
        name = d["name"].strip()
        tg = ("@" + d["username"]) if d.get("username") else None
        exists = con.execute(
            "SELECT id FROM contacts WHERE name=? OR tg_id=?",
            (name, str(d.get("id")))).fetchone()
        if exists:
            skipped += 1
            continue
        if a.dry:
            print(f"  [dry] + {name}" + (f" ({tg})" if tg else ""))
            added += 1
            continue
        slug = unique_slug(con, slugify(name))
        cid = con.execute(
            "INSERT INTO contacts(slug,name,category,status,source,tg_id,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (slug, name, a.category, "активный", "telegram", str(d.get("id")),
             now(), now())).lastrowid
        if tg:
            set_channels(con, cid, "telegram", [tg])
        if d.get("phone"):
            phone = d["phone"] if str(d["phone"]).startswith("+") else "+" + str(d["phone"])
            set_channels(con, cid, "phone", [phone])
        added += 1
    if not a.dry:
        con.commit()
    print(f"import-telegram: диалогов {len(dialogs)} · людей {len(people)} ·"
          f" добавлено {added} · уже было {skipped}"
          + (" [dry]" if a.dry else f" · категория {a.category}"))


# ── импорт из файлов: vCard и CSV ─────────────────────────────────────────────
def norm_phone(v: str) -> str:
    """Читаемый вид номера: +7 (861) 221-59-42. Импорты приходят кто с чем —
    от «8 861 2215942» до «+7(861)221-59-42», в базе пусть будет одинаково."""
    d = re.sub(r"[^\d+]", "", v or "")
    core = d.lstrip("+")
    if len(core) == 11 and core[0] in "78":
        core = "7" + core[1:]
        return f"+7 ({core[1:4]}) {core[4:7]}-{core[7:9]}-{core[9:]}"
    if len(core) == 10:
        return f"+7 ({core[:3]}) {core[3:6]}-{core[6:8]}-{core[8:]}"
    return v.strip()


def _upsert_imported(con, name: str, fields: dict, chans: dict, attrs: dict,
                     category: str, source: str, dry: bool) -> str:
    """Заводит контакт из импорта или дополняет существующего (по имени/каналу).
    Возвращает '+' (новый), '~' (дополнен) или '=' (нечего добавлять)."""
    if chans.get("phone"):
        chans = {**chans, "phone": [norm_phone(p) for p in chans["phone"]]}
    row = con.execute("SELECT * FROM contacts WHERE name=?", (name,)).fetchone()
    if row is None:
        for kind, vals in chans.items():    # тот же человек под другим именем
            for v in vals:
                row = con.execute(
                    "SELECT c.* FROM contacts c JOIN channels ch ON ch.contact_id=c.id"
                    " WHERE ch.kind=? AND lower(ch.value)=?",
                    (kind, v.lower())).fetchone()
                if row:
                    break
            if row:
                break
    if dry:
        return "~" if row else "+"
    if row is None:
        slug = unique_slug(con, slugify(name))
        cid = con.execute(
            "INSERT INTO contacts(slug,name,category,status,source,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (slug, name, category, "активный", source, now(), now())).lastrowid
        mark = "+"
    else:
        cid, mark = row["id"], "="
    # существующему дописываем только пустые поля — импорт не затирает ручное
    fill = {k: v for k, v in fields.items()
            if v and (row is None or not row[k if k != "org" else "org_id"])}
    if fill:
        apply_fields(con, cid, fill)
        mark = "+" if mark == "+" else "~"
    for kind, vals in chans.items():
        for v in vals:
            cur = con.execute("SELECT 1 FROM channels WHERE contact_id=? AND kind=?"
                              " AND lower(value)=?", (cid, kind, v.lower())).fetchone()
            if not cur:
                con.execute("INSERT OR IGNORE INTO channels(contact_id,kind,value)"
                            " VALUES(?,?,?)", (cid, kind, v))
                mark = "+" if mark == "+" else "~"
    for kind, vals in attrs.items():
        for v in vals:
            con.execute("INSERT OR IGNORE INTO attrs(contact_id,kind,value)"
                        " VALUES(?,?,?)", (cid, kind, v))
    return mark


VCARD_TEL = {"cell": "phone", "voice": "phone", "work": "phone", "home": "phone"}


def cmd_import_vcf(a):
    """vCard 3/4 — выгрузка из Контактов macOS/iOS, Google Contacts, телефона."""
    text = Path(a.path).read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\r?\n[ \t]", "", text)          # развернуть переносы строк
    con = db()
    cat = cat_resolve(con, a.category)
    stats = {"+": 0, "~": 0, "=": 0}
    for card in re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, re.S | re.I):
        fields: dict = {}
        chans: dict[str, list[str]] = {}
        attrs: dict[str, list[str]] = {}
        name = ""
        for line in card.splitlines():
            if ":" not in line:
                continue
            head, val = line.split(":", 1)
            val = val.strip().replace("\\,", ",").replace("\\;", ";").replace("\\n", ", ")
            parts = head.upper().split(";")
            tag = parts[0].split(".")[-1]
            if not val:
                continue
            if tag == "FN":
                name = val
            elif tag == "N" and not name:
                name = " ".join(x for x in reversed(val.split(";")[:2]) if x).strip()
            elif tag == "ORG":
                fields["org"] = val.split(";")[0].strip()
            elif tag == "TITLE":
                fields["role"] = val
            elif tag == "EMAIL":
                chans.setdefault("email", []).append(val)
            elif tag == "TEL":
                chans.setdefault("phone", []).append(re.sub(r"[^\d+]", "", val))
            elif tag == "URL":
                chans.setdefault("site", []).append(val)
            elif tag == "IMPP" or tag == "X-SOCIALPROFILE":
                low = val.lower()
                for k in ("telegram", "linkedin", "github", "instagram", "whatsapp"):
                    if k in low or k in head.lower():
                        chans.setdefault(k, []).append(val.split(":")[-1])
                        break
            elif tag == "ADR":
                adr = [x for x in val.split(";") if x.strip()]
                if adr:
                    fields["address"] = ", ".join(adr)
                    if len(adr) >= 3:
                        fields.setdefault("city", adr[-3] if len(adr) > 3 else adr[1])
            elif tag == "BDAY":
                fields["birthday"] = val[:10]
            elif tag == "NOTE":
                fields["notes"] = val
            elif tag == "CATEGORIES":
                attrs.setdefault("tag", []).extend(
                    [x.strip() for x in val.split(",") if x.strip()])
        if not name:
            continue
        m = _upsert_imported(con, name, fields, chans, attrs, cat,
                             a.source or "vcard", a.dry)
        stats[m] += 1
        if a.dry:
            print(f"  [dry] {m} {name}" + (f" · {fields.get('org', '')}" if fields.get("org") else ""))
    if not a.dry:
        con.commit()
    print(f"import-vcf: новых {stats['+']} · дополнено {stats['~']} ·"
          f" без изменений {stats['=']}" + (" [dry]" if a.dry else ""))


# заголовок столбца (в нижнем регистре) → куда класть
CSV_MAP = {
    "имя": "name", "name": "name", "full name": "name", "фио": "name",
    "first name": "_first", "last name": "_last", "имя контакта": "name",
    "организация": "org", "company": "org", "organization": "org", "компания": "org",
    "должность": "role", "position": "role", "title": "role", "job title": "role",
    "email": "@email", "e-mail": "@email", "почта": "@email",
    "e-mail address": "@email", "email address": "@email", "email 1 - value": "@email",
    "phone": "@phone", "телефон": "@phone", "мобильный": "@phone",
    "phone 1 - value": "@phone", "phone number": "@phone",
    "telegram": "@telegram", "тг": "@telegram",
    "linkedin": "@linkedin", "url": "@site", "сайт": "@site", "website": "@site",
    "github": "@github", "instagram": "@instagram",
    "город": "city", "city": "city", "страна": "country", "country": "country",
    "адрес": "address", "address": "address",
    "интересы": "#interest", "навыки": "#skill", "теги": "#tag", "tags": "#tag",
    "заметка": "notes", "notes": "notes", "note": "notes", "комментарий": "notes",
    "категория": "_category", "подкатегория": "_subcategory",
    "день рождения": "birthday", "birthday": "birthday",
    "сфера": "industry", "industry": "industry",
}


def cmd_import_csv(a):
    """CSV из чего угодно: Google Contacts, LinkedIn, выгрузка CRM, своя табличка.
    Колонки распознаются по заголовку (см. CSV_MAP), лишние — игнорируются."""
    con = db()
    default_cat = cat_resolve(con, a.category)
    stats = {"+": 0, "~": 0, "=": 0}
    with Path(a.path).open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        for rec in csv.DictReader(f, dialect=dialect):
            fields, chans, attrs = {}, {}, {}
            first = last = name = ""
            cat, sub = None, None
            for col, val in rec.items():
                val = (val or "").strip()
                if not val or not col:
                    continue
                dest = CSV_MAP.get(col.strip().lower())
                if not dest:
                    continue
                if dest == "name":
                    name = val
                elif dest == "_first":
                    first = val
                elif dest == "_last":
                    last = val
                elif dest == "_category":
                    cat = val
                elif dest == "_subcategory":
                    sub = val
                elif dest.startswith("@"):
                    chans.setdefault(dest[1:], []).extend(split_list(val))
                elif dest.startswith("#"):
                    attrs.setdefault(dest[1:], []).extend(split_list(val))
                else:
                    fields[dest] = val
            name = name or " ".join(x for x in (first, last) if x)
            if not name.strip():
                continue
            key = default_cat
            if cat:
                key = cat_ensure(con, cat) if not a.dry else default_cat
            if sub and not a.dry:
                key = cat_ensure(con, sub, cat_ensure(con, cat) if cat else None)
            m = _upsert_imported(con, name.strip(), fields, chans, attrs, key,
                                 a.source or f"csv:{Path(a.path).name}", a.dry)
            stats[m] += 1
            if a.dry:
                print(f"  [dry] {m} {name.strip()}"
                      + (f" · {fields.get('org')}" if fields.get("org") else ""))
    if not a.dry:
        con.commit()
    print(f"import-csv: новых {stats['+']} · дополнено {stats['~']} ·"
          f" без изменений {stats['=']}" + (" [dry]" if a.dry else ""))


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(prog="capital.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    p = sub.add_parser("add", help="добавить контакт")
    p.add_argument("name")
    p.add_argument("field", nargs="*", help="поле=значение")
    p.add_argument("--slug")
    for f in ("category", "status", "org", "role", "city", "country",
              "notes", "source", "relation"):
        p.add_argument(f"--{f}")
    for f in ("email", "phone", "tg", "site", "linkedin", "github", "instagram",
              "interests", "skills", "tags", "projects"):
        p.add_argument(f"--{f}")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("set", help="изменить поля")
    p.add_argument("ref")
    p.add_argument("field", nargs="+", help="поле=значение")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("show")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("list")
    p.add_argument("--category")
    p.add_argument("--tag")
    p.add_argument("--q")
    p.add_argument("--due", action="store_true", help="кому пора написать")
    p.add_argument("--archived", action="store_true")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("rm")
    p.add_argument("ref")
    p.add_argument("--card", action="store_true", help="удалить и карточку в Obsidian")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("channel")
    p.add_argument("ref")
    p.add_argument("action", choices=["add", "rm"])
    p.add_argument("kind")
    p.add_argument("value")
    p.add_argument("--label")
    p.add_argument("--primary", action="store_true")
    p.set_defaults(fn=cmd_channel)

    p = sub.add_parser("tag")
    p.add_argument("ref")
    p.add_argument("action", choices=["add", "rm"])
    p.add_argument("kind", help="interest|skill|tag|project")
    p.add_argument("value", nargs="+")
    p.set_defaults(fn=cmd_tag)

    p = sub.add_parser("link")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--kind", default="знакомы")
    p.add_argument("--strength", type=int, default=2)
    p.add_argument("--since")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_link)

    p = sub.add_parser("unlink")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--kind")
    p.set_defaults(fn=cmd_unlink)

    p = sub.add_parser("log", help="записать взаимодействие")
    p.add_argument("ref")
    p.add_argument("--kind", default="контакт")
    p.add_argument("--summary")
    p.add_argument("--channel")
    p.add_argument("--sentiment")
    p.add_argument("--next")
    p.add_argument("--date")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("category")
    p.add_argument("action", choices=["list", "add", "rm"])
    p.add_argument("key", nargs="?")
    p.add_argument("name", nargs="?")
    p.add_argument("--parent", help="сделать подкатегорией (ключ или имя)")
    p.add_argument("--move-to", help="куда перевести контакты при rm")
    p.add_argument("--color")
    p.add_argument("--desc")
    p.add_argument("--sort", type=int, default=100)
    p.set_defaults(fn=cmd_category)

    p = sub.add_parser("org")
    p.add_argument("action", choices=["list", "set"])
    p.add_argument("name", nargs="?")
    p.add_argument("--kind")
    p.add_argument("--industry")
    p.add_argument("--city")
    p.add_argument("--country")
    p.add_argument("--site")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_org)

    p = sub.add_parser("sync", help="БД → карточки Obsidian")
    p.add_argument("--pull", action="store_true", help="сначала забрать правки из карточек")
    p.add_argument("--prune", action="store_true",
                   help="удалить карточки, за которыми в базе нет контакта")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("dedupe", help="слить дубли (одно имя + общий канал связи)")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--show", type=int, default=20, help="сколько групп показать в --dry")
    p.set_defaults(fn=cmd_dedupe)

    p = sub.add_parser("pull", help="карточки Obsidian → БД")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("graph")
    p.add_argument("--thr", type=float, default=0.3, help="порог веса ребра")
    p.add_argument("--no-orgs", action="store_true", help="без узлов-организаций")
    p.set_defaults(fn=cmd_graph)

    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    p = sub.add_parser("export-csv")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_export_csv)

    p = sub.add_parser("import-telegram")
    p.add_argument("--limit", type=int, default=600,
                   help="сколько диалогов просмотреть (личные лежат ниже каналов)")
    p.add_argument("--category", default="inbox")
    p.add_argument("--bots", action="store_true", help="не отсеивать ботов")
    p.add_argument("--contacts-only", action="store_true",
                   help="только те, кто есть в адресной книге Telegram")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_import_telegram)

    p = sub.add_parser("import-vcf", help="vCard (.vcf): Контакты macOS/iOS, Google")
    p.add_argument("path")
    p.add_argument("--category", default="inbox")
    p.add_argument("--source")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_import_vcf)

    p = sub.add_parser("import-csv", help="CSV: Google Contacts, LinkedIn, CRM, своя табличка")
    p.add_argument("path")
    p.add_argument("--category", default="inbox")
    p.add_argument("--source")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_import_csv)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
