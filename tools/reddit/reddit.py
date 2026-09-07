#!/usr/bin/env python3
"""
Reddit — инструмент публикаций и участия в сабреддитах.

Не «рассыльщик». Reddit устроен так, что механическая отправка там бесполезна:
пост уходит по API успешно, а AutoModerator снимает его через секунду — и автор
продолжает видеть свой пост, пока никто другой его не видит. Поэтому здесь три
слоя, и публикация — только третий:

  1. Разведка   — какие сабреддиты есть, что в них можно, какие правила,
                  какие требования к посту (флейр, длина, домены).
  2. Участие    — поиск живых тредов по теме и ответы в них. Основной канал:
                  комментарий по делу не снимают и не репортят.
  3. Публикация — пост из файла-спеки, с преполётной проверкой и обязательной
                  сверкой через 10–15 минут, что он не удалён молча.

Гарды (config.json → guards) не дают повторить историю с телеграмом:
  • не чаще N часов между постами и не больше N постов в сутки;
  • в один сабреддит — не чаще раза в N дней;
  • правило 9:1 (у Reddit это писаная норма самопиара): инструмент считает
    отношение комментариев к своим постам и предупреждает, когда перекос.

Схема (reddit.db):
  subreddits  — рабочий список площадок: правила, требования, вердикт проверки
  posts       — свои посты: что, куда, когда, жив ли (проверка удаления)
  comments    — свои комментарии (они же знаменатель в правиле 9:1)
  kv          — кэш токена и служебное

Вход — твой личный аккаунт, ничего регистрировать не надо:
  python3 reddit.py login    — откроется системный Chrome, входишь руками,
                               сессия ложится в ~/.reddit-session и живёт дальше.
Дальше весь инструмент ходит на old.reddit.com под этой сессией через
driver.mjs (Playwright + системный Chrome), как это сделано у hh.ru.
config.json нужен только для гардов и полностью необязателен.

Команды:
  login                                — войти руками в открывшемся Chrome
  me                                   — аккаунт: карма, возраст, ограничения
  find <запрос> [--limit 25]           — искать сабреддиты по теме
  sub info <sab>                       — о сабреддите: люди, тип постов, флейры
  sub rules <sab>                      — правила текстом (включая правила сайта)
  sub check <sab> [--kind self|link]   — ПРЕПОЛЁТ: можно ли мне туда постить
  sub add <sab> [--note ...]           — в рабочий список
  sub list [--checked]                 — рабочий список с вердиктами
  threads <запрос> [--sub a,b] [--days 7] [--limit 25]
                                       — живые треды по теме (кому отвечать)
  post <файл-спеки> [--dry] [--force]  — опубликовать пост из спеки
  comment <thing_id> <файл|--text ...> — ответить в тред (t3_… пост, t1_… коммент)
  verify [--id ID] [--all]             — жив ли пост: проверка тихого удаления
  inbox [--unread] [--limit 25]        — ответы на мои посты и комментарии
  stats                                — свои посты/комментарии, соотношение 9:1
  plan                                 — что гарды разрешают сегодня

Формат спеки поста (JSON):
  {
    "subreddit": "languagelearning",
    "kind": "self",                    // self — текстовый, link — ссылка
    "title": "…",
    "text": "…",                       // для kind=self; markdown
    "url": "https://…",                // для kind=link
    "flair": "Discussion",             // по названию, id подберётся сам
    "nsfw": false,
    "send_replies": true
  }
Текст можно держать отдельным файлом: "text_file": "post.md".

Только stdlib. Сеть — urllib, база — sqlite3.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "reddit.db"
CONFIG_PATH = HERE / "config.json"
DRIVER = HERE / "driver.mjs"

WWW_BASE = "https://old.reddit.com"

# Слова, по которым в правилах сабреддита узнаётся запрет самопиара.
SELF_PROMO_MARKERS = [
    "self-promo", "self promo", "selfpromo", "promotion", "promotional",
    "advertis", "no ads", "soliciting", "solicitation", "referral",
    "surveys", "survey", "market research", "recruit", "no apps",
    "app promo", "shameless plug", "spam",
]

DEFAULT_GUARDS = {
    "min_hours_between_posts": 12,
    "max_posts_per_day": 3,
    "days_between_same_sub": 30,
    "comment_to_post_ratio": 5,      # цель: столько комментариев на один свой пост
    "min_account_age_days": 30,      # ниже — почти везде авто-снос
    "min_comment_karma": 50,
}


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
        CREATE TABLE IF NOT EXISTS subreddits (
            name            TEXT PRIMARY KEY,
            subscribers     INTEGER,
            submission_type TEXT,
            over18          INTEGER DEFAULT 0,
            flair_required  INTEGER DEFAULT 0,
            promo_rule      TEXT,      -- цитата правила про самопиар, если нашлось
            verdict         TEXT,      -- ok | careful | no
            verdict_note    TEXT,
            note            TEXT,
            checked_at      REAL,
            added_at        REAL
        );
        CREATE TABLE IF NOT EXISTS posts (
            id          TEXT PRIMARY KEY,   -- t3_xxxxx
            subreddit   TEXT,
            title       TEXT,
            kind        TEXT,
            url         TEXT,
            permalink   TEXT,
            created_at  REAL,
            alive       INTEGER,            -- NULL — не проверяли, 1 — жив, 0 — снят
            removed_by  TEXT,
            checked_at  REAL,
            score       INTEGER,
            num_comments INTEGER
        );
        CREATE TABLE IF NOT EXISTS comments (
            id          TEXT PRIMARY KEY,   -- t1_xxxxx
            parent_id   TEXT,
            subreddit   TEXT,
            body        TEXT,
            permalink   TEXT,
            created_at  REAL
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
        (key, json.dumps(value)),
    )
    conn.commit()


def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


# ──────────────────────────────── клиент API ────────────────────────────────

class Reddit:
    """
    Клиент поверх твоей живой сессии Chrome (driver.mjs), без ключей и
    регистрации приложений. Запросы уходят на old.reddit.com под теми же
    кукисами, что и в обычном браузере.

    Пути внутри команд записаны в привычной для Reddit форме (/r/x/about,
    /api/info); сюда же сведён перевод их в вид старого сайта (.json).
    """

    def __init__(self, cfg: dict, conn: sqlite3.Connection):
        self.cfg = cfg
        self.conn = conn
        if not DRIVER.exists():
            die(f"нет драйвера {DRIVER}")

    # — транспорт —

    # Один браузер на весь прогон: без этого каждый запрос — запуск Chrome,
    # и проверка трёх десятков сабреддитов растягивается на часы.
    _server = None
    _req_id = 0

    def _serve(self, tool: str, args: dict):
        if Reddit._server is None:
            Reddit._server = subprocess.Popen(
                ["node", str(DRIVER), "serve"],
                cwd=str(HERE), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
        Reddit._req_id += 1
        rid = Reddit._req_id
        srv = Reddit._server
        try:
            srv.stdin.write(json.dumps({"id": rid, "tool": tool, "args": args}) + "\n")
            srv.stdin.flush()
            line = srv.stdout.readline()
        except (BrokenPipeError, ValueError):
            Reddit._server = None
            return None
        if not line:
            Reddit._server = None
            return None
        try:
            return json.loads(line).get("result")
        except json.JSONDecodeError:
            return None

    @staticmethod
    def stop_server():
        if Reddit._server is not None:
            try:
                Reddit._server.stdin.write(json.dumps({"id": 0, "tool": "quit"}) + "\n")
                Reddit._server.stdin.flush()
                Reddit._server.wait(timeout=15)
            except Exception:
                Reddit._server.kill()
            Reddit._server = None

    def _drive(self, tool: str, payload: dict | None = None):
        # login/open интерактивные — только разовым запуском
        if tool in ("get", "post", "whoami"):
            res = self._serve(tool, payload or {})
            if res is not None:
                if isinstance(res, dict) and res.get("_error") == "not_logged_in":
                    die("сессия Reddit не найдена или просрочена: python3 reddit.py login")
                return res
        cmd = ["node", str(DRIVER), tool]
        if payload is not None:
            cmd.append(json.dumps(payload, ensure_ascii=False))
        proc = subprocess.run(
            cmd, cwd=str(HERE), capture_output=True, text=True, timeout=420
        )
        out = (proc.stdout or "").strip()
        if not out:
            err = (proc.stderr or "").strip()[-400:]
            die(f"драйвер молчит ({tool}). stderr: {err}")
        try:
            res = json.loads(out)
        except json.JSONDecodeError:
            die(f"драйвер вернул не JSON: {out[:300]}")
        if isinstance(res, dict) and res.get("_error") == "not_logged_in":
            die("сессия Reddit не найдена или просрочена. Войди один раз:\n"
                f"    node {DRIVER.name} login   (из {HERE})")
        return res

    @staticmethod
    def _browser_path(path: str) -> str:
        """Путь OAuth-вида → путь старого сайта."""
        if path == "/api/v1/me":
            return "/api/me.json"
        m = re.match(r"^/api/v1/([^/]+)/post_requirements$", path)
        if m:
            return f"/api/v1/{m.group(1)}/post_requirements.json"
        if path.endswith(".json"):
            return path
        return path + ".json"

    def get(self, path, **params):
        res = self._drive("get", {"path": self._browser_path(path), "params": params})
        if isinstance(res, dict) and res.get("_error"):
            status = res.get("_status")
            if status == 404:
                return {}
            if status == 403:
                die(f"403 на {path} — приватный сабреддит, бан или выкинутая сессия")
            return {}
        # /api/me.json приходит обёрнутым в {kind, data} — команды ждут плоский вид
        if path == "/api/v1/me":
            data = res.get("data", res) if isinstance(res, dict) else {}
            if not data.get("name"):
                die("для этого нужен вход: python3 reddit.py login")
            return data
        return res

    def post_form(self, path, **data):
        res = self._drive("post", {"path": path, "data": data})
        if isinstance(res, dict) and res.get("_error"):
            die(f"{path}: {res['_error']} {res.get('_body', '')}")
        return res

    def whoami(self):
        return self._drive("whoami")

    # — «жив ли пост» —
    # Автору снятый пост показывают как обычный, поэтому нужен внешний взгляд.
    # Анонимный www.reddit.com/*.json для этого не годится: Reddit отдаёт на него
    # 403 Blocked с большинства IP (проверено). Работаем через OAuth двумя
    # независимыми сигналами: поле removed_by_category и наличие поста в ленте
    # /new — снятые посты из лент исчезают.

    def info(self, thing_id: str) -> dict | None:
        res = self.get("/api/info", id=thing_id)
        children = res.get("data", {}).get("children", [])
        return children[0]["data"] if children else None

    def in_listing(self, sub: str, thing_id: str, created_utc: float,
                   pages: int = 3) -> bool | None:
        """True — пост в ленте, False — точно нет, None — не докрутили до его возраста."""
        after = None
        oldest = None
        for _ in range(pages):
            res = self.get(f"/r/{sub}/new", limit=100, after=after)
            data = res.get("data", {})
            children = data.get("children", [])
            if not children:
                break
            for c in children:
                d = c["data"]
                if d.get("name") == thing_id:
                    return True
                oldest = d.get("created_utc", oldest)
            after = data.get("after")
            if not after:
                # долистали до конца ленты и не встретили — значит снят
                return False
        if oldest is not None and oldest < created_utc:
            # прошли мимо его возраста и не нашли
            return False
        return None


# ─────────────────────────────── вспомогательное ───────────────────────────────

def ago(ts: float | None) -> str:
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 3600:
        return f"{int(d // 60)} мин назад"
    if d < 86400:
        return f"{int(d // 3600)} ч назад"
    return f"{int(d // 86400)} дн назад"


def fmt_count(n) -> str:
    if n is None:
        return "?"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def norm_sub(name: str) -> str:
    return name.strip().lstrip("/").removeprefix("r/").strip("/")


def find_promo_rule(rules: list[dict]) -> str | None:
    """Ищет в правилах пункт про самопиар/рекламу и возвращает его текстом."""
    for rule in rules:
        blob = " ".join(
            str(rule.get(k) or "") for k in ("short_name", "description", "violation_reason")
        ).lower()
        for marker in SELF_PROMO_MARKERS:
            if marker in blob:
                title = rule.get("short_name") or rule.get("violation_reason") or "правило"
                desc = (rule.get("description") or "").strip().replace("\n", " ")
                return f"{title}: {desc[:300]}"
    return None


# ──────────────────────────────── команды ────────────────────────────────

def cmd_me(rd: Reddit, args):
    me = rd.get("/api/v1/me")
    age_days = (time.time() - me.get("created_utc", time.time())) / 86400
    print(f"u/{me.get('name')}")
    print(f"  возраст аккаунта : {age_days:.0f} дн")
    print(f"  карма post/comment: {me.get('link_karma')} / {me.get('comment_karma')}")
    print(f"  всего кармы      : {me.get('total_karma')}")
    if me.get("is_suspended"):
        print("  ⚠️  аккаунт ПРИОСТАНОВЛЕН")
    g = rd.cfg["guards"]
    problems = []
    if age_days < g["min_account_age_days"]:
        problems.append(
            f"аккаунту {age_days:.0f} дн (< {g['min_account_age_days']}) — "
            "большинство сабреддитов снимут пост автоматически"
        )
    if (me.get("comment_karma") or 0) < g["min_comment_karma"]:
        problems.append(
            f"карма комментариев {me.get('comment_karma')} "
            f"(< {g['min_comment_karma']}) — то же самое"
        )
    if problems:
        print("\n⚠️  до постинга стоит закрыть:")
        for p in problems:
            print(f"  • {p}")
        print("  Лечится единственным способом: несколько дней осмысленных "
              "комментариев в тех же сабреддитах.")
    else:
        print("\n✓ по возрасту и карме проходишь типовые фильтры")


def cmd_find(rd: Reddit, args):
    res = rd.get("/subreddits/search", q=args.query, limit=args.limit,
                 sort="relevance", include_over_18="off")
    rows = [c["data"] for c in res.get("data", {}).get("children", [])]
    if not rows:
        print("ничего не нашлось")
        return
    print(f"{'сабреддит':32} {'людей':>8}  {'тип':10} описание")
    for d in rows:
        desc = (d.get("public_description") or "").replace("\n", " ")[:60]
        print(
            f"r/{d['display_name']:30.30} {fmt_count(d.get('subscribers')):>8}  "
            f"{(d.get('submission_type') or '?'):10.10} {desc}"
        )


def _sub_facts(rd: Reddit, sub: str) -> dict:
    about = rd.get(f"/r/{sub}/about").get("data", {})
    rules_payload = rd.get(f"/r/{sub}/about/rules")
    rules = rules_payload.get("rules", [])
    try:
        reqs = rd.get(f"/api/v1/{sub}/post_requirements")
    except SystemExit:
        reqs = {}
    return {"about": about, "rules": rules, "reqs": reqs,
            "site_rules": rules_payload.get("site_rules", [])}


def cmd_login(rd: Reddit, args):
    """Единственный шаг настройки: войти руками в открывшемся Chrome."""
    res = rd._drive("login")
    if res.get("ok"):
        print(f"✓ вошёл как u/{res['user']} · {res.get('note', '')}")
    else:
        why = res.get("error") or res.get("_error") or json.dumps(res, ensure_ascii=False)
        die(f"вход не удался: {why}")


def cmd_sub_info(rd: Reddit, args):
    sub = norm_sub(args.subreddit)
    f = _sub_facts(rd, sub)
    a, reqs = f["about"], f["reqs"]
    print(f"r/{a.get('display_name', sub)} — {a.get('title', '')}")
    print(f"  подписчиков      : {fmt_count(a.get('subscribers'))} "
          f"(онлайн {fmt_count(a.get('active_user_count'))})")
    print(f"  тип постов       : {a.get('submission_type')}")
    print(f"  18+              : {'да' if a.get('over18') else 'нет'}")
    if a.get("user_is_banned"):
        print("  ⚠️  ТЫ ЗАБАНЕН в этом сабреддите")
    print(f"  подписан         : {'да' if a.get('user_is_subscriber') else 'нет'}")
    if reqs:
        print(f"  флейр обязателен : {'да' if reqs.get('is_flair_required') else 'нет'}")
        tmin = reqs.get("title_text_min_length")
        tmax = reqs.get("title_text_max_length")
        if tmin or tmax:
            print(f"  длина заголовка  : {tmin or 0}–{tmax or '∞'}")
        if reqs.get("body_restriction_policy"):
            print(f"  тело поста       : {reqs['body_restriction_policy']}")
        if reqs.get("domain_blacklist"):
            print(f"  домены в чёрном  : {', '.join(reqs['domain_blacklist'][:10])}")
        if reqs.get("guidelines_text"):
            print(f"  памятка сабреддита: {reqs['guidelines_text'][:200]}")
    if reqs.get("is_flair_required") or args.flairs:
        try:
            flairs = rd.get(f"/r/{sub}/api/link_flair_v2")
            if flairs:
                print("  флейры           : " + ", ".join(
                    f"{fl.get('text')}" for fl in flairs[:15]))
        except SystemExit:
            print("  флейры           : недоступны (нужны права)")
    promo = find_promo_rule(f["rules"])
    if promo:
        print(f"\n⚠️  правило про самопиар — {promo}")


def cmd_sub_rules(rd: Reddit, args):
    sub = norm_sub(args.subreddit)
    payload = rd.get(f"/r/{sub}/about/rules")
    rules = payload.get("rules", [])
    if not rules:
        print("правил не опубликовано (но правила сайта всё равно действуют)")
    for i, rule in enumerate(rules, 1):
        print(f"{i}. {rule.get('short_name')}  [{rule.get('kind')}]")
        desc = (rule.get("description") or "").strip()
        if desc:
            for line in desc.splitlines():
                print(f"     {line}")
        print()


def cmd_sub_check(rd: Reddit, args):
    """Преполёт: можно ли мне сюда постить и что именно проверить руками."""
    sub = norm_sub(args.subreddit)
    conn = rd.conn
    f = _sub_facts(rd, sub)
    a, reqs, rules = f["about"], f["reqs"], f["rules"]
    me = rd.get("/api/v1/me")
    age_days = (time.time() - me.get("created_utc", time.time())) / 86400
    g = rd.cfg["guards"]

    blockers, warnings = [], []

    if a.get("user_is_banned"):
        blockers.append("ты забанен в этом сабреддите")
    st = a.get("submission_type")
    if st == "link" and args.kind == "self":
        blockers.append("сабреддит принимает только ссылки, текстовый пост не пройдёт")
    if st == "self" and args.kind == "link":
        blockers.append("сабреддит принимает только текстовые посты, ссылка не пройдёт")

    promo = find_promo_rule(rules)
    if promo:
        warnings.append(f"есть правило про самопиар/рекламу — {promo}")

    if age_days < g["min_account_age_days"]:
        warnings.append(
            f"аккаунту {age_days:.0f} дн — типовой фильтр AutoModerator снимает "
            "посты младше 30 дней"
        )
    if (me.get("comment_karma") or 0) < g["min_comment_karma"]:
        warnings.append(
            f"карма комментариев {me.get('comment_karma')} — многие сабреддиты "
            "требуют больше"
        )
    if not a.get("user_is_subscriber"):
        warnings.append("ты не подписан на сабреддит — подпишись и почитай неделю")

    if reqs.get("is_flair_required"):
        warnings.append("флейр обязателен — без него пост снимут")
    if reqs.get("domain_blacklist"):
        warnings.append(f"домены в чёрном списке: {', '.join(reqs['domain_blacklist'][:5])}")

    # свои прошлые посты в этот сабреддит
    row = conn.execute(
        "SELECT created_at, title FROM posts WHERE subreddit=? "
        "ORDER BY created_at DESC LIMIT 1", (sub,)
    ).fetchone()
    if row:
        days = (time.time() - row["created_at"]) / 86400
        if days < g["days_between_same_sub"]:
            blockers.append(
                f"ты уже постил сюда {days:.0f} дн назад «{row['title'][:40]}» "
                f"(гард: не чаще раза в {g['days_between_same_sub']} дн)"
            )

    verdict = "no" if blockers else ("careful" if warnings else "ok")
    note = "; ".join(blockers + warnings)[:500]

    conn.execute(
        "INSERT INTO subreddits(name,subscribers,submission_type,over18,"
        "flair_required,promo_rule,verdict,verdict_note,checked_at,added_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,COALESCE((SELECT added_at FROM subreddits "
        "WHERE name=?),?)) "
        "ON CONFLICT(name) DO UPDATE SET subscribers=excluded.subscribers,"
        "submission_type=excluded.submission_type,over18=excluded.over18,"
        "flair_required=excluded.flair_required,promo_rule=excluded.promo_rule,"
        "verdict=excluded.verdict,verdict_note=excluded.verdict_note,"
        "checked_at=excluded.checked_at",
        (sub, a.get("subscribers"), st, 1 if a.get("over18") else 0,
         1 if reqs.get("is_flair_required") else 0, promo, verdict, note,
         time.time(), sub, time.time()),
    )
    conn.commit()

    icon = {"ok": "✓", "careful": "⚠️", "no": "✗"}[verdict]
    print(f"{icon} r/{sub} — {fmt_count(a.get('subscribers'))} подписчиков, "
          f"тип постов: {st}")
    for b in blockers:
        print(f"  ✗ {b}")
    for w in warnings:
        print(f"  ⚠️  {w}")
    if verdict == "ok":
        print("  препятствий не видно")
    print("\nЧего инструмент проверить не может — прочитай сам: закреп сабреддита "
          "и последние 20 постов. Если постов «я сделал штуку» там нет — "
          "значит их снимают, независимо от правил.")


def cmd_sub_add(rd: Reddit, args):
    sub = norm_sub(args.subreddit)
    rd.conn.execute(
        "INSERT INTO subreddits(name,note,added_at) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET note=COALESCE(excluded.note,note)",
        (sub, args.note, time.time()),
    )
    rd.conn.commit()
    print(f"✓ r/{sub} в рабочем списке")


def cmd_sub_list(rd: Reddit, args):
    rows = rd.conn.execute(
        "SELECT * FROM subreddits ORDER BY COALESCE(subscribers,0) DESC"
    ).fetchall()
    if not rows:
        print("список пуст — добавь: sub add <name> или sub check <name>")
        return
    print(f"{'сабреддит':30} {'людей':>7} {'вердикт':9} заметка")
    for r in rows:
        icon = {"ok": "✓", "careful": "⚠️", "no": "✗"}.get(r["verdict"], "·")
        note = (r["verdict_note"] or r["note"] or "")[:60].replace("\n", " ")
        print(f"r/{r['name']:28.28} {fmt_count(r['subscribers']):>7} "
              f"{icon} {(r['verdict'] or '—'):7.7} {note}")


def cmd_threads(rd: Reddit, args):
    """Живые треды по теме — те, где вопрос уже задан и ответ ждут."""
    subs = [norm_sub(s) for s in (args.sub.split(",") if args.sub else [])]
    if not subs:
        rows = rd.conn.execute(
            "SELECT name FROM subreddits WHERE verdict IS NULL OR verdict!='no'"
        ).fetchall()
        subs = [r["name"] for r in rows]
    found = []
    if subs:
        for sub in subs:
            res = rd.get(f"/r/{sub}/search", q=args.query, restrict_sr=1,
                         sort="new", limit=args.limit, t=args.period)
            found += [c["data"] for c in res.get("data", {}).get("children", [])]
    else:
        res = rd.get("/search", q=args.query, sort="new", limit=args.limit,
                     t=args.period)
        found = [c["data"] for c in res.get("data", {}).get("children", [])]

    cutoff = time.time() - args.days * 86400
    found = [d for d in found if d.get("created_utc", 0) >= cutoff]
    found.sort(key=lambda d: d.get("created_utc", 0), reverse=True)
    if not found:
        print("свежих тредов не нашлось — попробуй шире запрос или больше --days")
        return
    for d in found:
        print(f"\n[{ago(d.get('created_utc'))}] r/{d['subreddit']} · "
              f"↑{d.get('score')} 💬{d.get('num_comments')}")
        print(f"  {d.get('title')}")
        body = (d.get("selftext") or "").strip().replace("\n", " ")
        if body:
            print(f"  {body[:200]}")
        print(f"  t3_{d['id']}  https://reddit.com{d.get('permalink')}")
    print(f"\n{len(found)} тредов. Отвечать: "
          f"reddit.py comment t3_<id> --text \"…\"")


def _resolve_flair(rd: Reddit, sub: str, wanted: str | None):
    if not wanted:
        return None, None
    flairs = rd.get(f"/r/{sub}/api/link_flair_v2")
    if not flairs:  # у старого сайта ручка называется иначе
        flairs = rd.get(f"/r/{sub}/api/link_flair")
    if not flairs:
        return None, wanted
    for fl in flairs or []:
        if (fl.get("text") or "").strip().lower() == wanted.strip().lower():
            return fl.get("id"), fl.get("text")
    available = ", ".join((fl.get("text") or "") for fl in (flairs or [])[:20])
    die(f"флейр «{wanted}» не найден в r/{sub}. Есть: {available}")


def _check_guards(rd: Reddit, sub: str, force: bool) -> list[str]:
    g, conn = rd.cfg["guards"], rd.conn
    problems = []
    last = conn.execute(
        "SELECT created_at FROM posts ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if last:
        hours = (time.time() - last["created_at"]) / 3600
        if hours < g["min_hours_between_posts"]:
            problems.append(
                f"прошлый пост был {hours:.1f} ч назад "
                f"(гард: не чаще {g['min_hours_between_posts']} ч)"
            )
    today = conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE created_at > ?", (time.time() - 86400,)
    ).fetchone()["c"]
    if today >= g["max_posts_per_day"]:
        problems.append(f"за сутки уже {today} постов (гард: {g['max_posts_per_day']})")
    same = conn.execute(
        "SELECT created_at FROM posts WHERE subreddit=? ORDER BY created_at DESC LIMIT 1",
        (sub,),
    ).fetchone()
    if same:
        days = (time.time() - same["created_at"]) / 86400
        if days < g["days_between_same_sub"]:
            problems.append(
                f"в r/{sub} уже постил {days:.0f} дн назад "
                f"(гард: {g['days_between_same_sub']} дн)"
            )
    posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    comments = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    need = g["comment_to_post_ratio"]
    if posts and comments < posts * need:
        problems.append(
            f"соотношение участия {comments} комментариев на {posts} постов "
            f"(цель — {need}:1; у Reddit это писаное правило самопиара 9:1)"
        )
    return problems


def cmd_post(rd: Reddit, args):
    spec_path = Path(args.spec)
    if not spec_path.exists():
        die(f"нет файла спеки {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    sub = norm_sub(spec.get("subreddit") or "")
    if not sub:
        die("в спеке нет subreddit")
    kind = spec.get("kind", "self")
    title = (spec.get("title") or "").strip()
    if not title:
        die("в спеке нет title")
    text = spec.get("text")
    if spec.get("text_file"):
        tf = (spec_path.parent / spec["text_file"]).resolve()
        if not tf.exists():
            die(f"нет файла текста {tf}")
        text = tf.read_text(encoding="utf-8")
    url = spec.get("url")
    if kind == "self" and not text:
        die("для kind=self нужен text или text_file")
    if kind == "link" and not url:
        die("для kind=link нужен url")

    problems = _check_guards(rd, sub, args.force)
    if problems:
        print("Гарды говорят подождать:")
        for p in problems:
            print(f"  • {p}")
        if not args.force:
            die("публикация остановлена. Осознанно перебить — флаг --force")
        print("  → --force, продолжаю\n")

    flair_id, flair_text = _resolve_flair(rd, sub, spec.get("flair"))

    print(f"r/{sub} · {kind} · {title}")
    if text:
        print("─" * 60)
        print(text[:1500] + ("…" if len(text) > 1500 else ""))
        print("─" * 60)
    if url:
        print(f"ссылка: {url}")
    if flair_text:
        print(f"флейр: {flair_text}")
    if args.dry:
        print("\n(--dry, ничего не отправлено)")
        return

    data = {
        "api_type": "json",
        "sr": sub,
        "kind": kind,
        "title": title,
        "nsfw": "true" if spec.get("nsfw") else "false",
        "sendreplies": "true" if spec.get("send_replies", True) else "false",
        "resubmit": "true",
    }
    if kind == "self":
        data["text"] = text
    else:
        data["url"] = url
    if flair_id:
        data["flair_id"] = flair_id
        if flair_text:
            data["flair_text"] = flair_text

    res = rd.post_form("/api/submit", **data)
    payload = res.get("json", {})
    errors = payload.get("errors") or []
    if errors:
        for err in errors:
            print(f"✗ {' · '.join(str(x) for x in err)}")
        die("Reddit отклонил пост")
    d = payload.get("data", {})
    post_id = d.get("name") or (f"t3_{d.get('id')}" if d.get("id") else None)
    link = d.get("url")
    rd.conn.execute(
        "INSERT OR REPLACE INTO posts(id,subreddit,title,kind,url,permalink,"
        "created_at) VALUES(?,?,?,?,?,?,?)",
        (post_id, sub, title, kind, url, link, time.time()),
    )
    rd.conn.commit()
    print(f"\n✓ опубликовано: {link}")
    print(f"  id: {post_id}")
    print("\nЧерез 10–15 минут обязательно: reddit.py verify --all")
    print("Пост может быть снят молча — тебе он будет виден, остальным нет.")


def cmd_comment(rd: Reddit, args):
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        die("нужен --text или --file")
    thing = args.thing_id
    if not thing.startswith(("t1_", "t3_")):
        die("thing_id должен начинаться с t3_ (пост) или t1_ (комментарий)")
    if args.dry:
        print(f"→ {thing}\n{'─' * 60}\n{text}\n{'─' * 60}\n(--dry)")
        return
    res = rd.post_form("/api/comment", api_type="json", thing_id=thing, text=text)
    payload = res.get("json", {})
    if payload.get("errors"):
        for err in payload["errors"]:
            print(f"✗ {' · '.join(str(x) for x in err)}")
        die("комментарий не прошёл")
    things = payload.get("data", {}).get("things", [])
    if not things:
        die(f"неожиданный ответ: {res}")
    c = things[0]["data"]
    rd.conn.execute(
        "INSERT OR REPLACE INTO comments(id,parent_id,subreddit,body,permalink,"
        "created_at) VALUES(?,?,?,?,?,?)",
        (c.get("name"), thing, c.get("subreddit"), text,
         c.get("permalink"), time.time()),
    )
    rd.conn.commit()
    print(f"✓ https://reddit.com{c.get('permalink', '')}")


def cmd_verify(rd: Reddit, args):
    """Жив ли пост. Автору снятый пост виден как обычный — смотрим со стороны."""
    conn = rd.conn
    if args.id:
        rows = conn.execute("SELECT * FROM posts WHERE id=?", (args.id,)).fetchall()
    elif args.all:
        rows = conn.execute(
            "SELECT * FROM posts WHERE alive IS NULL OR alive=1 "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM posts ORDER BY created_at DESC LIMIT 1"
        ).fetchall()
    if not rows:
        print("нечего проверять")
        return
    for r in rows:
        d = rd.info(r["id"])
        if d is None:
            print(f"✗  r/{r['subreddit']} · пост не найден (удалён) · {r['title'][:50]}")
            conn.execute(
                "UPDATE posts SET alive=0,removed_by='deleted',checked_at=? WHERE id=?",
                (time.time(), r["id"]),
            )
            continue

        removed_by = d.get("removed_by_category")
        score, ncom = d.get("score"), d.get("num_comments")
        listed = rd.in_listing(r["subreddit"], r["id"],
                               d.get("created_utc") or r["created_at"])

        if removed_by or d.get("selftext") == "[removed]":
            alive, why = 0, removed_by or "removed"
        elif listed is False:
            alive, why = 0, "нет в ленте /new — тихое снятие фильтром"
        elif listed is True:
            alive, why = 1, None
        else:
            alive, why = None, "в ленте не докрутили — проверь глазами"

        conn.execute(
            "UPDATE posts SET alive=?,removed_by=?,checked_at=?,score=?,"
            "num_comments=? WHERE id=?",
            (alive, why, time.time(), score, ncom, r["id"]),
        )

        head = f"r/{r['subreddit']} · ↑{score} 💬{ncom} · {r['title'][:50]}"
        if alive == 1:
            print(f"✓  {head}")
        elif alive == 0:
            print(f"✗  {head}\n   СНЯТ: {why}")
            print("   Что делать: написать модераторам сабреддита вежливое "
                  "сообщение с вопросом, что поправить. Часто разрешают "
                  "перезалить — с флейром, без ссылки или в другом виде.")
        else:
            print(f"?  {head}\n   {why}: https://reddit.com{d.get('permalink', '')}")
    conn.commit()


def cmd_inbox(rd: Reddit, args):
    path = "/message/unread" if args.unread else "/message/inbox"
    res = rd.get(path, limit=args.limit)
    items = [c["data"] for c in res.get("data", {}).get("children", [])]
    if not items:
        print("пусто")
        return
    for d in items:
        kind = d.get("type") or "message"
        print(f"\n[{ago(d.get('created_utc'))}] {kind} от u/{d.get('author')} "
              f"· r/{d.get('subreddit')}")
        print(f"  {(d.get('body') or '').strip()[:300]}")
        if d.get("name"):
            print(f"  ответить: comment {d['name']} --text \"…\"")


def cmd_stats(rd: Reddit, args):
    conn = rd.conn
    posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    alive = conn.execute("SELECT COUNT(*) c FROM posts WHERE alive=1").fetchone()["c"]
    dead = conn.execute("SELECT COUNT(*) c FROM posts WHERE alive=0").fetchone()["c"]
    comments = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    subs = conn.execute("SELECT COUNT(*) c FROM subreddits").fetchone()["c"]
    print(f"постов      : {posts} (живых {alive}, снято {dead})")
    print(f"комментариев: {comments}")
    print(f"сабреддитов : {subs}")
    ratio = rd.cfg["guards"]["comment_to_post_ratio"]
    if posts:
        print(f"участие     : {comments / posts:.1f} комментариев на пост "
              f"(цель {ratio}:1)")
        if comments < posts * ratio:
            print("  ⚠️  перекос в сторону своих постов — на Reddit это ровно то, "
                  "за что снимают и банят")
    for r in conn.execute(
        "SELECT subreddit, COUNT(*) c, SUM(COALESCE(alive,1)) a FROM posts "
        "GROUP BY subreddit ORDER BY c DESC"
    ):
        print(f"  r/{r['subreddit']:28.28} {r['c']} постов, живых {r['a']}")


def cmd_plan(rd: Reddit, args):
    g = rd.cfg["guards"]
    conn = rd.conn
    today = conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE created_at > ?", (time.time() - 86400,)
    ).fetchone()["c"]
    print(f"постов за сутки: {today} из {g['max_posts_per_day']}")
    last = conn.execute(
        "SELECT subreddit, created_at FROM posts ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if last:
        hours = (time.time() - last["created_at"]) / 3600
        left = g["min_hours_between_posts"] - hours
        print(f"прошлый пост   : {ago(last['created_at'])} в r/{last['subreddit']}"
              + (f" · следующий можно через {left:.1f} ч" if left > 0 else
                 " · пауза выдержана"))
    rows = conn.execute(
        "SELECT name, verdict, checked_at FROM subreddits ORDER BY name"
    ).fetchall()
    ready, waiting = [], []
    for r in rows:
        same = conn.execute(
            "SELECT created_at FROM posts WHERE subreddit=? "
            "ORDER BY created_at DESC LIMIT 1", (r["name"],)
        ).fetchone()
        if same:
            days = (time.time() - same["created_at"]) / 86400
            if days < g["days_between_same_sub"]:
                waiting.append(f"r/{r['name']} (ещё {g['days_between_same_sub'] - days:.0f} дн)")
                continue
        if r["verdict"] == "no":
            continue
        ready.append(f"r/{r['name']}" + (" ⚠️" if r["verdict"] == "careful" else ""))
    print("\nсвободны сейчас: " + (", ".join(ready) if ready else "—"))
    if waiting:
        print("в паузе        : " + ", ".join(waiting))
    comments = conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"]
    posts = conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"]
    need = posts * g["comment_to_post_ratio"] - comments
    if need > 0:
        print(f"\nдля соотношения {g['comment_to_post_ratio']}:1 не хватает "
              f"{need} комментариев — их и стоит сделать до следующего поста")


# ──────────────────────────────── разбор аргументов ────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reddit.py",
        description="Reddit: разведка сабреддитов, участие в тредах, публикация",
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("login", help="войти руками в открывшемся Chrome (один раз)")
    sp.add_parser("me", help="аккаунт: карма, возраст, готовность к постингу")

    f = sp.add_parser("find", help="искать сабреддиты по теме")
    f.add_argument("query")
    f.add_argument("--limit", type=int, default=25)

    sub = sp.add_parser("sub", help="работа с сабреддитом")
    subsp = sub.add_subparsers(dest="subcmd", required=True)
    si = subsp.add_parser("info", help="о сабреддите")
    si.add_argument("subreddit")
    si.add_argument("--flairs", action="store_true", help="показать флейры")
    sr = subsp.add_parser("rules", help="правила текстом")
    sr.add_argument("subreddit")
    sc = subsp.add_parser("check", help="преполёт: можно ли мне сюда постить")
    sc.add_argument("subreddit")
    sc.add_argument("--kind", choices=["self", "link"], default="self")
    sa = subsp.add_parser("add", help="в рабочий список")
    sa.add_argument("subreddit")
    sa.add_argument("--note")
    subsp.add_parser("list", help="рабочий список")

    t = sp.add_parser("threads", help="живые треды по теме — кому отвечать")
    t.add_argument("query")
    t.add_argument("--sub", help="через запятую; по умолчанию — рабочий список")
    t.add_argument("--days", type=int, default=7)
    t.add_argument("--limit", type=int, default=25)
    t.add_argument("--period", default="month",
                   choices=["hour", "day", "week", "month", "year", "all"])

    po = sp.add_parser("post", help="опубликовать пост из файла-спеки")
    po.add_argument("spec")
    po.add_argument("--dry", action="store_true")
    po.add_argument("--force", action="store_true", help="перебить гарды")

    co = sp.add_parser("comment", help="ответить в тред")
    co.add_argument("thing_id")
    co.add_argument("--text")
    co.add_argument("--file")
    co.add_argument("--dry", action="store_true")

    v = sp.add_parser("verify", help="жив ли пост (тихое удаление)")
    v.add_argument("--id")
    v.add_argument("--all", action="store_true")

    i = sp.add_parser("inbox", help="ответы мне")
    i.add_argument("--unread", action="store_true")
    i.add_argument("--limit", type=int, default=25)

    sp.add_parser("stats", help="свои посты/комментарии, соотношение 9:1")
    sp.add_parser("plan", help="что гарды разрешают сегодня")
    return p


HANDLERS = {
    "login": cmd_login,
    "me": cmd_me,
    "find": cmd_find,
    "threads": cmd_threads,
    "post": cmd_post,
    "comment": cmd_comment,
    "verify": cmd_verify,
    "inbox": cmd_inbox,
    "stats": cmd_stats,
    "plan": cmd_plan,
}
SUB_HANDLERS = {
    "info": cmd_sub_info,
    "rules": cmd_sub_rules,
    "check": cmd_sub_check,
    "add": cmd_sub_add,
    "list": cmd_sub_list,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = db()
    # Команды, которым сеть не нужна, работают и без конфига.
    offline = args.cmd in ("stats", "plan") or (
        args.cmd == "sub" and args.subcmd in ("list", "add")
    )
    needs_net = not offline
    cfg = load_config() if needs_net else {"guards": dict(DEFAULT_GUARDS)}
    rd = Reddit(cfg, conn) if needs_net else _Offline(cfg, conn)
    try:
        if args.cmd == "sub":
            SUB_HANDLERS[args.subcmd](rd, args)
        else:
            HANDLERS[args.cmd](rd, args)
    finally:
        Reddit.stop_server()


class _Offline:
    """Заглушка клиента для команд, которым сеть не нужна."""

    def __init__(self, cfg, conn):
        self.cfg = cfg
        self.conn = conn


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nпрервано", file=sys.stderr)
        sys.exit(130)
