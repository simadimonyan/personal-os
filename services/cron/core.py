"""
Ядро cron-сервиса Personal OS.

Общая логика, которую используют демон (cron.py) и CLI (cronctl.py):
  - загрузка/сохранение задач (jobs.json) и состояния (state.json)
  - разбор расписаний (cron-выражения, @every, @daily ...)
  - проверка «пора ли запускать»
  - выполнение задачи (action) и доставка результата в приёмники (output)

Без внешних зависимостей — только стандартная библиотека.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import time
from pathlib import Path

from client import ask_claude, server_alive

# ── Пути ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
JOBS_FILE = BASE_DIR / "jobs.json"
STATE_FILE = BASE_DIR / "state.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "cron.log"
RUN_LOG = LOG_DIR / "runs.log"

OBSIDIAN_DRIVER = Path.home() / ".claude" / "skills" / "obsidian" / "driver.mjs"
TELEGRAM_DRIVER = Path.home() / ".claude" / "skills" / "telegram" / "driver.cjs"

# Сколько секунд между тиками демона (как часто проверять расписания).
TICK_SECONDS = int(os.environ.get("POS_CRON_TICK", "30"))
# Окно «догонки» пропущенных cron-запусков (минут). По умолчанию 8 суток —
# покрывает дневные/недельные задачи, пропущенные из-за сна/офлайна.
CATCHUP_WINDOW_MIN = int(os.environ.get("POS_CRON_CATCHUP_MIN", "11520"))
# Хост для проверки интернета (TCP-коннект, без DNS-зависимости).
NET_HOST = (os.environ.get("POS_CRON_NET_HOST", "1.1.1.1"), 443)


# ── Логирование ─────────────────────────────────────────────────────────────
def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, *, stream: bool = True) -> None:
    _ensure_dirs()
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if stream:
        print(line, flush=True)


# ── Хранилище задач и состояния ───────────────────────────────────────────────
def load_jobs() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    data = json.loads(JOBS_FILE.read_text(encoding="utf-8") or "[]")
    # допускаем как список задач, так и {"jobs": [...]}
    return data["jobs"] if isinstance(data, dict) else data


def save_jobs(jobs: list[dict]) -> None:
    JOBS_FILE.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}")


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def find_job(jobs: list[dict], job_id: str) -> dict | None:
    return next((j for j in jobs if j.get("id") == job_id), None)


# ── Разбор расписаний ────────────────────────────────────────────────────────
_SHORTCUTS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(schedule: str) -> int | None:
    """'@every 30m' / '@every 2h' / '@every 90s' → секунды. Иначе None."""
    s = schedule.strip()
    if not s.startswith("@every"):
        return None
    spec = s.split(None, 1)[1].strip().lower()
    unit = spec[-1]
    if unit not in _UNIT_SECONDS:
        raise ValueError(f"неизвестная единица интервала в '{schedule}' (нужно s/m/h/d)")
    return int(spec[:-1]) * _UNIT_SECONDS[unit]


def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    vals: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/")
            step = int(step_s)
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        vals.update(range(start, end + 1, step))
    return vals


def cron_matches(schedule: str, when: dt.datetime) -> bool:
    """Совпадает ли cron-выражение (5 полей) с моментом времени (с точностью до минуты)."""
    expr = _SHORTCUTS.get(schedule.strip(), schedule.strip())
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron-выражение должно иметь 5 полей: 'мин час день месяц день_недели', получено: '{schedule}'"
        )
    minute, hour, dom, mon, dow = fields
    cron_dow = (when.weekday() + 1) % 7  # cron: вс=0..сб=6
    dow_set = _parse_cron_field(dow, 0, 7)
    if 7 in dow_set:
        dow_set.add(0)
    return (
        when.minute in _parse_cron_field(minute, 0, 59)
        and when.hour in _parse_cron_field(hour, 0, 23)
        and when.day in _parse_cron_field(dom, 1, 31)
        and when.month in _parse_cron_field(mon, 1, 12)
        and cron_dow in dow_set
    )


def prev_scheduled(schedule: str, now: dt.datetime, window_min: int = CATCHUP_WINDOW_MIN):
    """Последний момент по cron-расписанию <= now (в пределах окна догонки). Иначе None."""
    cur = now.replace(second=0, microsecond=0)
    for i in range(window_min + 1):
        t = cur - dt.timedelta(minutes=i)
        if cron_matches(schedule, t):
            return t
    return None


def is_due(job: dict, state: dict, now: dt.datetime) -> bool:
    """Пора ли запускать задачу — с учётом пропущенных запусков (catch-up)."""
    if not job.get("enabled", True):
        return False
    schedule = job["schedule"]
    last = state.get(job["id"], {}).get("last_run")  # epoch float

    interval = parse_interval(schedule)
    if interval is not None:
        # интервальные и так догоняют: due, если прошло >= интервала
        return last is None or (now.timestamp() - last) >= interval

    # cron-расписание
    if last is None:
        # новая задача — только вперёд, без догонки прошлого слота
        return cron_matches(schedule, now)
    if not job.get("catchup", True):
        minute_start = now.replace(second=0, microsecond=0).timestamp()
        return cron_matches(schedule, now) and last < minute_start
    # с догонкой: есть ли запланированный момент в (last_run, now]
    t = prev_scheduled(schedule, now)
    return t is not None and last < t.timestamp()


def overdue_seconds(job: dict, state: dict, now: dt.datetime) -> float:
    """На сколько секунд задача просрочена (для приоритета). On-time ≈ 0..60."""
    last = state.get(job["id"], {}).get("last_run")
    if last is None:
        return 0.0
    interval = parse_interval(job["schedule"])
    if interval is not None:
        return max(0.0, (now.timestamp() - last) - interval)
    t = prev_scheduled(job["schedule"], now)
    return max(0.0, now.timestamp() - t.timestamp()) if t else 0.0


# ── Доступность ресурсов (сеть / claude) ──────────────────────────────────────
_net_cache = {"ts": 0.0, "up": False}


def internet_up(timeout: float = 2.0) -> bool:
    """TCP-коннект к надёжному хосту. Результат кешируется на 15с."""
    now = time.time()
    if now - _net_cache["ts"] < 15:
        return _net_cache["up"]
    try:
        with socket.create_connection(NET_HOST, timeout=timeout):
            up = True
    except OSError:
        up = False
    _net_cache.update(ts=now, up=up)
    return up


def requirements_met(job: dict) -> tuple[bool, list[str]]:
    """Проверить требования задачи (requires: internet|claude). Возвращает (ok, чего нет)."""
    missing = []
    for req in job.get("requires", []):
        if req == "internet" and not internet_up():
            missing.append("internet")
        elif req == "claude" and not server_alive():
            missing.append("claude")
    return (not missing, missing)


def describe_schedule(schedule: str) -> str:
    interval = None
    try:
        interval = parse_interval(schedule)
    except ValueError:
        pass
    if interval is not None:
        return f"каждые {interval}s"
    return _SHORTCUTS.get(schedule.strip(), schedule.strip())


# ── Приёмники результата (output) ─────────────────────────────────────────────
def _deliver(sink: dict, job: dict, text: str) -> str:
    """Доставить текст в один приёмник. Возвращает короткий статус для лога."""
    to = sink.get("to", "log")
    header = sink.get("header", f"## {job['id']} — {dt.datetime.now():%Y-%m-%d %H:%M}")

    if to == "log":
        return "log"

    if to == "file":
        path = Path(os.path.expanduser(sink["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n{header}\n{text}\n")
        return f"file:{path}"

    if to == "obsidian":
        content = f"\n{header}\n\n{text}\n"
        payload = json.dumps({"path": sink["path"], "content": content}, ensure_ascii=False)
        subprocess.run(
            ["node", str(OBSIDIAN_DRIVER), "obsidian_append", payload],
            check=True,
            capture_output=True,
            text=True,
        )
        return f"obsidian:{sink['path']}"

    if to == "telegram":
        # отправить текст результата в чат/канал через telegram driver
        chat = sink["chat"]
        payload = {"chat_id": chat, "text": text}
        if sink.get("parse_mode"):
            payload["parse_mode"] = sink["parse_mode"]
        proc = subprocess.run(
            ["node", str(TELEGRAM_DRIVER), "send_message", json.dumps(payload, ensure_ascii=False)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or '"success": true' not in proc.stdout:
            raise RuntimeError(f"telegram send failed: {(proc.stdout + proc.stderr).strip()[:300]}")
        return f"telegram:{chat}"

    raise ValueError(f"неизвестный приёмник output.to='{to}'")


# ── Отчётность в Telegram (Избранное / Saved Messages) ────────────────────────
# По умолчанию каждая задача после выполнения шлёт отчёт в Избранное аккаунта.
# Отключить глобально: POS_CRON_REPORT=0 ; для одной задачи: "report": false.
DEFAULT_REPORT = os.environ.get("POS_CRON_REPORT", "1") == "1"


def report_saved(text: str) -> None:
    """Отправить отчёт в Избранное (Saved Messages) своего аккаунта."""
    payload = json.dumps({"chat_id": "me", "text": text}, ensure_ascii=False)
    proc = subprocess.run(
        ["node", str(TELEGRAM_DRIVER), "send_message", payload],
        capture_output=True, text=True, timeout=60,
    )
    if '"success": true' not in proc.stdout:
        raise RuntimeError((proc.stdout + proc.stderr)[:200])


def _maybe_report(job: dict, *, ok: bool, ms: int, reason: str,
                  preview: str = "", error: str = "") -> None:
    if not job.get("report", DEFAULT_REPORT):
        return
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = "✅ выполнено" if ok else "❌ ошибка"
    body = preview if ok else error
    desc = job.get("description") or job["id"]
    text = (f"🤖 Personal OS · отчёт\n{head}: {job['id']}\n"
            f"🕒 {ts} · {ms}ms · {reason}\n{desc}\n\n{body}".strip())
    try:
        report_saved(text)
    except Exception as e:  # noqa: BLE001 — отчёт не должен ронять задачу
        log(f"⚠ отчёт '{job['id']}' в TG не отправлен: {e}")


# ── Выполнение задачи ────────────────────────────────────────────────────────
def run_action(job: dict) -> str:
    """Выполнить action задачи и вернуть текстовый результат."""
    action = job["action"]
    kind = action["type"]

    if kind == "prompt":
        return ask_claude(action["prompt"], model=action.get("model"))

    if kind == "shell":
        proc = subprocess.run(
            action["command"],
            shell=True,
            capture_output=True,
            text=True,
            cwd=action.get("cwd"),
            timeout=action.get("timeout", 600),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(f"shell exit {proc.returncode}:\n{out.strip()}")
        return out.strip()

    raise ValueError(f"неизвестный тип action.type='{kind}'")


def run_job(job: dict, state: dict, *, reason: str = "scheduled") -> bool:
    """Запустить задачу, разложить результат по приёмникам, обновить состояние."""
    job_id = job["id"]
    t0 = time.perf_counter()
    log(f"▶ запуск '{job_id}' ({reason})")
    record = state.setdefault(job_id, {})
    try:
        result = run_action(job)
        sinks = job.get("output", [])
        if isinstance(sinks, dict):
            sinks = [sinks]
        if not sinks:
            sinks = [{"to": "log"}]
        delivered = [_deliver(s, job, result) for s in sinks]

        ms = int((time.perf_counter() - t0) * 1000)
        preview = result.replace("\n", " ")[:160]
        log(f"✓ '{job_id}' ok за {ms}ms → {', '.join(delivered)} | {preview}")
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}\tOK\t{job_id}\t{ms}ms\n")
        record.update(last_run=time.time(), last_status="ok", last_ms=ms)
        # Успех очищает следы прошлой ошибки/отсрочки: иначе в state остаётся
        # «протухший» last_error от давнего падения, и пульт ложно покажет degraded
        # (B1/T1 редизайна Mission Control). Непустой last_error при ok отныне = ТОЛЬКО
        # свежая ошибка текущего запуска — это и делает правило degraded честным.
        record.pop("last_error", None)
        record.pop("last_error_run_ts", None)
        record.pop("last_missing", None)
        _maybe_report(job, ok=True, ms=ms, reason=reason, preview=preview)
        return True
    except Exception as e:  # noqa: BLE001 — задача не должна ронять демон
        ms = int((time.perf_counter() - t0) * 1000)
        log(f"✗ '{job_id}' ОШИБКА за {ms}ms: {e}")
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}\tERR\t{job_id}\t{e}\n")
        # Привязываем ошибку к конкретному запуску: last_error_run_ts == last_run.
        # Это второй слой защиты (вариант 2 из B1) — даже если где-то ещё останется
        # стейл-ошибка, gather() сможет отличить свежую от устаревшей.
        err_ts = time.time()
        record.update(last_run=err_ts, last_status="error",
                      last_error=str(e), last_error_run_ts=err_ts)
        _maybe_report(job, ok=False, ms=ms, reason=reason, error=str(e))
        return False
    finally:
        save_state(state)


def tick(now: dt.datetime | None = None) -> int:
    """
    Один проход планировщика.

    Логика устойчивости к офлайну:
      - собираем все готовые задачи (включая просроченные из-за сна/отсутствия сети);
      - сортируем по просрочке — самые «опоздавшие» выполняются первыми (приоритет);
      - если задаче нужен недоступный ресурс (нет интернета / не поднят claude),
        её НЕ запускаем и НЕ помечаем выполненной — она остаётся просроченной и
        выполнится приоритетно на ближайшем тике, как только ресурс появится.
    """
    now = now or dt.datetime.now()
    jobs = load_jobs()
    state = load_state()

    due = []
    for job in jobs:
        try:
            if is_due(job, state, now):
                due.append(job)
        except Exception as e:  # noqa: BLE001 — плохое расписание не валит остальные
            log(f"⚠ задача '{job.get('id', '?')}': ошибка расписания: {e}")
    if not due:
        return 0

    # приоритет: сначала самые просроченные
    due.sort(key=lambda j: overdue_seconds(j, state, now), reverse=True)

    ran = 0
    deferred: list[str] = []
    for job in due:
        ok, missing = requirements_met(job)
        if not ok:
            rec = state.setdefault(job["id"], {})
            rec.update(last_status="deferred", last_missing=",".join(missing),
                       deferred_since=rec.get("deferred_since") or time.time())
            save_state(state)
            deferred.append(f"{job['id']}(нет: {','.join(missing)})")
            continue
        late = overdue_seconds(job, state, now)
        reason = f"catch-up, опоздание {int(late)}s" if late > 90 else "scheduled"
        # сбросить отметку отсрочки
        state.get(job["id"], {}).pop("deferred_since", None)
        run_job(job, state, reason=reason)
        ran += 1

    if deferred:
        log(f"⏸ отложено {len(deferred)}: {', '.join(deferred)} — выполню, как появится ресурс")
    return ran
