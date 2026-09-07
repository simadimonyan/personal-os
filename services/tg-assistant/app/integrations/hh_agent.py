"""Мост к сервису автоотклика hh-autoapply.

Разделение труда: браузерную работу (поиск, отклики, опрос ответов HR) делает
крон через свои скрипты; бот только читает `history.db`, правит `config.json`
и дёргает разовый прогон. Поэтому здесь нет ни playwright, ни node — только
sqlite и subprocess, и ничто не блокирует event loop надолго.

Импортов из hh-autoapply намеренно нет: сервисы живут в разных процессах и с
разными зависимостями, связь — через файлы, которые оба знают.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("assistant.hh")

HH_DIR = Path.home() / "Desktop" / "personal os" / "services" / "hh-autoapply"
CRON_JOBS = Path.home() / "Desktop" / "personal os" / "services" / "cron" / "jobs.json"
CRON_STATE = Path.home() / "Desktop" / "personal os" / "services" / "cron" / "state.json"
DB_PATH = HH_DIR / "history.db"
CONFIG_PATH = HH_DIR / "config.json"
PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

APPLY_JOBS = ("hh-autoapply-am", "hh-autoapply-day", "hh-autoapply-eve")


class HHUnavailable(RuntimeError):
    """Сервис автоотклика не развёрнут или его база ещё не создана."""


# ─────────────────────────── чтение состояния ───────────────────────────

def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HHUnavailable("история откликов пуста — автоотклик ещё ни разу не отработал")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def is_paused() -> bool:
    """Автоотклик на паузе, если все три крон-задачи выключены."""
    try:
        jobs = json.loads(CRON_JOBS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    apply_jobs = [j for j in jobs if j.get("id") in APPLY_JOBS]
    return bool(apply_jobs) and not any(j.get("enabled") for j in apply_jobs)


def set_paused(paused: bool) -> None:
    jobs = json.loads(CRON_JOBS.read_text(encoding="utf-8"))
    for j in jobs:
        if j.get("id") in APPLY_JOBS:
            j["enabled"] = not paused
    CRON_JOBS.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def last_run() -> tuple[str, str]:
    """(когда, статус) последнего прогона автоотклика — из состояния крона."""
    try:
        state = json.loads(CRON_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "—", "неизвестно"
    runs = [(state[j].get("last_run", 0), state[j].get("last_status", "?"))
            for j in APPLY_JOBS if j in state]
    if not runs:
        return "—", "неизвестно"
    ts, status = max(runs)
    delta = datetime.now() - datetime.fromtimestamp(ts)
    if delta < timedelta(hours=1):
        when = f"{int(delta.total_seconds() // 60)} мин назад"
    elif delta < timedelta(days=1):
        when = f"{int(delta.total_seconds() // 3600)} ч назад"
    else:
        when = f"{delta.days} дн назад"
    return when, status


def status() -> dict:
    """Сводка для карточки /hh."""
    cfg = load_config()
    with _connect() as conn:
        def count(where: str, *args) -> int:
            return conn.execute(f"SELECT COUNT(*) c FROM applied WHERE {where}", args).fetchone()["c"]

        today = count("outcome='applied' AND date(created_at)=date('now','localtime')")
        week = count("outcome='applied' AND created_at >= datetime('now','-7 days')")
        rejected = count("outcome='rejected_by_llm' AND created_at >= datetime('now','-7 days')")
        total = conn.execute("SELECT COUNT(*) c FROM applied").fetchone()["c"]
        avg = conn.execute(
            "SELECT AVG(score) a FROM applied WHERE score IS NOT NULL "
            "AND created_at >= datetime('now','-7 days')"
        ).fetchone()["a"]
        invites = conn.execute(
            "SELECT title, company, status, url FROM negotiations "
            "WHERE kind='invite' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) c FROM negotiations WHERE kind='pending'"
        ).fetchone()["c"]

    when, run_status = last_run()
    llm = cfg.get("llm", {})
    return {
        "today": today, "week": week, "rejected_week": rejected, "total": total,
        "avg_score": round(avg, 1) if avg else None,
        "invites": [dict(r) for r in invites], "pending": pending,
        "paused": is_paused(), "last_run": when, "last_status": run_status,
        "min_score": llm.get("min_score", 6),
        "llm_enabled": llm.get("enabled", True),
        "target": cfg.get("target_applies", 10),
        "query": cfg.get("query", "—"),
    }


def pending_invitations() -> list[dict]:
    """Приглашения/отказы, о которых боту ещё предстоит сообщить."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT key, title, company, status, url, kind FROM negotiations "
                "WHERE announced=0 AND kind IN ('invite','reject') ORDER BY updated_at"
            ).fetchall()
        return [dict(r) for r in rows]
    except (HHUnavailable, sqlite3.Error):
        return []


def mark_announced(keys: list[str]) -> None:
    if not keys:
        return
    # отдельное read-write соединение: _connect() открыт только на чтение
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.executemany("UPDATE negotiations SET announced=1 WHERE key=?", [(k,) for k in keys])
        conn.commit()


def run_progress() -> dict:
    """Состояние текущего/последнего прогона (пишет hh-autoapply/progress.py)."""
    path = HH_DIR / "run_state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "empty": True}

    fresh = (time.time() - data.get("updated_at", 0)) < 900
    alive = False
    pid = data.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)   # сигнал 0 — только проверка существования процесса
            alive = True
        except OSError:
            alive = False
    running = not data.get("done") and alive and fresh
    return {**data, "running": running,
            "stale": not data.get("done") and not running, "empty": False}


SKIPPED_OUTCOMES = ("rejected_by_llm", "skip", "unconfirmed")


def skipped(limit: int = 50) -> list[dict]:
    """Вакансии, на которые автоотклик НЕ откликнулся, — для ручного разбора.

    Только те, что бот действительно смотрел (есть название): записи из старой
    плоской истории лежат без данных и показывать в ленте нечего.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, title, company, url, salary, score, reason, outcome, created_at "
            f"FROM applied WHERE outcome IN ({','.join('?' * len(SKIPPED_OUTCOMES))}) "
            f"AND title IS NOT NULL AND title != '' "
            f"ORDER BY score DESC, created_at DESC LIMIT ?",
            (*SKIPPED_OUTCOMES, limit),
        ).fetchall()
    return [dict(r) for r in rows]


async def apply_single(vacancy_id: str, timeout: int = 300) -> str:
    """Ручной отклик на одну вакансию. Возвращает строку итога для карточки."""
    proc = await asyncio.create_subprocess_exec(
        PYTHON, str(HH_DIR / "apply_one.py"), str(vacancy_id), cwd=str(HH_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "⏱ hh не ответил вовремя — попробуй ещё раз"
    text = (out or b"").decode(errors="replace").strip()
    return text.splitlines()[-1] if text else "не понял ответ hh"


def drop_from_skipped(vacancy_id: str) -> None:
    """Убрать вакансию из ленты, не откликаясь («не интересно»)."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("UPDATE applied SET outcome='dismissed' WHERE id=?", (str(vacancy_id),))
        conn.commit()


def recent_letters(limit: int = 5) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT title, company, score, letter, created_at FROM applied "
            "WHERE outcome='applied' AND letter != '' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── правки настроек ───────────────────────────

# Значения фильтров hh — коды те же, что в URL поиска (?experience=between1And3).
# Порядок словарей задаёт порядок кнопок в боте.
EXPERIENCE = {
    "noExperience": "Без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "Более 6",
}
WORK_FORMAT = {
    "REMOTE": "Удалёнка",
    "HYBRID": "Гибрид",
    "ON_SITE": "Офис",
}
# Фильтр -> (ключ в config.json, словарь значений)
FILTERS = {
    "exp": ("experience", EXPERIENCE),
    "fmt": ("work_format", WORK_FORMAT),
}


def filters_state() -> dict[str, list[str]]:
    """Что сейчас выбрано по каждому фильтру."""
    cfg = load_config()
    out = {}
    for name, (key, _) in FILTERS.items():
        value = cfg.get(key) or []
        out[name] = [value] if isinstance(value, str) else list(value)
    return out


def toggle_filter(name: str, code: str) -> list[str]:
    """Включить/выключить значение фильтра. Возвращает новый набор.

    Пустой набор не запрещаем: для hh отсутствие параметра означает «любой»,
    что честнее, чем молча оставлять последнее значение включённым.
    """
    key, allowed = FILTERS[name]
    if code not in allowed:
        raise ValueError(f"неизвестное значение фильтра {name}: {code}")
    cfg = load_config()
    current = cfg.get(key) or []
    if isinstance(current, str):
        current = [current]
    current = list(current)
    if code in current:
        current.remove(code)
    else:
        # порядок как в словаре — чтобы конфиг не перемешивался от кликов
        current = [c for c in allowed if c in current or c == code]
    cfg[key] = current
    save_config(cfg)
    return current


# ──────────────── частота прогонов и уведомления ────────────────

# Расписания под нужное число прогонов в день. Часы разнесены: hh охотнее
# отдаёт выдачу вне пиков, да и отклики в разное время суток выглядят живее.
SCHEDULES = {
    1: ["0 10 * * *"],
    2: ["0 10 * * *", "0 18 * * *"],
    3: ["0 9 * * *", "0 14 * * *", "0 19 * * *"],
    4: ["0 9 * * *", "0 13 * * *", "0 17 * * *", "0 21 * * *"],
}
JOB_PREFIX = "hh-autoapply"
SEEN_FILE = HH_DIR / "bot_seen.json"


def _load_jobs() -> list[dict]:
    return json.loads(CRON_JOBS.read_text(encoding="utf-8"))


def _save_jobs(jobs: list[dict]) -> None:
    CRON_JOBS.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def schedule_state() -> dict:
    jobs = [j for j in _load_jobs() if str(j.get("id", "")).startswith(JOB_PREFIX)
            and "responses" not in str(j.get("id"))]
    active = [j for j in jobs if j.get("enabled")]
    times = []
    for j in active:
        parts = str(j.get("schedule", "")).split()
        if len(parts) >= 2:
            times.append(f"{parts[1].zfill(2)}:{parts[0].zfill(2)}")
    cfg = load_config()
    return {"runs_per_day": len(active), "times": sorted(times),
            "target": cfg.get("target_applies", 10)}


def set_runs_per_day(n: int) -> dict:
    """Переписать расписание крона под n прогонов в день (1–4)."""
    n = max(1, min(4, n))
    schedules = SCHEDULES[n]
    jobs = _load_jobs()
    template = next((j for j in jobs if str(j.get("id", "")).startswith(JOB_PREFIX)
                     and "responses" not in str(j.get("id"))), None)
    if template is None:
        raise HHUnavailable("в кроне нет ни одной задачи автоотклика — нечего настраивать")

    slots = ["am", "day", "eve", "night"]
    rest = [j for j in jobs if not (str(j.get("id", "")).startswith(JOB_PREFIX)
                                    and "responses" not in str(j.get("id")))]
    rebuilt = []
    for i, cron in enumerate(schedules):
        job = json.loads(json.dumps(template))  # копия со всеми полями (output, requires…)
        job["id"] = f"{JOB_PREFIX}-{slots[i]}"
        job["schedule"] = cron
        job["enabled"] = True
        job["description"] = f"Авто-отклик на hh ({cron.split()[1]}:00)"
        rebuilt.append(job)
    _save_jobs(rest + rebuilt)
    return schedule_state()


def set_target(value: int) -> int:
    """Сколько откликов слать за один прогон."""
    cfg = load_config()
    cfg["target_applies"] = max(1, min(50, value))
    save_config(cfg)
    return cfg["target_applies"]


def notify_settings() -> dict:
    cfg = load_config().get("notify", {})
    return {"invites": cfg.get("invites", True),
            "rejects": cfg.get("rejects", True),
            "runs": cfg.get("runs", False)}


def toggle_notify(key: str) -> dict:
    cfg = load_config()
    n = cfg.setdefault("notify", {})
    current = notify_settings()
    n[key] = not current.get(key, False)
    save_config(cfg)
    return notify_settings()


def pending_run_report() -> str | None:
    """Итог прогона, о котором боту ещё не сообщали (если уведомления включены)."""
    if not notify_settings()["runs"]:
        return None
    p = run_progress()
    if p.get("empty") or p.get("running") or not p.get("done"):
        return None
    started = p.get("started_at", 0)
    try:
        seen = json.loads(SEEN_FILE.read_text(encoding="utf-8")).get("last_run", 0)
    except (OSError, json.JSONDecodeError):
        seen = 0
    if started <= seen:
        return None
    SEEN_FILE.write_text(json.dumps({"last_run": started}), encoding="utf-8")
    return f"🏁 Прогон завершён ({p.get('mode', '—')})\n\n{p.get('result', 'без итога')}"


# ─────────────────────────── выбор резюме ───────────────────────────

RESUMES_CACHE = HH_DIR / "resumes_cache.json"


async def list_resumes(refresh: bool = False) -> list[dict]:
    """Резюме аккаунта. Кэшируем: поход в браузер занимает ~20с, а список меняется редко."""
    if not refresh:
        try:
            return json.loads(RESUMES_CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    proc = await asyncio.create_subprocess_exec(
        "node", str(Path.home() / "Desktop" / "personal os" / "tools" / "hh" / "driver.mjs"),
        "hh_get_resumes",
        cwd=str(HH_DIR), env={**os.environ, "HH_HEADLESS": "1"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise HHUnavailable("hh не ответил за 3 минуты — попробуй ещё раз")
    try:
        data = json.loads((out or b"").decode(errors="replace"))
    except json.JSONDecodeError:
        raise HHUnavailable("не удалось прочитать список резюме")
    if not isinstance(data, list) or not data:
        raise HHUnavailable("на аккаунте не найдено резюме")
    RESUMES_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def current_resume() -> str:
    return load_config().get("resume_id", "")


def set_resume(resume_id: str) -> None:
    cfg = load_config()
    cfg["resume_id"] = resume_id
    save_config(cfg)
    # профиль кандидата для писем строится из резюме — сбрасываем, чтобы
    # письма перестали ссылаться на стек из прежнего резюме
    profile = HH_DIR / "profile.txt"
    if profile.exists():
        profile.unlink()


def set_min_score(value: int) -> int:
    cfg = load_config()
    cfg.setdefault("llm", {})["min_score"] = max(0, min(10, value))
    save_config(cfg)
    return cfg["llm"]["min_score"]


def letter_notes() -> list[str]:
    return load_config().get("llm", {}).get("letter_notes", [])


def add_letter_note(note: str) -> list[str]:
    """Добавить правку стиля письма — она уходит в промпт всех будущих писем."""
    cfg = load_config()
    notes = cfg.setdefault("llm", {}).setdefault("letter_notes", [])
    note = note.strip()
    if note and note not in notes:
        notes.append(note)
    cfg["llm"]["letter_notes"] = notes[-10:]  # держим последние 10, иначе промпт распухает
    save_config(cfg)
    return cfg["llm"]["letter_notes"]


def clear_letter_notes() -> None:
    cfg = load_config()
    cfg.setdefault("llm", {})["letter_notes"] = []
    save_config(cfg)


# ─────────────────────────── запуск прогонов ───────────────────────────

async def start_run(dry: bool = False) -> str:
    """Запустить прогон и сразу вернуться, НЕ дожидаясь конца.

    Раньше бот ждал завершения с таймаутом и по его истечении убивал процесс.
    Прогон длится дольше таймаута (описания + оценка + письма), и его прибивало
    на середине отправки — половина откликов не уходила, итог не записывался,
    а оборвать процесс между нажатием «Откликнуться» и подтверждением hh просто
    опасно. Теперь запускаем и отпускаем: за ходом следит живой экран, итог
    возьмётся из run_state.json, который скрипт закрывает сам при любом исходе.
    """
    args = [PYTHON, str(HH_DIR / "autoapply.py")]
    if dry:
        args.append("--dry")
    log_path = HH_DIR / "last_run.log"
    with open(log_path, "wb") as fh:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(HH_DIR), stdout=fh, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,   # переживёт перезапуск бота
        )
    return f"запущен (pid {proc.pid})"


def last_run_log(limit: int = 3000) -> str:
    try:
        return (HH_DIR / "last_run.log").read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


async def preview_letter(timeout: int = 200) -> str:
    """Показать, каким письмо получается сейчас — на последней вакансии из истории."""
    script = (
        "import analyzer, history, json\n"
        "conn = history.connect()\n"
        "row = conn.execute(\"SELECT id,title,company FROM applied WHERE title IS NOT NULL \"\n"
        "                   \"ORDER BY created_at DESC LIMIT 1\").fetchone()\n"
        "if not row: print('в истории нет вакансий для примера'); raise SystemExit\n"
        "from hhdriver import driver\n"
        "try:\n"
        "    d = driver('hh_get_vacancy', {'vacancy_id': row['id']}, tries=1)\n"
        "except Exception: d = None\n"
        "v = dict(row); v.update(d or {})\n"
        "if not v.get('description'): v['description'] = 'Разработка backend-сервисов на Java, Spring Boot, PostgreSQL.'\n"
        "prof = analyzer.profile_text('')\n"
        "fb = open('cover.txt').read().strip()\n"
        "text, gen = analyzer.cover_letter(v, prof, fb)\n"
        "print(f\"вакансия: {v.get('title')} — {v.get('company')}\")\n"
        "print('сгенерировано моделью' if gen else 'модель недоступна, показан шаблон cover.txt')\n"
        "print()\nprint(text)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        PYTHON, "-c", script, cwd=str(HH_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "⏱ модель не ответила вовремя"
    return (out or b"").decode(errors="replace").strip()[-3000:] or "пустой ответ"
