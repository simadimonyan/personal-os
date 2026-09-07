#!/usr/bin/env python3
"""
Mission Control — единый дашборд Personal OS.

Локальный веб-пульт (только 127.0.0.1, stdlib, без зависимостей): показывает живое
состояние супервизора и сервисов, расписание и статусы крон-задач, ленту запусков,
сводку по hh-откликам и блогу. Кнопки реально управляют системой через cronctl.py и
pos.py: запустить задачу сейчас, включить/выключить, перезапустить супервизор.

Запуск:
  python3 server.py                 # http://127.0.0.1:8787
  PORT=9000 python3 server.py       # другой порт

Под супервизором pos.py добавляется в services.json как сервис "mission-control".
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # …/services
CRON = BASE / "cron"
PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
PORT = int(os.environ.get("PORT", "8787"))

JOBS_FILE = CRON / "jobs.json"
STATE_FILE = CRON / "state.json"
RUNS_LOG = CRON / "logs" / "runs.log"
CRON_LOG = CRON / "logs" / "cron.log"
POS_STATE = BASE / "pos_state.json"
SERVICES_FILE = BASE / "services.json"
POS_LOG = BASE / "logs" / "pos.log"
APPLIED = BASE / "hh-autoapply" / "applied.json"
HH_CONFIG = BASE / "hh-autoapply" / "config.json"
BLOG_DB = BASE / "tg-blog-editor" / "data" / "queue.db"
PUBLISHED = BASE / "tg-blog-editor" / "data" / "published.json"
CRONCTL = CRON / "cronctl.py"
POS = BASE / "pos.py"

# Claude Code из-под UI
PROJECT_DIR = BASE.parent                               # …/personal os
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
CLAUDE_SESSIONS = Path(__file__).resolve().parent / "claude_sessions.json"
MODELS = {"haiku", "sonnet", "opus"}
PERMS = {"plan", "acceptEdits", "full"}

# Истории сессий Claude Code (jsonl) и лента local-API
CC_PROJECT_DIR = Path.home() / ".claude" / "projects" / "-Users-dimitrisimonyan-Desktop-personal-os"
LOCALAPI_LOG = BASE / "logs" / "localapi-activity.jsonl"
PSYCH_DB = BASE / "tg-assistant" / "data" / "assistant.db"
RADAR_DB = BASE / "radar" / "radar.db"
RADAR_SOURCES = BASE / "radar" / "sources.json"

# Граф векторных связей (L4-память) — tools/obsidian/tools/graph/
VSEARCH = PROJECT_DIR / "tools" / "obsidian" / "tools" / "obsidian_vsearch.py"
GRAPH_DIR = VSEARCH.parent / "graph"
GRAPH_HTML = GRAPH_DIR / "graph.html"
GRAPH_JSON = GRAPH_DIR / "graph.json"

# Векторная библиотека Zotero — tools/zotero/
ZOTERO_VEC = PROJECT_DIR / "tools" / "zotero" / "vectorize.py"
ZOTERO_STATUS = ZOTERO_VEC.parent / "status.json"
ZOTERO_GRAPH_HTML = ZOTERO_VEC.parent / "graph" / "graph.html"
ZOTERO_GRAPH_JSON = ZOTERO_VEC.parent / "graph" / "graph.json"

# Единая база источников (origin-tagged) — tools/sources/
# Физически та же БД, что у Zotero; отличие — колонка origin и граф по origin.
SOURCES_INGEST = PROJECT_DIR / "tools" / "sources" / "ingest.py"
SOURCES_DB = ZOTERO_VEC.parent / "zotero_vectors.db"
SOURCES_GRAPH_HTML = SOURCES_INGEST.parent / "graph" / "graph.html"
SOURCES_GRAPH_JSON = SOURCES_INGEST.parent / "graph" / "graph.json"

# Социальный капитал — tools/social-capital/ (контакты, связи, граф людей)
CAPITAL_TOOL = PROJECT_DIR / "tools" / "social-capital" / "capital.py"
CAPITAL_DB = CAPITAL_TOOL.parent / "social_capital.db"
CAPITAL_GRAPH_HTML = CAPITAL_TOOL.parent / "graph" / "graph.html"
CAPITAL_GRAPH_JSON = CAPITAL_TOOL.parent / "graph" / "graph.json"

# База блога (Base B) — tools/blog/ (собственный контент + публикации)
BLOGBASE_STORE = PROJECT_DIR / "tools" / "blog" / "store.py"
BLOGBASE_DB = BLOGBASE_STORE.parent / "blog.db"

# Хранилище визуальных референсов — код в tools/context-store/, данные в Obsidian
VAULT = Path("/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/"
             "Knowledge base/Obsidian/Органон")
REFS_TOOL = PROJECT_DIR / "tools" / "context-store" / "store.py"
REFS_ROOT = VAULT / "09 — Шаблоны и ресурсы" / "Визуальные референсы"


# ── чтение состояния ──────────────────────────────────────────────────────────
def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _fmt_uptime(s: float | int | None) -> str:
    if not s:
        return "—"
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}д {h}ч"
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def _next_run(schedule: str, last_run: float | None) -> str:
    """Грубая оценка следующего запуска для форм '@every Nu' и 'M H * * D'."""
    now = dt.datetime.now()
    m = re.match(r"@every\s+(\d+)([smhd])", schedule.strip())
    if m:
        n, u = int(m.group(1)), m.group(2)
        secs = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
        base = dt.datetime.fromtimestamp(last_run) if last_run else now
        nxt = base + dt.timedelta(seconds=secs)
        while nxt < now:
            nxt += dt.timedelta(seconds=secs)
        return nxt.strftime("%H:%M")
    parts = schedule.split()
    if len(parts) == 5:
        minute, hour, _, _, dow = parts
        try:
            mi = int(minute); hh = int(hour)
        except ValueError:
            return "—"
        dows = None
        if dow != "*":
            # cron: 0=вс..6=сб; python weekday: 0=пн..6=вс
            cron_to_py = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
            dows = {cron_to_py[int(x)] for x in dow.split(",") if x.isdigit()}
        for add in range(0, 8):
            cand = (now + dt.timedelta(days=add)).replace(hour=hh, minute=mi, second=0, microsecond=0)
            if cand <= now:
                continue
            if dows is None or cand.weekday() in dows:
                if add == 0:
                    return cand.strftime("сегодня %H:%M")
                if add == 1:
                    return cand.strftime("завтра %H:%M")
                return cand.strftime("%a %H:%M")
    return "—"


def _job_health(s: dict) -> str:
    """Честный статус задачи по полям state.json (правило degraded из B1/T2).

    Возвращает: disabled | waiting | error | degraded | ok | unknown.
      - disabled  — задача выключена (enabled=false); решается в build_jobs.
      - waiting   — последний прогон отложен (нет ресурса: internet/claude).
      - error     — последний прогон упал (last_status=="error").
      - degraded  — last_status=="ok", НО есть СВЕЖАЯ ошибка текущего запуска
                    (last_error непустой и last_error_run_ts == last_run).
                    После фикса core.py (T1) успех чистит last_error, поэтому
                    непустой last_error при ok означает только свежее падение.
      - ok        — успешный последний прогон без следов ошибки.
      - unknown   — ещё не запускалась (нет last_status).
    """
    status = s.get("last_status")
    if status == "deferred":
        return "waiting"
    if status == "error":
        return "error"
    if status == "ok":
        err = (s.get("last_error") or "").strip()
        if err:
            # Свежесть: ошибка считается актуальной, только если она привязана к
            # последнему запуску. Это страховка на случай стейл-ошибки в старых
            # state.json, записанных до фикса core.py (T1).
            err_ts = s.get("last_error_run_ts")
            last_run = s.get("last_run")
            if err_ts is None or last_run is None or err_ts == last_run:
                return "degraded"
        return "ok"
    if not status or status == "—":
        return "unknown"
    return status


def build_jobs() -> list[dict]:
    """Список крон-задач с вычисленным честным health (T2/T5).

    Вынесено из gather() в отдельную функцию, чтобы service_detail("cron")
    не пересобирал весь gather() ради jobs (P3/T5).
    """
    jobs = _load(JOBS_FILE, [])
    st = _load(STATE_FILE, {})
    cron_jobs = []
    for j in jobs:
        s = st.get(j["id"], {})
        lr = s.get("last_run")
        enabled = bool(j.get("enabled", True))
        health = "disabled" if not enabled else _job_health(s)
        cron_jobs.append({
            "id": j["id"],
            "schedule": j["schedule"],
            "enabled": enabled,
            "description": j.get("description", ""),
            "status": s.get("last_status", "—"),
            "health": health,
            "last_run": dt.datetime.fromtimestamp(lr).strftime("%d.%m %H:%M") if lr else "—",
            "last_run_ts": lr,
            "last_ms": s.get("last_ms"),
            "last_error": (s.get("last_error") or "")[:600],
            "missing": s.get("last_missing"),
            "next_run": _next_run(j["schedule"], lr),
            "is_example": j["id"].startswith("example-"),
        })
    return cron_jobs


def build_services() -> list[dict]:
    """Список сервисов с флагом degraded (restarts>0) — T3."""
    pos = _load(POS_STATE, {})
    svc_defs = {s["name"]: s for s in _load(SERVICES_FILE, {}).get("services", [])}
    services = []
    for name, info in pos.get("services", {}).items():
        restarts = info.get("restarts", 0) or 0
        alive = bool(info.get("alive"))
        services.append({
            "name": name,
            "alive": alive,
            "pid": info.get("pid"),
            "uptime": _fmt_uptime(info.get("uptime_s")),
            "restarts": restarts,
            "degraded": (restarts > 0) and alive,  # флаг, alive не трогаем
            "note": (svc_defs.get(name, {}) or {}).get("note", ""),
        })
    return services


# Серьёзность health/severity для max-агрегации и сортировки.
_SEVERITY_RANK = {"ok": 0, "info": 1, "warn": 2, "error": 3}
# overall системы — словарь состояний (ok|degraded|error).
_OVERALL_FROM_SEVERITY = {0: "ok", 1: "degraded", 2: "degraded", 3: "error"}


def _error_essence(err: str) -> str:
    """Однострочная человекочитаемая суть ошибки из traceback/лога.

    Цель — чтобы по строке было ясно «что именно сломалось», без чтения
    всего стек-трейса. Используется в карточках «Требует внимания».
    """
    e = (err or "").strip()
    if not e:
        return "причина не записана"
    m = re.search(r"timed out after (\d+)\s*seconds?", e)
    if m:
        sec = int(m.group(1))
        human = f"{sec // 60} мин" if sec >= 60 else f"{sec} с"
        return f"таймаут — не уложилась за {human}"
    # последняя строка traceback — это, как правило, сам класс исключения
    if "Traceback" in e:
        last = [ln.strip() for ln in e.splitlines() if ln.strip()]
        tail = last[-1] if last else ""
        m2 = re.match(r"([A-Za-z_][\w.]*(?:Error|Exception|Timeout|Warning)):?\s*(.*)", tail)
        if m2:
            msg = (m2.group(2) or "").strip()
            return f"{m2.group(1)}" + (f": {msg[:80]}" if msg else "")
        return "исключение внутри (traceback)"
    m3 = re.search(r"shell exit (\d+)", e)
    if m3:
        return f"шаг упал (exit {m3.group(1)})"
    m4 = re.search(r"exit(?:ed)?\s*(?:code\s*)?(\d+)", e, re.I)
    if m4 and m4.group(1) != "0":
        return f"завершилась с кодом {m4.group(1)}"
    if e.startswith("Command "):
        return "внешняя команда упала (подробности — в логе/ошибке)"
    first = e.splitlines()[0]
    return first[:90]


def build_attention(services: list[dict], jobs: list[dict]) -> list[dict]:
    """Производный список «что требует внимания прямо сейчас» (T3, UX §2.3).

    Каждый элемент: {kind, severity, entity_id, reason, error, actions[]}.
      kind     — service | job
      severity — error | warn | info
      reason   — короткая человекочитаемая причина (для заголовка строки)
      error    — текст ошибки (traceback), если есть; иначе ""
      actions  — допустимые действия фронта: run|restart|restart_global|toggle_off|show_error
    near_timeout НЕ включаем (нет per-job лимита — уходит в Next, C2).
    """
    items: list[dict] = []

    for s in services:
        if not s["alive"]:
            items.append({
                "kind": "service", "severity": "error", "entity_id": s["name"],
                "reason": "не запущен — сервис не работает совсем",
                "hint": "Нажми «перезапустить»: супервизор поднимет процесс заново.",
                "error": "",
                "actions": ["restart", "log"],
            })
        elif s.get("degraded"):
            n = s["restarts"]
            items.append({
                "kind": "service", "severity": "info", "entity_id": s["name"],
                "reason": f"восстановился сам после {n}× перезапуск(ов) — сейчас работает",
                "hint": "Это история, не текущая поломка. Если перезапуски частят — глянь лог.",
                "error": "",
                "actions": ["log"],
            })

    for j in jobs:
        if j["is_example"] or not j["enabled"]:
            continue
        h = j["health"]
        if h == "error":
            items.append({
                "kind": "job", "severity": "error", "entity_id": j["id"],
                "reason": f"упала: {_error_essence(j['last_error'])}",
                "hint": "Прогон завершился ошибкой — задача НЕ выполнена.",
                "error": j["last_error"],
                "actions": ["run", "show_error", "log", "toggle_off"],
            })
        elif h == "degraded":
            items.append({
                "kind": "job", "severity": "warn", "entity_id": j["id"],
                "reason": f"вернула «успех», но внутри упала: {_error_essence(j['last_error'])}",
                "hint": "Код выхода 0, но шаг внутри сломался — работа, скорее всего, не сделана. Прогони заново или открой ошибку.",
                "error": j["last_error"],
                "actions": ["run", "show_error", "log", "toggle_off"],
            })
        elif h == "waiting":
            miss = j.get("missing") or "ресурс"
            items.append({
                "kind": "job", "severity": "info", "entity_id": j["id"],
                "reason": f"ждёт ресурс: {miss}",
                "hint": "Это норма: запустится сама, когда ресурс появится. Можно прогнать вручную.",
                "error": "",
                "actions": ["run"],
            })

    # сортировка: error → warn → info, внутри уровня — стабильный исходный порядок
    items.sort(key=lambda it: _SEVERITY_RANK.get(it["severity"], 0), reverse=True)
    return items


def gather() -> dict:
    jobs = _load(JOBS_FILE, [])

    services = build_services()
    cron_jobs = build_jobs()
    attention = build_attention(services, cron_jobs)

    # сводное здоровье системы (T3): overall = max-серьёзность по attention.
    max_sev = max((_SEVERITY_RANK.get(a["severity"], 0) for a in attention), default=0)
    overall = _OVERALL_FROM_SEVERITY[max_sev]
    health = {"overall": overall, "attention_count": len(attention)}

    # лента запусков (последние 25)
    feed = []
    try:
        for line in RUNS_LOG.read_text(encoding="utf-8").splitlines()[-25:][::-1]:
            cols = line.split("\t")
            if len(cols) >= 3:
                feed.append({"ts": cols[0], "result": cols[1], "job": cols[2],
                             "detail": (cols[3] if len(cols) > 3 else "")[:80]})
    except Exception:
        pass

    # сводка hh / блог
    applied = _load(APPLIED, [])
    blog = {"pending": 0, "published": 0, "skipped": 0}
    try:
        conn = sqlite3.connect(f"file:{BLOG_DB}?mode=ro", uri=True)
        for status, cnt in conn.execute("select status,count(*) from drafts group by status"):
            blog[status] = cnt
        conn.close()
    except Exception:
        pass
    published_total = len(_load(PUBLISHED, []))

    pos = _load(POS_STATE, {})
    # jobs_error теперь честный: считает и error, и degraded (ложно-зелёные) —
    # ровно те, что попадают в attention с серьёзностью >= warn по задачам.
    jobs_bad = len([j for j in cron_jobs
                    if j["health"] in ("error", "degraded") and not j["is_example"]])
    return {
        "now": dt.datetime.now().strftime("%H:%M:%S"),
        "supervisor": {"pid": pos.get("supervisor_pid"), "updated": pos.get("updated")},
        "health": health,
        "attention": attention,
        "services": services,
        "jobs": cron_jobs,
        "feed": feed,
        "stats": {
            "hh_applied": len(applied) if isinstance(applied, list) else 0,
            "blog_pending": blog.get("pending", 0),
            "blog_published": published_total,
            "blog_skipped": blog.get("skipped", 0),
            "jobs_total": len([j for j in jobs if not j["id"].startswith("example-")]),
            "jobs_error": jobs_bad,
            "services_alive": sum(1 for s in services if s["alive"]),
            "services_total": len(services),
            "mc_sessions": len(claude_sessions()),
        },
    }


# ── управление (cronctl / pos) ────────────────────────────────────────────────
def _run(cmd: list[str], background: bool = False) -> dict:
    try:
        if background:
            subprocess.Popen(cmd, cwd=str(BASE),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "out": "запущено в фоне"}
        p = subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True, timeout=30)
        return {"ok": p.returncode == 0, "out": (p.stdout or p.stderr).strip()[:400]}
    except Exception as e:
        return {"ok": False, "out": str(e)[:400]}


def cron_run(job_id: str) -> dict:
    return _run([PYTHON, str(CRONCTL), "run", job_id], background=True)


def cron_toggle(job_id: str, enabled: bool) -> dict:
    return _run([PYTHON, str(CRONCTL), "enable" if enabled else "disable", job_id])


def service_restart(name: str | None = None) -> dict:
    """Рестарт сервиса.

    name задан  → точечный рестарт ОДНОГО сервиса (`pos.py restart <name>`):
        бьёт только его PID, супервизор поднимает обратно. Остальные не трогаются.
    name пуст   → глобальный рестарт супервизора (`pos.py restart`): перезапустит
        ВСЕ сервисы. Фронт обязан показать confirm для этого варианта.
    """
    if name:
        # точечный рестарт быстрый и синхронный — вернём реальный результат pos.py,
        # чтобы фронт честно показал успех/ошибку конкретной операции.
        return _run([PYTHON, str(POS), "restart", name], background=False)
    return _run([PYTHON, str(POS), "restart"], background=True)


# ── граф векторных связей (L4) ────────────────────────────────────────────────
_graph_meta_cache: dict = {"mtime": None, "meta": {}}


def graph_meta() -> dict:
    """Сводка по графу из graph.json (meta), кэш по mtime (файл ~11 МБ)."""
    if not GRAPH_JSON.exists():
        return {"exists": False, "html": GRAPH_HTML.exists()}
    mt = GRAPH_JSON.stat().st_mtime
    if _graph_meta_cache["mtime"] != mt:
        try:
            data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
            _graph_meta_cache.update(mtime=mt, meta=data.get("meta", {}) or {})
        except Exception:
            _graph_meta_cache.update(mtime=mt, meta={})
    m = _graph_meta_cache["meta"]
    built = m.get("built_at")
    return {
        "exists": True,
        "html": GRAPH_HTML.exists(),
        "notes": m.get("total_notes"),
        "chunks": m.get("total_chunks"),
        "edges": m.get("total_edges"),
        "threshold": m.get("threshold"),
        "built_at": dt.datetime.fromtimestamp(built).strftime("%d.%m %H:%M") if built else "—",
        "size_mb": round(GRAPH_HTML.stat().st_size / 1048576, 1) if GRAPH_HTML.exists() else 0,
    }


def graph_rebuild() -> dict:
    """Перестроить graph.json + graph.html из текущего векторного индекса."""
    if not VSEARCH.exists():
        return {"ok": False, "out": "obsidian_vsearch.py не найден"}
    return _run([PYTHON, str(VSEARCH), "build_graph"], background=True)


def zotero_meta() -> dict:
    """Статус векторной библиотеки Zotero: свежие счётчики из БД + метаданные
    последнего refresh (status.json) + сводка по графу."""
    st = _load(ZOTERO_STATUS, {}) if ZOTERO_STATUS.exists() else {}
    counts = {}
    zdb = ZOTERO_VEC.parent / "zotero_vectors.db"
    if zdb.exists():
        try:
            con = sqlite3.connect(f"file:{zdb}?mode=ro", uri=True, timeout=2)
            # БД общая с базой источников — считаем только origin='zotero'
            # (собранные ссылки живут тут же с другим origin, их сюда не мешаем).
            has = any(c[1] == "origin" for c in con.execute("PRAGMA table_info(books)"))
            zf = "WHERE COALESCE(b.origin,'zotero')='zotero'" if has else ""
            counts["books"] = con.execute(
                f"SELECT COUNT(*) FROM books b {zf}").fetchone()[0]
            # книги отдельно от прочих ресурсов (статьи, веб-ссылки, видео, блоги)
            bf = (zf + " AND b.item_type='book'") if zf else "WHERE b.item_type='book'"
            counts["book_count"] = con.execute(
                f"SELECT COUNT(*) FROM books b {bf}").fetchone()[0]
            counts["annotations"] = con.execute(
                f"SELECT COUNT(*) FROM annotations a "
                f"JOIN books b ON a.book_id=b.book_id {zf}").fetchone()[0]
            counts["chunks"] = con.execute(
                f"SELECT COUNT(*) FROM chunks c "
                f"JOIN books b ON c.book_id=b.book_id {zf}").fetchone()[0]
            counts["db_mb"] = round(zdb.stat().st_size / 1e6, 1)
            con.close()
        except Exception:
            counts = {}
    gm = {}
    if ZOTERO_GRAPH_JSON.exists():
        try:
            gm = (json.loads(ZOTERO_GRAPH_JSON.read_text(encoding="utf-8"))
                  .get("meta", {}) or {})
        except Exception:
            gm = {}
    return {
        "exists": bool(counts) or bool(st) or ZOTERO_GRAPH_HTML.exists(),
        "books": counts.get("books", st.get("books")),
        "book_count": counts.get("book_count"),
        "annotations": counts.get("annotations", st.get("annotations")),
        "chunks": counts.get("chunks", st.get("chunks")),
        "db_mb": counts.get("db_mb", st.get("db_mb")),
        "updated": st.get("updated", "—"),
        "last_actions": st.get("actions", []),
        "graph_html": ZOTERO_GRAPH_HTML.exists(),
        "graph_edges": gm.get("total_edges"),
        "graph_size_mb": round(ZOTERO_GRAPH_HTML.stat().st_size / 1048576, 1)
        if ZOTERO_GRAPH_HTML.exists() else 0,
    }


def zotero_rebuild() -> dict:
    """Инкрементальный refresh библиотеки Zotero (аннотации+текст+граф)."""
    if not ZOTERO_VEC.exists():
        return {"ok": False, "out": "vectorize.py не найден"}
    return _run([PYTHON, str(ZOTERO_VEC), "refresh"], background=True)


# ── Единая база источников (origin-tagged) ────────────────────────────────────
def sources_meta() -> dict:
    """Разбивка базы источников по origin + сводка по графу (раскраска по origin).
    Под «источниками» здесь — собранные ссылки, а не книги Zotero: библиотека
    исключена (у неё своя карточка «Библиотека · Zotero» и свой граф)."""
    origins, total_books, total_chunks = [], 0, 0
    if SOURCES_DB.exists():
        try:
            con = sqlite3.connect(f"file:{SOURCES_DB}?mode=ro", uri=True, timeout=2)
            has = any(c[1] == "origin" for c in con.execute("PRAGMA table_info(books)"))
            oexpr = "COALESCE(b.origin,'zotero')" if has else "'zotero'"
            for o, nb, nc in con.execute(
                f"""SELECT {oexpr} o, COUNT(DISTINCT b.book_id) nb, COUNT(c.id) nc
                      FROM books b LEFT JOIN chunks c ON c.book_id = b.book_id
                     WHERE {oexpr} != 'zotero'
                     GROUP BY o ORDER BY nb DESC"""):
                origins.append({"origin": o, "books": nb, "chunks": nc})
                total_books += nb
                total_chunks += nc
            con.close()
        except Exception:
            origins = []
    gm = {}
    if SOURCES_GRAPH_JSON.exists():
        try:
            gm = (json.loads(SOURCES_GRAPH_JSON.read_text(encoding="utf-8"))
                  .get("meta", {}) or {})
        except Exception:
            gm = {}
    built = gm.get("built_at")
    return {
        "exists": bool(origins) or SOURCES_GRAPH_HTML.exists(),
        "origins": origins,
        "total_books": total_books,
        "total_chunks": total_chunks,
        "graph_html": SOURCES_GRAPH_HTML.exists(),
        "graph_nodes": gm.get("total_notes"),
        "graph_edges": gm.get("total_edges"),
        "graph_built": _fmt_ts(built) if built else "—",
        "graph_size_mb": round(SOURCES_GRAPH_HTML.stat().st_size / 1048576, 1)
        if SOURCES_GRAPH_HTML.exists() else 0,
    }


# ── Гибридный поиск по хранилищам контекста (FTS+вектор, RRF) ──────────────────
def _json_tail(s: str):
    """Достаёт последний валидный JSON-объект из stdout инструмента (модель
    подмешивает свои логи перед итоговым JSON)."""
    dec = json.JSONDecoder()
    best, best_end = None, -1
    for i, ch in enumerate(s or ""):
        if ch == "{":
            try:
                obj, end = dec.raw_decode(s[i:])
            except Exception:
                continue
            if isinstance(obj, dict) and i + end > best_end:
                best, best_end = obj, i + end
    return best


def _ctx_cmd(store, q, mode, limit):
    if store == "obsidian":
        return [PYTHON, str(VSEARCH), "hybrid", q, str(limit), "--mode", mode]
    scope = "zotero" if store == "zotero" else "sources"
    return [PYTHON, str(ZOTERO_VEC), "hybrid", q, str(limit),
            "--scope", scope, "--mode", mode, "--json"]


def ctx_search(store: str, mode: str, q: str, limit: int = 8) -> dict:
    """Прогоняет запрос через нужное хранилище (obsidian / zotero / sources) в
    выбранном режиме (vector / fts / hybrid) и возвращает единый JSON."""
    q = (q or "").strip()
    store = store if store in ("obsidian", "zotero", "sources") else "obsidian"
    mode = mode if mode in ("vector", "fts", "hybrid") else "hybrid"
    try:
        limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        limit = 8
    if not q:
        return {"store": store, "mode": mode, "results": [], "error": "пустой запрос"}
    try:
        p = subprocess.run(_ctx_cmd(store, q, mode, limit), capture_output=True,
                           text=True, timeout=120, cwd=str(PROJECT_DIR))
    except subprocess.TimeoutExpired:
        return {"store": store, "mode": mode, "results": [], "error": "таймаут"}
    data = _json_tail(p.stdout) or {}
    results = data.get("results", [])
    for r in results:
        r["store"] = store
        if store == "obsidian":                 # у Obsidian заголовок = имя файла
            r["title"] = (r.get("path") or "").rsplit("/", 1)[-1].removesuffix(".md")
        else:
            r["title"] = r.get("who")
    err = None if results else (data.get("error") or (p.stderr or "").strip()[-200:] or "нет результатов")
    return {"store": store, "mode": mode, "query": q, "results": results, "error": err}


# ── Социальный капитал (контакты и связи) ─────────────────────────────────────
def capital_meta() -> dict:
    """Сводка базы контактов: сколько людей, по категориям, кому пора написать,
    состояние графа связей. Читаем базу только на чтение — писать в неё может
    capital.py из другой сессии."""
    total, cats, orgs, links, due = 0, [], 0, 0, 0
    if CAPITAL_DB.exists():
        try:
            con = sqlite3.connect(f"file:{CAPITAL_DB}?mode=ro", uri=True, timeout=2)
            total = con.execute(
                "SELECT COUNT(*) FROM contacts WHERE COALESCE(archived,0)=0").fetchone()[0]
            # подкатегории показываем как «Лиды · Тёплые» — иначе в сводке
            # непонятно, к какой группе относится ветка
            has_parent = any(c[1] == "parent"
                             for c in con.execute("PRAGMA table_info(categories)"))
            pexpr = "p.name" if has_parent else "NULL"
            pjoin = "LEFT JOIN categories p ON p.key=c.parent" if has_parent else ""
            cats = [{"key": r[0],
                     "name": f"{r[4]} · {r[1]}" if r[4] else r[1],
                     "color": r[2], "n": r[3]}
                    for r in con.execute(
                        f"""SELECT c.key, c.name, c.color, COUNT(k.id), {pexpr}
                             FROM categories c {pjoin}
                             LEFT JOIN contacts k ON k.category=c.key
                                  AND COALESCE(k.archived,0)=0
                            GROUP BY c.key HAVING COUNT(k.id)>0
                            ORDER BY c.sort""")]
            orgs = con.execute("SELECT COUNT(*) FROM orgs").fetchone()[0]
            links = con.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            due = con.execute(
                "SELECT COUNT(*) FROM contacts WHERE next_touch IS NOT NULL"
                " AND next_touch<=date('now')").fetchone()[0]
            con.close()
        except Exception:
            pass
    gm = {}
    if CAPITAL_GRAPH_JSON.exists():
        try:
            gm = (json.loads(CAPITAL_GRAPH_JSON.read_text(encoding="utf-8"))
                  .get("meta", {}) or {})
        except Exception:
            gm = {}
    built = gm.get("built_at")
    return {
        "exists": bool(total) or CAPITAL_GRAPH_HTML.exists(),
        "contacts": total,
        "categories": cats,
        "orgs": orgs,
        "links": links,
        "due": due,
        "graph_html": CAPITAL_GRAPH_HTML.exists(),
        "graph_nodes": gm.get("total_notes"),
        "graph_edges": gm.get("total_edges"),
        "graph_built": _fmt_ts(built) if built else "—",
    }


def capital_rebuild(sync: bool = False) -> dict:
    """Пересобрать граф связей; с sync=True — сперва подтянуть правки карточек
    Obsidian в базу и раскатать базу обратно в карточки."""
    if not CAPITAL_TOOL.exists():
        return {"ok": False, "out": "tools/social-capital/capital.py не найден"}
    if sync:
        r = _run([PYTHON, str(CAPITAL_TOOL), "sync", "--pull", "--prune"])
        if not r["ok"]:
            return r
    return _run([PYTHON, str(CAPITAL_TOOL), "graph"], background=True)


def sources_rebuild() -> dict:
    """Перестроить граф базы источников (раскраска по origin) в фоне."""
    if not SOURCES_INGEST.exists():
        return {"ok": False, "out": "tools/sources/ingest.py не найден"}
    return _run([PYTHON, str(SOURCES_INGEST), "graph"], background=True)


# ── База блога (Base B) — собственный контент + публикации ────────────────────
def blogbase_admin() -> dict:
    """Сводка по базе блога: статьи по origin, площадки, публикации, свежие."""
    if not BLOGBASE_DB.exists():
        return {"exists": False}
    try:
        con = sqlite3.connect(f"file:{BLOGBASE_DB}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        articles = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        by_origin = [{"k": (r[0] or "—"), "n": r[1]} for r in con.execute(
            "SELECT origin, COUNT(*) FROM articles GROUP BY origin ORDER BY 2 DESC")]
        nets = [{"name": r[0], "enabled": bool(r[1])} for r in con.execute(
            "SELECT name, enabled FROM networks ORDER BY enabled DESC, name")]
        pubs = [{"network": r[0], "n": r[1]} for r in con.execute(
            "SELECT network, COUNT(*) FROM publications "
            "WHERE status='published' GROUP BY network ORDER BY 2 DESC")]
        pub_total = con.execute(
            "SELECT COUNT(*) FROM publications WHERE status='published'").fetchone()[0]
        recent = [{"id": r["id"], "title": r["title"], "origin": r["origin"],
                   "created": _fmt_ts(r["created_at"])}
                  for r in con.execute(
                      "SELECT id,title,origin,created_at FROM articles "
                      "ORDER BY created_at DESC LIMIT 20")]
        con.close()
    except Exception as e:
        return {"exists": True, "error": str(e)[:200]}
    return {"exists": True, "articles": articles, "by_origin": by_origin,
            "networks": nets, "publications": pubs, "pub_total": pub_total,
            "recent": recent}


# ── Хранилище визуальных референсов ───────────────────────────────────────────
def _refs_run(args: list[str], timeout: int = 120) -> dict:
    """Вызов store.py, ответ — JSON последней строки stdout."""
    if not REFS_TOOL.exists():
        return {"ok": False, "out": "store.py не найден"}
    try:
        # supervisor часто без UTF-8 локали → кириллица в путях/выводе рвётся.
        # Форсируем UTF-8 и для дочернего IO, и для декода на нашей стороне.
        # CONTEXT_STORE_ROOT — чтобы путь к бордам задавался тут, а не дублировался
        # в store.py: разделы Органона переезжают между номерами.
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
               "CONTEXT_STORE_ROOT": str(REFS_ROOT)}
        p = subprocess.run([PYTHON, str(REFS_TOOL), *args], cwd=str(PROJECT_DIR),
                           capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=timeout)
        out = (p.stdout or "").strip().splitlines()
        for line in reversed(out):
            try:
                return json.loads(line)
            except Exception:
                continue
        return {"ok": p.returncode == 0, "out": (p.stderr or p.stdout).strip()[:1600]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "таймаут (много картинок?) — повтори"}
    except Exception as e:
        return {"ok": False, "out": str(e)[:400]}


def refs_meta() -> dict:
    """Манифест хранилища референсов (пересобирается на каждом запросе — дёшево)."""
    m = _refs_run(["manifest"], timeout=30)
    if "boards" not in m:
        return {"exists": REFS_ROOT.exists(), "boards": [], "categories": [],
                "total_boards": 0, "total_images": 0, "error": m.get("out")}
    m["exists"] = True
    return m


def refs_img_bytes(rel: str) -> tuple[bytes, str] | None:
    """Отдать байты картинки по пути вида '<slug>/<Полярность>/<file>'.
    Жёсткая защита: путь обязан лежать внутри REFS_ROOT."""
    try:
        p = (REFS_ROOT / rel).resolve()
        if not str(p).startswith(str(REFS_ROOT.resolve())) or not p.is_file():
            return None
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        return p.read_bytes(), ctype
    except Exception:
        return None


def refs_board_create(p: dict) -> dict:
    name = (p.get("name") or "").strip()
    if not name:
        return {"ok": False, "out": "нужно имя борда"}
    args = ["board-create", name, "--name", name,
            "--category", (p.get("category") or "Разное").strip()]
    if p.get("description"):
        args += ["--desc", p["description"]]
    if p.get("tags"):
        args += ["--tags", p["tags"] if isinstance(p["tags"], str)
                 else ",".join(p["tags"])]
    return _refs_run(args, timeout=20)


def refs_add_url(p: dict) -> dict:
    urls = p.get("urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in re.split(r"[\s,]+", urls) if u.strip()]
    if not p.get("slug") or not urls:
        return {"ok": False, "out": "нужны slug и ссылки"}
    return _refs_run(["add-url", p["slug"], p.get("polarity", "like"), *urls],
                     timeout=180)


def refs_add_pinterest(p: dict) -> dict:
    if not p.get("slug") or not p.get("url"):
        return {"ok": False, "out": "нужны slug и ссылка на борд/пин Pinterest"}
    return _refs_run(["ingest-pinterest", p["slug"], p.get("polarity", "like"),
                      p["url"], "--limit", str(int(p.get("limit", 30)))], timeout=300)


def refs_add_file(p: dict) -> dict:
    """Загрузка файла из UI (base64)."""
    if not p.get("slug") or not p.get("data") or not p.get("name"):
        return {"ok": False, "out": "нужны slug, name, data"}
    return _refs_run(["add-b64", p["slug"], p.get("polarity", "like"),
                      p["name"], p["data"]], timeout=60)


def refs_delete(p: dict) -> dict:
    if p.get("file"):
        return _refs_run(["delete-image", p["slug"], p.get("polarity", "like"),
                          p["file"]], timeout=20)
    if p.get("slug"):
        return _refs_run(["delete-board", p["slug"]], timeout=20)
    return {"ok": False, "out": "нужен slug (и file для одной картинки)"}


def refs_dna_prompt(slug: str) -> str:
    """Готовый промпт для Claude-роута: извлечь визуальное ДНК борда."""
    d = REFS_ROOT / slug
    return (
        f"Проанализируй визуальный борд референсов «{slug}».\n"
        f"Папка борда: {d}\n"
        f"1. Посмотри (зрением, Read) все картинки в подпапке «Нравится» — это то, "
        f"как Димитри ХОЧЕТ. И все в «Не нравится» — как он НЕ хочет.\n"
        f"2. Извлеки визуальное ДНК: палитра (hex), типографика/настроение, "
        f"плотность и лейаут, стиль по словарю ui-ux-pro-max, ключевые слова.\n"
        f"3. Из «Нравится» собери do's, из «Не нравится» — явные don'ts "
        f"(«не делать как здесь, потому что …»).\n"
        f"4. Запиши результат в раздел «## Визуальное ДНК» файла {d}/board.md "
        f"(замени плейсхолдер).")


def tail_log(job: str | None, service: str | None) -> str:
    if service:
        p = _run([PYTHON, str(POS), "logs", service])
        return p["out"] or "(пусто)"
    path = CRON_LOG
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if job:
            lines = [ln for ln in lines if job in ln]
        return "\n".join(lines[-60:]) or "(пусто)"
    except Exception as e:
        return f"(нет лога: {e})"


# ── Claude Code (стрим из-под UI) ─────────────────────────────────────────────
_sess_lock = threading.Lock()


def claude_sessions() -> list[dict]:
    return _load(CLAUDE_SESSIONS, [])


def _save_session(sid: str, title: str, model: str, cwd: str) -> None:
    with _sess_lock:
        items = claude_sessions()
        if any(s["id"] == sid for s in items):
            return
        items.insert(0, {"id": sid, "title": title[:80], "model": model,
                         "cwd": cwd, "created": dt.datetime.now().strftime("%d.%m %H:%M")})
        CLAUDE_SESSIONS.write_text(json.dumps(items[:40], ensure_ascii=False, indent=2),
                                   encoding="utf-8")


def stream_claude(prompt: str, session_id: str | None, model: str,
                  permission: str, cwd: str, emit) -> None:
    """Запустить claude -p и стримить stream-json события через emit(dict)."""
    model = model if model in MODELS else "sonnet"
    permission = permission if permission in PERMS else "plan"
    cwd = cwd if os.path.isdir(cwd) else str(PROJECT_DIR)
    is_new = not session_id
    sid = session_id or str(uuid.uuid4())

    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--model", model]
    cmd += ["--session-id", sid] if is_new else ["--resume", sid]
    if permission == "full":
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--permission-mode", permission]

    emit({"type": "_meta", "session_id": sid, "is_new": is_new, "model": model,
          "permission": permission, "cwd": cwd})
    if is_new:
        _save_session(sid, prompt, model, cwd)

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
    except Exception as e:
        emit({"type": "_error", "error": f"не удалось запустить claude: {e}"})
        return
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                emit(json.loads(line))
            except json.JSONDecodeError:
                emit({"type": "_raw", "text": line})
        proc.wait(timeout=5)
        err = (proc.stderr.read() or "").strip()
        if proc.returncode and err:
            emit({"type": "_error", "error": err[:500]})
    except Exception as e:
        emit({"type": "_error", "error": str(e)[:300]})
        try:
            proc.kill()
        except Exception:
            pass
    emit({"type": "_done"})


# ── Детальная аналитика сервисов ──────────────────────────────────────────────
def _runs_log(filter_fn=None, n=40) -> dict:
    """Разобрать runs.log: {success, error, recent[]}. filter_fn(job)->bool."""
    success = error = 0
    recent = []
    try:
        for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            ts, res, job = cols[0], cols[1], cols[2]
            if filter_fn and not filter_fn(job):
                continue
            if res == "OK":
                success += 1
            elif res == "ERR":
                error += 1
            recent.append({"ts": ts, "result": res, "job": job,
                           "detail": (cols[3] if len(cols) > 3 else "")[:90]})
    except Exception:
        pass
    return {"success": success, "error": error, "recent": recent[-n:][::-1]}


def localapi_activity(n=60) -> list[dict]:
    items = []
    try:
        for line in LOCALAPI_LOG.read_text(encoding="utf-8").splitlines()[-n:][::-1]:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return items


def _sqlite_counts(db: Path, tables: list[str]) -> dict:
    out = {}
    if not db.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for t in tables:
            try:
                out[t] = conn.execute(f"select count(*) from {t}").fetchone()[0]
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return out


def _svc_base(name: str) -> dict:
    pos = _load(POS_STATE, {})
    info = pos.get("services", {}).get(name, {})
    svc_defs = {s["name"]: s for s in _load(SERVICES_FILE, {}).get("services", [])}
    d = svc_defs.get(name, {})
    return {
        "name": name,
        "alive": bool(info.get("alive")),
        "pid": info.get("pid"),
        "uptime": _fmt_uptime(info.get("uptime_s")),
        "uptime_s": info.get("uptime_s"),
        "restarts": info.get("restarts", 0),
        "last_exit": info.get("last_exit"),
        "note": d.get("note", ""),
        "cmd": " ".join(d.get("cmd", [])),
        "cwd": d.get("cwd", ""),
        "log": tail_log(None, name),
    }


def service_detail(name: str) -> dict:
    base = _svc_base(name)
    cards = []
    extra = {}

    if name == "cron":
        jobs = [j for j in _load(JOBS_FILE, []) if not j["id"].startswith("example-")]
        st = _load(STATE_FILE, {})
        ids = {j["id"] for j in jobs}
        rl = _runs_log(lambda j: j in ids, n=60)
        # honest jobs с вычисленным health, без повторного gather() (P3/T5)
        all_jobs = build_jobs()
        detail_jobs = [j for j in all_jobs if not j["is_example"]]
        bad = [j["id"] for j in detail_jobs if j["health"] in ("error", "degraded")]
        cards = [
            ("Задач", len(jobs)), ("Активных", sum(1 for j in jobs if j.get("enabled", True))),
            ("Сейчас в ошибке", len(bad), "err" if bad else "ok"),
            ("Успешных запусков", rl["success"], "ok"),
            ("Ошибок запусков", rl["error"], "err" if rl["error"] else ""),
        ]
        extra = {"jobs": detail_jobs, "recent": rl["recent"],
                 "radar": _sqlite_counts(RADAR_DB, ["items", "sources"])}

    elif name == "claude-local-api":
        act = localapi_activity(80)
        today = dt.date.today().isoformat()
        td = [a for a in act if str(a.get("ts", "")).startswith(today)]
        oks = [a for a in act if a.get("ok")]
        avg = int(sum(a.get("ms") or 0 for a in oks) / len(oks)) if oks else 0
        by_model = {}
        for a in act:
            by_model[a.get("model", "?")] = by_model.get(a.get("model", "?"), 0) + 1
        cards = [
            ("Вызовов сегодня", len(td)), ("Всего в логе", len(act)),
            ("Сред. время", f"{avg}<small>ms</small>"),
            ("Ошибок", sum(1 for a in act if not a.get("ok")), "err" if any(not a.get("ok") for a in act) else ""),
        ]
        extra = {"activity": act, "by_model": by_model}

    elif name == "tg-blog-editor":
        blog = {"pending": 0, "published": 0, "skipped": 0}
        try:
            conn = sqlite3.connect(f"file:{BLOG_DB}?mode=ro", uri=True)
            for s, c in conn.execute("select status,count(*) from drafts group by status"):
                blog[s] = c
            conn.close()
        except Exception:
            pass
        pub = len(_load(PUBLISHED, []))
        rl = _runs_log(lambda j: j in ("digitcode-digest", "blog-autopublish"), n=40)
        cards = [
            ("Черновики", blog.get("pending", 0)), ("Опубликовано", pub, "ok"),
            ("Пропущено", blog.get("skipped", 0)),
            ("Дайджест: ошибок", rl["error"], "err" if rl["error"] else "ok"),
        ]
        extra = {"recent": rl["recent"]}

    elif name == "tg-assistant":
        c = _sqlite_counts(PSYCH_DB, ["checkins", "outbox", "channel_messages", "schedule"])
        cards = [
            ("Чек-инов", c.get("checkins", 0)), ("Очередь (outbox)", c.get("outbox", 0)),
            ("Сообщений канала", c.get("channel_messages", 0)),
            ("Расписаний", c.get("schedule", 0)),
        ]
        extra = {"counts": c}

    elif name == "mission-control":
        sess = claude_sessions()
        cards = [
            ("Claude-сессий", len(sess)),
            ("Аптайм", base["uptime"]),
            ("Перезапусков", base["restarts"]),
        ]
        extra = {"sessions": sess[:10]}

    base["cards"] = [{"k": c[0], "v": c[1], "cls": (c[2] if len(c) > 2 else "")} for c in cards]
    base.update(extra)
    return base


# ── История сессий Claude Code ────────────────────────────────────────────────
def claude_session_history(sid: str) -> dict:
    """Прочитать ~/.claude/projects/<proj>/<sid>.jsonl → список сообщений."""
    f = CC_PROJECT_DIR / f"{sid}.jsonl"
    msgs = []
    if not f.exists():
        return {"messages": [], "exists": False}
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") not in ("user", "assistant"):
                continue
            m = e.get("message", {})
            content = m.get("content")
            role = e.get("type")
            if isinstance(content, str):
                if content.strip():
                    msgs.append({"role": role, "kind": "text", "text": content})
                continue
            for b in content or []:
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    msgs.append({"role": role, "kind": "text", "text": b["text"]})
                elif bt == "tool_use":
                    msgs.append({"role": "assistant", "kind": "tool", "name": b.get("name", ""),
                                 "input": b.get("input", {})})
                elif bt == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    msgs.append({"role": "user", "kind": "result", "text": (str(c) or "")[:400]})
    except Exception as e:
        return {"messages": [], "exists": True, "error": str(e)}
    return {"messages": msgs, "exists": True}


# ── Radar · инфополе (отдельная вкладка-инструмент) ───────────────────────────
def _radar_conn():
    return sqlite3.connect(f"file:{RADAR_DB}?mode=ro", uri=True)


def _fmt_ts(ts) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else "—"


def _ago(ts) -> str:
    if not ts:
        return "—"
    s = max(0, int(dt.datetime.now().timestamp() - ts))
    if s < 3600:
        return f"{s // 60}м назад"
    if s < 86400:
        return f"{s // 3600}ч назад"
    return f"{s // 86400}д назад"


# стоп-слова для частотного выделения тем (RU+EN, служебное)
_STOP = set("""и в во не на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе
под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно
всю между что это как для the and for are but not you all any can her was one our out has had how its who get why
this that with from your they have will been more some what when which their would about into them then než из-за
который которая которые также чем кроме после года году нового новый стал стало будут этом году этому есть""".split())
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]{2,}")


def radar_data() -> dict:
    """Обзор инфополя: пульс, разбивки (с свежестью), таймлайн, темы, свежие."""
    out = {"exists": RADAR_DB.exists(), "total": 0, "today": 0, "week": 0,
           "by_kind": [], "by_source": [], "timeline": [], "topics": [],
           "recent": [], "updated": "—", "sources_list": []}
    if not RADAR_DB.exists():
        return out
    now = dt.datetime.now().timestamp()
    try:
        conn = _radar_conn()
        out["total"] = conn.execute("select count(*) from items").fetchone()[0]
        out["today"] = conn.execute("select count(*) from items where ts>=?", (now - 86400,)).fetchone()[0]
        out["week"] = conn.execute("select count(*) from items where ts>=?", (now - 7 * 86400,)).fetchone()[0]
        out["by_kind"] = [{"k": k or "—", "n": n}
                          for k, n in conn.execute("select kind,count(*) from items group by kind order by 2 desc")]
        # источники + свежесть (last ts) — видно, какие ленты замолчали
        for s, n, last in conn.execute(
                "select source,count(*),max(ts) from items group by source order by 2 desc limit 20"):
            out["by_source"].append({"k": s or "—", "n": n, "ago": _ago(last),
                                     "stale": bool(last and (now - last) > 3 * 86400)})
        out["sources_list"] = [r["k"] for r in out["by_source"]]
        # таймлайн по дням за 30 дней
        for d, n in conn.execute(
                "select date(ts,'unixepoch','localtime') d,count(*) from items where ts>=? group by d order by d",
                (now - 30 * 86400,)):
            out["timeline"].append({"d": d, "n": n})
        # темы за 7 дней — частотность по заголовкам
        cnt: dict = {}
        for (title,) in conn.execute("select title from items where ts>=? and title is not null", (now - 7 * 86400,)):
            for w in _WORD_RE.findall((title or "").lower()):
                if w not in _STOP:
                    cnt[w] = cnt.get(w, 0) + 1
        out["topics"] = [{"k": w, "n": c} for w, c in sorted(cnt.items(), key=lambda i: -i[1])[:18] if c > 1]
        # свежие материалы (с id для карточки)
        for iid, src, kind, title, url, ts in conn.execute(
                "select id,source,kind,title,url,ts from items order by ts desc limit 50"):
            out["recent"].append({"id": iid, "source": src or "—", "kind": kind or "—",
                                  "title": (title or "(без заголовка)")[:200], "url": url or "", "ts": _fmt_ts(ts)})
        mx = conn.execute("select max(fetched_at) from items").fetchone()[0]
        out["updated"] = _fmt_ts(mx)
        conn.close()
    except Exception:
        pass
    return out


def _radar_where(q: str, kind: str, source: str, since_days: int):
    """Собрать общий WHERE для поиска/фасетов: (sql, params)."""
    where, params = [], []
    if (q or "").strip():
        where.append("(title like ? or text like ?)")
        like = f"%{q.strip()}%"
        params += [like, like]
    if kind in ("rss", "telegram"):
        where.append("kind=?")
        params.append(kind)
    if (source or "").strip():
        where.append("source=?")
        params.append(source.strip())
    if since_days and since_days > 0:
        where.append("ts>=?")
        params.append(dt.datetime.now().timestamp() - since_days * 86400)
    return ((" where " + " and ".join(where)) if where else ""), params


def radar_search(q: str, kind: str, source: str, since_days: int, offset: int, limit: int = 50) -> dict:
    """Поиск/фильтр материалов: текст (LIKE по title+text) + kind + source + период."""
    out = {"items": [], "total": 0, "offset": offset, "has_more": False}
    if not RADAR_DB.exists():
        return out
    wsql, params = _radar_where(q, kind, source, since_days)
    try:
        conn = _radar_conn()
        out["total"] = conn.execute(f"select count(*) from items{wsql}", params).fetchone()[0]
        rows = conn.execute(
            f"select id,source,kind,title,url,ts from items{wsql} order by ts desc limit ? offset ?",
            params + [limit, offset]).fetchall()
        for iid, src, k, title, url, ts in rows:
            out["items"].append({"id": iid, "source": src or "—", "kind": k or "—",
                                "title": (title or "(без заголовка)")[:200], "url": url or "", "ts": _fmt_ts(ts)})
        out["has_more"] = offset + len(rows) < out["total"]
        conn.close()
    except Exception:
        pass
    return out


def radar_item(iid: int) -> dict:
    out = {"found": False}
    if not RADAR_DB.exists():
        return out
    try:
        conn = _radar_conn()
        r = conn.execute("select id,source,kind,title,text,url,ts,fetched_at from items where id=?", (iid,)).fetchone()
        conn.close()
        if r:
            out = {"found": True, "id": r[0], "source": r[1] or "—", "kind": r[2] or "—",
                   "title": r[3] or "(без заголовка)", "text": r[4] or "(текст не сохранён)",
                   "url": r[5] or "", "ts": _fmt_ts(r[6]), "fetched": _fmt_ts(r[7])}
    except Exception:
        pass
    return out


def radar_similar(iid: int, k: int = 8) -> dict:
    """Ближайшие по эмбеддингу материалы (косинус = скалярное, векторы нормированы)."""
    out = {"items": []}
    if not RADAR_DB.exists():
        return out
    try:
        import numpy as np
        conn = _radar_conn()
        base = conn.execute("select embedding from items where id=?", (iid,)).fetchone()
        if not base or not base[0]:
            conn.close()
            return out
        q = np.frombuffer(base[0], dtype=np.float32)
        scored = []
        for rid, src, kind, title, url, ts, emb in conn.execute(
                "select id,source,kind,title,url,ts,embedding from items where embedding is not null and id!=?", (iid,)):
            v = np.frombuffer(emb, dtype=np.float32)
            if v.shape == q.shape:
                scored.append((float(np.dot(q, v)), rid, src, kind, title, url, ts))
        conn.close()
        scored.sort(reverse=True)
        for sc, rid, src, kind, title, url, ts in scored[:k]:
            out["items"].append({"id": rid, "source": src or "—", "kind": kind or "—",
                                "title": (title or "(без заголовка)")[:200], "url": url or "",
                                "ts": _fmt_ts(ts), "score": round(sc, 3)})
    except Exception as e:
        out["error"] = str(e)[:120]
    return out


def radar_facets(q: str, kind: str, source: str, since_days: int) -> dict:
    """Срезы (категории) под ТЕКУЩИЙ фильтр: по типу, источникам, темам, дням.

    Меняются вместе с фильтрами — поэтому считаются по тому же WHERE, что и поиск.
    """
    out = {"by_kind": [], "by_source": [], "topics": [], "timeline": [], "matched": 0}
    if not RADAR_DB.exists():
        return out
    wsql, params = _radar_where(q, kind, source, since_days)
    now = dt.datetime.now().timestamp()
    try:
        conn = _radar_conn()
        out["matched"] = conn.execute(f"select count(*) from items{wsql}", params).fetchone()[0]
        out["by_kind"] = [{"k": k or "—", "n": n} for k, n in conn.execute(
            f"select kind,count(*) from items{wsql} group by kind order by 2 desc", params)]
        for s, n, last in conn.execute(
                f"select source,count(*),max(ts) from items{wsql} group by source order by 2 desc limit 20", params):
            out["by_source"].append({"k": s or "—", "n": n, "ago": _ago(last),
                                     "stale": bool(last and (now - last) > 3 * 86400)})
        # таймлайн по дням в пределах текущего фильтра (последние 30 дней)
        tl_w = wsql + (" and " if wsql else " where ") + "ts>=?"
        for d, n in conn.execute(
                f"select date(ts,'unixepoch','localtime') d,count(*) from items{tl_w} group by d order by d",
                params + [now - 30 * 86400]):
            out["timeline"].append({"d": d, "n": n})
        # темы по заголовкам отфильтрованного набора
        cnt: dict = {}
        for (title,) in conn.execute(f"select title from items{wsql}", params):
            for w in _WORD_RE.findall((title or "").lower()):
                if w not in _STOP:
                    cnt[w] = cnt.get(w, 0) + 1
        out["topics"] = [{"k": w, "n": c} for w, c in sorted(cnt.items(), key=lambda i: -i[1])[:18] if c > 1]
        conn.close()
    except Exception:
        pass
    return out


# ── Radar · спросить нейронку про новости (RAG по базе) ───────────────────────
def _radar_retrieve(question: str, kind: str, source: str, since_days: int, n: int = 50) -> list[dict]:
    """Подобрать релевантные материалы под вопрос: ключевые слова + свежесть, в рамках фильтра."""
    if not RADAR_DB.exists():
        return []
    words = [w for w in _WORD_RE.findall((question or "").lower()) if w not in _STOP][:6]
    wsql, params = _radar_where("", kind, source, since_days)
    rows = []
    try:
        conn = _radar_conn()
        if words:
            kw = " or ".join(["(title like ? or text like ?)"] * len(words))
            qp = []
            for w in words:
                qp += [f"%{w}%", f"%{w}%"]
            sql = (f"select source,kind,title,text,url,ts from items{wsql}"
                   + (" and (" if wsql else " where (") + kw + ") order by ts desc limit ?")
            rows = conn.execute(sql, params + qp + [n]).fetchall()
        # мало по ключевым словам — добираем свежими в пределах фильтра
        if len(rows) < min(n, 12):
            seen = {r[4] for r in rows if r[4]}
            for r in conn.execute(f"select source,kind,title,text,url,ts from items{wsql} order by ts desc limit ?",
                                  params + [n]):
                key = r[4] or (r[2] or "")
                if key in seen:
                    continue
                rows.append(r)
                seen.add(key)
                if len(rows) >= n:
                    break
        conn.close()
    except Exception:
        pass
    return [{"source": r[0], "kind": r[1], "title": r[2] or "", "text": r[3] or "",
             "url": r[4] or "", "ts": _fmt_ts(r[5])} for r in rows[:n]]


def radar_ask_prompt(question: str, kind: str, source: str, since_days: int):
    """Собрать промпт для Claude: материалы из базы + вопрос. Возвращает (prompt, n).

    Контекст намеренно компактный (≈24 материала, текст обрезан) — чтобы ответ
    приходил быстро и не упирался в большие задержки на крупном промпте.
    """
    items = _radar_retrieve(question, kind, source, since_days, n=24)
    blocks = []
    for i, it in enumerate(items, 1):
        blocks.append(f"[{i}] {it['source']} · {it['ts']}\n{it['title']}\n{it['text'][:400]}\n{it['url']}")
    ctx = "\n\n".join(blocks) if blocks else "(подходящих материалов в базе не найдено)"
    prompt = (
        f"Ты — аналитик инфополя. Ниже {len(items)} материалов из базы радара (новости и посты "
        "из RSS-лент и Telegram-каналов). Ответь на вопрос пользователя, опираясь ТОЛЬКО на эти "
        "материалы, по-русски, по делу. Ссылайся на источники по номеру [N] и давай ссылки, когда "
        "уместно. Если ответа в материалах нет — честно скажи об этом.\n\n"
        f"=== МАТЕРИАЛЫ ===\n{ctx}\n\n=== ВОПРОС ===\n{question}"
    )
    return prompt, len(items)


# ── Radar · управление источниками парсинга (sources.json) ────────────────────
def radar_sources_get() -> dict:
    s = _load(RADAR_SOURCES, {})
    settings = s.get("settings", {}) if isinstance(s, dict) else {}
    return {
        "exists": RADAR_SOURCES.exists(),
        "max_per_source": settings.get("max_per_source", 15),
        "lookback_hours": settings.get("lookback_hours", 24),
        "fetch_full": settings.get("fetch_full", True),
        "rss": [{"name": r.get("name", ""), "url": r.get("url", "")} for r in (s.get("rss") or []) if isinstance(r, dict)],
        "telegram": [{"name": c.get("name", ""), "chat": c.get("chat", "")}
                     for c in (s.get("telegram_channels") or []) if isinstance(c, dict)],
        "counts": _sqlite_counts(RADAR_DB, ["items"]),
    }


def radar_sources_save(p: dict) -> dict:
    """Перезаписать список источников парсинга. Прочие настройки сохраняются."""
    s = _load(RADAR_SOURCES, {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("settings", {})
    # лимит на источник
    try:
        mps = int(p.get("max_per_source", s["settings"].get("max_per_source", 15)))
        if 1 <= mps <= 200:
            s["settings"]["max_per_source"] = mps
    except (TypeError, ValueError):
        return {"ok": False, "out": "лимит на источник — число 1..200"}
    # ходить ли по ссылке материала за полным текстом
    if "fetch_full" in (p or {}):
        s["settings"]["fetch_full"] = bool(p.get("fetch_full"))
    # RSS: name+url, url обязан быть http(s)
    rss = []
    for r in (p.get("rss") or []):
        if not isinstance(r, dict):
            continue
        url = (r.get("url") or "").strip()
        name = (r.get("name") or "").strip()
        if not url:
            continue
        if not re.match(r"^https?://", url):
            return {"ok": False, "out": f"RSS-ссылка должна начинаться с http(s): {url[:40]}"}
        rss.append({"name": name or url, "url": url})
    # Telegram: name+chat (@username или числовой id)
    tg = []
    for c in (p.get("telegram") or []):
        if not isinstance(c, dict):
            continue
        chat = (c.get("chat") or "").strip()
        name = (c.get("name") or "").strip()
        if not chat:
            continue
        if not re.match(r"^(@[\w\d_]{3,}|-?\d{5,})$", chat):
            return {"ok": False, "out": f"TG-канал — @username или числовой id: {chat[:40]}"}
        tg.append({"name": name or chat, "chat": chat})
    if not rss and not tg:
        return {"ok": False, "out": "нужен хотя бы один источник"}
    s["rss"] = rss
    s["telegram_channels"] = tg
    try:
        RADAR_SOURCES.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "out": str(e)[:200]}
    return {"ok": True, "out": f"сохранено: {len(rss)} RSS + {len(tg)} TG · применится при следующем прогоне news-ingest"}


# ── Cron · конструктор задач (create/delete через cronctl) ────────────────────
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def cron_create(p: dict) -> dict:
    """Собрать `cronctl add` из формы UI. kind: shell|prompt."""
    jid = (p.get("id") or "").strip()
    if not _ID_RE.match(jid):
        return {"ok": False, "out": "id: латиница/цифры/дефис, 2–41 символ, начинается с буквы/цифры"}
    schedule = (p.get("schedule") or "").strip()
    if not schedule:
        return {"ok": False, "out": "укажи расписание"}
    cmd = [PYTHON, str(CRONCTL), "add", jid, "--schedule", schedule]
    if p.get("kind") == "prompt":
        prompt = (p.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "out": "пустой промпт"}
        cmd += ["--prompt", prompt]
        if p.get("model") in MODELS:
            cmd += ["--model", p["model"]]
    else:
        shell = (p.get("shell") or "").strip()
        if not shell:
            return {"ok": False, "out": "пустая shell-команда"}
        cmd += ["--shell", shell]
    if (p.get("desc") or "").strip():
        cmd += ["--desc", p["desc"].strip()]
    if (p.get("requires") or "").strip():
        cmd += ["--requires", p["requires"].strip()]
    for sink, flag in (("obsidian", "--obsidian"), ("file", "--file"), ("telegram", "--telegram")):
        v = (p.get(sink) or "").strip()
        if v:
            cmd += [flag, v]
    if p.get("no_catchup"):
        cmd.append("--no-catchup")
    return _run(cmd)


def cron_delete(jid: str) -> dict:
    if not (jid or "").strip():
        return {"ok": False, "out": "нет id"}
    return _run([PYTHON, str(CRONCTL), "rm", jid.strip()])


# ── Blog · админ-панель (данные/инфополе/история) ─────────────────────────────
def blog_admin() -> dict:
    counts = {"pending": 0, "published": 0, "skipped": 0}
    drafts, sources = [], {}
    try:
        conn = sqlite3.connect(f"file:{BLOG_DB}?mode=ro", uri=True)
        for s, c in conn.execute("select status,count(*) from drafts group by status"):
            counts[s] = c
        for did, text, url, status, src, created, by in conn.execute(
                "select id,text,url,status,source_spec,created_at,decided_by from drafts order by id desc limit 60"):
            drafts.append({"id": did, "text": (text or "")[:260], "url": url or "", "status": status,
                          "source": src or "—", "by": by or "",
                          "created": dt.datetime.fromtimestamp(created).strftime("%d.%m %H:%M") if created else "—"})
            if src:
                sources[src] = sources.get(src, 0) + 1
        conn.close()
    except Exception:
        pass
    pub = _load(PUBLISHED, [])
    pub_list = pub if isinstance(pub, list) else []
    published = [{"title": x.get("title", "(без заголовка)"), "url": x.get("url", ""), "by": x.get("by", ""),
                 "ts": dt.datetime.fromtimestamp(x["ts"]).strftime("%d.%m %H:%M") if x.get("ts") else "—"}
                for x in pub_list[::-1][:40] if isinstance(x, dict)]
    return {"counts": counts, "drafts": drafts, "published": published,
            "published_total": len(pub_list), "channel": "@digit_code",
            "sources": [{"k": k, "n": v} for k, v in sorted(sources.items(), key=lambda i: -i[1])]}


# ── Psych-bot · аналитика (как в самом боте) ──────────────────────────────────
def _tcount(conn, table: str) -> int:
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]
    except Exception:
        return 0


def psych_admin() -> dict:
    out = {"slots": [], "totals": {}, "recent": [], "tasks": {}, "extra": {}}
    if not PSYCH_DB.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{PSYCH_DB}?mode=ro", uri=True)
        slot_map: dict = {}
        for slot, status, n in conn.execute("select slot,status,count(*) from checkins group by slot,status"):
            slot_map.setdefault(slot, {})[status] = n
        order = ["morning", "day", "evening", "situational", "note"]
        out["slots"] = [{"slot": s, "done": slot_map[s].get("done", 0),
                         "in_progress": slot_map[s].get("in_progress", 0),
                         "abandoned": slot_map[s].get("abandoned", 0)}
                        for s in order if s in slot_map]
        tot = conn.execute("select count(*),coalesce(sum(written),0) from checkins").fetchone()
        out["totals"] = {"checkins": tot[0] or 0, "written": tot[1] or 0,
                         "days": conn.execute("select count(distinct date) from checkins").fetchone()[0]}
        for d, slot, status, written in conn.execute(
                "select date,slot,status,written from checkins order by id desc limit 24"):
            out["recent"].append({"date": d, "slot": slot, "status": status, "written": bool(written)})
        out["tasks"] = {
            "active": conn.execute("select count(*) from tasks where done=0 and deleted=0").fetchone()[0],
            "done": conn.execute("select count(*) from tasks where done=1 and deleted=0").fetchone()[0],
        }
        out["extra"] = {
            "outbox_pending": conn.execute("select count(*) from outbox where done=0").fetchone()[0],
            "channel_messages": _tcount(conn, "channel_messages"),
            "schedule": _tcount(conn, "schedule"),
        }
        conn.close()
    except Exception:
        pass
    return out


# ── hh-autoapply · админ-панель (конфиг + отклики) ────────────────────────────
_HH_FIELDS = {
    "query": str, "search_field": str, "order_by": str, "area": str,
    "experience": list, "work_format": list, "perPage": int, "target_applies": int,
    "max_pages": int, "title_keywords": list, "title_exclude": list,
    "resume_id": str, "window_mode": str,
}


def hh_data() -> dict:
    cfg = _load(HH_CONFIG, {})
    applied = _load(APPLIED, [])
    alist = applied if isinstance(applied, list) else []
    recent = []
    for a in alist[-40:][::-1]:
        if isinstance(a, dict):
            vid = str(a.get("id") or a.get("vacancy_id") or "")
            recent.append({"id": vid, "name": a.get("name") or a.get("title") or vid,
                          "url": a.get("url") or a.get("alternate_url") or (f"https://hh.ru/vacancy/{vid}" if vid else "")})
        else:
            vid = str(a)
            recent.append({"id": vid, "name": f"вакансия {vid}", "url": f"https://hh.ru/vacancy/{vid}"})
    return {"config": cfg, "applied_total": len(alist), "recent": recent}


def hh_save(patch: dict) -> dict:
    cfg = _load(HH_CONFIG, {})
    if not isinstance(cfg, dict):
        cfg = {}
    for k, v in (patch or {}).items():
        if k not in _HH_FIELDS:
            continue
        t = _HH_FIELDS[k]
        try:
            if t is int:
                cfg[k] = int(v)
            elif t is list:
                src = v if isinstance(v, list) else str(v).split(",")
                cfg[k] = [str(x).strip() for x in src if str(x).strip()]
            else:
                cfg[k] = str(v)
        except (TypeError, ValueError):
            return {"ok": False, "out": f"поле {k}: неверное значение"}
    try:
        HH_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "out": str(e)[:200]}
    return {"ok": True, "out": "конфиг hh сохранён"}


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, *a):  # тихо
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif u.path == "/api/state":
            self._json(gather())
        elif u.path == "/api/log":
            q = urllib.parse.parse_qs(u.query)
            self._json({"log": tail_log(q.get("job", [None])[0], q.get("service", [None])[0])})
        elif u.path == "/api/claude/sessions":
            self._json({"sessions": claude_sessions(), "project": str(PROJECT_DIR)})
        elif u.path.startswith("/api/service/"):
            self._json(service_detail(urllib.parse.unquote(u.path.split("/api/service/", 1)[1])))
        elif u.path.startswith("/api/claude/session/"):
            self._json(claude_session_history(u.path.rsplit("/", 1)[1]))
        elif u.path == "/api/localapi/activity":
            self._json({"activity": localapi_activity()})
        elif u.path == "/api/graph":
            self._json(graph_meta())
        elif u.path == "/api/radar":
            self._json(radar_data())
        elif u.path == "/api/radar/search":
            q = urllib.parse.parse_qs(u.query)
            try:
                since = int(q.get("since", ["0"])[0])
            except ValueError:
                since = 0
            try:
                offset = max(0, int(q.get("offset", ["0"])[0]))
            except ValueError:
                offset = 0
            self._json(radar_search(q.get("q", [""])[0], q.get("kind", [""])[0],
                                    q.get("source", [""])[0], since, offset))
        elif u.path == "/api/radar/facets":
            q = urllib.parse.parse_qs(u.query)
            try:
                since = int(q.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._json(radar_facets(q.get("q", [""])[0], q.get("kind", [""])[0],
                                    q.get("source", [""])[0], since))
        elif u.path == "/api/radar/sources":
            self._json(radar_sources_get())
        elif u.path == "/api/radar/item":
            q = urllib.parse.parse_qs(u.query)
            try:
                self._json(radar_item(int(q.get("id", ["0"])[0])))
            except ValueError:
                self._json({"found": False})
        elif u.path == "/api/radar/similar":
            q = urllib.parse.parse_qs(u.query)
            try:
                self._json(radar_similar(int(q.get("id", ["0"])[0])))
            except ValueError:
                self._json({"items": []})
        elif u.path == "/api/blog":
            self._json(blog_admin())
        elif u.path == "/api/psych":
            self._json(psych_admin())
        elif u.path == "/api/hh":
            self._json(hh_data())
        elif u.path == "/graph":
            if GRAPH_HTML.exists():
                self._send(200, GRAPH_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "граф ещё не построен".encode("utf-8"), "text/plain; charset=utf-8")
        elif u.path == "/api/zotero":
            self._json(zotero_meta())
        elif u.path == "/zotero/graph":
            if ZOTERO_GRAPH_HTML.exists():
                self._send(200, ZOTERO_GRAPH_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "граф библиотеки ещё не построен".encode("utf-8"), "text/plain; charset=utf-8")
        elif u.path == "/api/ctxsearch":
            q = urllib.parse.parse_qs(u.query)
            self._json(ctx_search(q.get("store", ["obsidian"])[0],
                                  q.get("mode", ["hybrid"])[0],
                                  q.get("q", [""])[0],
                                  q.get("limit", ["8"])[0]))
        elif u.path == "/api/sources":
            self._json(sources_meta())
        elif u.path == "/sources/graph":
            if SOURCES_GRAPH_HTML.exists():
                self._send(200, SOURCES_GRAPH_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "граф базы источников ещё не построен".encode("utf-8"), "text/plain; charset=utf-8")
        elif u.path == "/api/capital":
            self._json(capital_meta())
        elif u.path == "/capital/graph":
            if CAPITAL_GRAPH_HTML.exists():
                self._send(200, CAPITAL_GRAPH_HTML.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "граф связей ещё не построен".encode("utf-8"),
                           "text/plain; charset=utf-8")
        elif u.path == "/api/blogbase":
            self._json(blogbase_admin())
        elif u.path == "/api/refs":
            self._json(refs_meta())
        elif u.path == "/api/refs/dna":
            q = urllib.parse.parse_qs(u.query)
            self._json({"prompt": refs_dna_prompt(q.get("slug", [""])[0])})
        elif u.path == "/refs/img":
            q = urllib.parse.parse_qs(u.query)
            r = refs_img_bytes(urllib.parse.unquote(q.get("path", [""])[0]))
            if r:
                self._send(200, r[0], r[1])
            else:
                self._send(404, b"no image", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if u.path == "/api/cron/run":
            self._json(cron_run(body["id"]))
        elif u.path == "/api/cron/toggle":
            self._json(cron_toggle(body["id"], bool(body["enabled"])))
        elif u.path == "/api/service/restart":
            # {name} → точечный рестарт одного сервиса; без name → глобальный (confirm на фронте)
            self._json(service_restart(body.get("name")))
        elif u.path == "/api/graph/rebuild":
            self._json(graph_rebuild())
        elif u.path == "/api/zotero/rebuild":
            self._json(zotero_rebuild())
        elif u.path == "/api/sources/rebuild":
            self._json(sources_rebuild())
        elif u.path == "/api/capital/rebuild":
            self._json(capital_rebuild(bool(body.get("sync"))))
        elif u.path == "/api/cron/create":
            self._json(cron_create(body))
        elif u.path == "/api/cron/delete":
            self._json(cron_delete(body.get("id", "")))
        elif u.path == "/api/hh/config":
            self._json(hh_save(body))
        elif u.path == "/api/radar/sources/save":
            self._json(radar_sources_save(body))
        elif u.path == "/api/refs/board-create":
            self._json(refs_board_create(body))
        elif u.path == "/api/refs/add-url":
            self._json(refs_add_url(body))
        elif u.path == "/api/refs/add-pinterest":
            self._json(refs_add_pinterest(body))
        elif u.path == "/api/refs/add-file":
            self._json(refs_add_file(body))
        elif u.path == "/api/refs/delete":
            self._json(refs_delete(body))
        elif u.path == "/api/claude/send":
            self._stream_claude(body)
        elif u.path == "/api/radar/ask":
            self._stream_radar_ask(body)
        else:
            self._send(404, b"not found", "text/plain")

    def _stream_claude(self, body):
        """NDJSON-стрим событий claude (соединение закрывается по завершении)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        lock = threading.Lock()

        def emit(ev):
            with lock:
                try:
                    self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ValueError):
                    pass
        try:
            stream_claude(
                prompt=body.get("prompt", ""),
                session_id=body.get("session_id") or None,
                model=body.get("model", "sonnet"),
                permission=body.get("permission", "plan"),
                cwd=body.get("cwd") or str(PROJECT_DIR),
                emit=emit,
            )
        except Exception as e:
            emit({"type": "_error", "error": str(e)[:300]})

    def _stream_radar_ask(self, body):
        """Спросить нейронку про новости: контекст из radar.db + вопрос → стрим Claude."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        lock = threading.Lock()

        def emit(ev):
            with lock:
                try:
                    self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ValueError):
                    pass
        q = (body.get("question") or "").strip()
        if not q:
            emit({"type": "_error", "error": "пустой вопрос"})
            emit({"type": "_done"})
            return
        try:
            since = int(body.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        try:
            prompt, n = radar_ask_prompt(q, body.get("kind", ""), body.get("source", ""), since)
            emit({"type": "_radar", "materials": n})
            stream_claude(prompt=prompt, session_id=body.get("session_id") or None,
                          model=body.get("model", "sonnet"), permission="plan",
                          cwd=str(PROJECT_DIR), emit=emit)
        except Exception as e:
            emit({"type": "_error", "error": str(e)[:300]})
            emit({"type": "_done"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[mission-control] http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


# HTML встраивается ниже (заполняется из index.html при наличии, иначе дефолт).
_INDEX_PATH = Path(__file__).resolve().parent / "index.html"
try:
    INDEX_HTML = _INDEX_PATH.read_text(encoding="utf-8")
except Exception:
    INDEX_HTML = "<h1>index.html не найден</h1>"


if __name__ == "__main__":
    main()
