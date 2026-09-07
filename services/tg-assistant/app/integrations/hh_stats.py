"""Статистика автоотклика: воронка, прогоны, конверсия.

Два источника, каждый знает своё:
  • history.db          — что случилось с вакансиями (воронка, оценки, компании)
  • cron/logs/runs.log  — что случилось с прогонами (TSV: время, OK/ERR, job, длительность)

Периоды считаются в SQL по created_at, а не в Python: строк уже сотни, и
дальше их будет только больше.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.integrations.hh_agent import DB_PATH, HHUnavailable, _connect

RUNS_LOG = Path.home() / "Desktop" / "personal os" / "services" / "cron" / "logs" / "runs.log"
# Набор задач автоотклика меняется из бота (1–4 прогона в день), поэтому
# сопоставляем по префиксу, а не по фиксированному списку id.
JOB_PREFIX = "hh-autoapply"

PERIODS = {"week": ("неделю", 7), "month": ("месяц", 30), "all": ("всё время", None)}

# Как исходы из applied.outcome называются для человека
OUTCOME_LABELS = {
    "applied": "отклики отправлены",
    "rejected_by_llm": "отсеяно моделью",
    "unconfirmed": "требуют ручного отклика",
    "skip": "пропущено (ошибки)",
    "already": "уже откликались раньше",
    "legacy": "из старой истории",
}


def _since(days: int | None) -> str:
    """SQL-условие по периоду. None = без ограничения."""
    return "1=1" if days is None else f"created_at >= datetime('now','-{days} days')"


def funnel(days: int | None) -> dict:
    """Воронка по вакансиям за период."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT outcome, COUNT(*) c FROM applied WHERE {_since(days)} GROUP BY outcome"
        ).fetchall()
        by_outcome = {r["outcome"] or "unknown": r["c"] for r in rows}

        scored = conn.execute(
            f"SELECT COUNT(*) c, AVG(score) avg, MIN(score) lo, MAX(score) hi "
            f"FROM applied WHERE score IS NOT NULL AND {_since(days)}"
        ).fetchone()

        top_companies = conn.execute(
            f"SELECT company, COUNT(*) c FROM applied "
            f"WHERE outcome='applied' AND company IS NOT NULL AND {_since(days)} "
            f"GROUP BY company ORDER BY c DESC, company LIMIT 5"
        ).fetchall()

        # Распределение оценок — видно, где стоит порог и что он режет
        buckets = conn.execute(
            f"SELECT score, COUNT(*) c FROM applied "
            f"WHERE score IS NOT NULL AND {_since(days)} GROUP BY score ORDER BY score"
        ).fetchall()

        letters = conn.execute(
            f"SELECT COUNT(*) c FROM applied "
            f"WHERE outcome='applied' AND letter != '' AND {_since(days)}"
        ).fetchone()["c"]

    return {
        "by_outcome": by_outcome,
        "scored": scored["c"], "avg_score": scored["avg"],
        "lo": scored["lo"], "hi": scored["hi"],
        "top_companies": [dict(r) for r in top_companies],
        "buckets": {r["score"]: r["c"] for r in buckets},
        "with_letter": letters,
    }


def responses() -> dict:
    """Ответы работодателей — по последнему известному статусу (без периода:
    hh не отдаёт дату смены статуса, только текущее состояние отклика)."""
    with _connect() as conn:
        rows = conn.execute("SELECT kind, COUNT(*) c FROM negotiations GROUP BY kind").fetchall()
        by_kind = {r["kind"] or "unknown": r["c"] for r in rows}
        invites = conn.execute(
            "SELECT title, company, status FROM negotiations WHERE kind='invite' "
            "ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
    return {"by_kind": by_kind, "invites": [dict(r) for r in invites]}


def runs(days: int | None) -> dict:
    """История прогонов из cron/logs/runs.log."""
    if not RUNS_LOG.exists():
        return {"ok": 0, "err": 0, "total": 0, "avg_sec": None, "last_errors": [], "by_day": {}}

    cutoff = datetime.now() - timedelta(days=days) if days else None
    ok = err = 0
    durations: list[int] = []
    last_errors: list[tuple[str, str]] = []
    by_day: dict[str, int] = {}

    for line in RUNS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0][:4].isdigit():
            continue
        stamp, status, job = parts[0], parts[1], parts[2]
        if not job.startswith(JOB_PREFIX):
            continue
        try:
            when = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if cutoff and when < cutoff:
            continue

        day = when.date().isoformat()
        if status == "OK":
            ok += 1
            by_day[day] = by_day.get(day, 0) + 1
            if len(parts) > 3:
                m = re.match(r"(\d+)ms", parts[3])
                if m:
                    durations.append(int(m.group(1)))
        else:
            err += 1
            reason = (parts[3] if len(parts) > 3 else "").strip()[:60]
            last_errors.append((stamp, reason or "без описания"))

    return {
        "ok": ok, "err": err, "total": ok + err,
        "avg_sec": round(sum(durations) / len(durations) / 1000) if durations else None,
        "last_errors": last_errors[-3:],
        "by_day": by_day,
    }


def collect(period: str = "week") -> dict:
    """Всё вместе для одного экрана статистики."""
    if period not in PERIODS:
        period = "week"
    label, days = PERIODS[period]
    if not DB_PATH.exists():
        raise HHUnavailable("история откликов пуста — автоотклик ещё ни разу не отработал")
    try:
        data = {"period": period, "label": label,
                "funnel": funnel(days), "runs": runs(days), "responses": responses()}
    except sqlite3.Error as e:
        raise HHUnavailable(f"база истории не читается: {e}") from e
    return data
