"""Рендер списка задач для бота — красивый ТЕКСТ (не стена кнопок).

Возвращает (text, order) где order — список uid в порядке вывода: i-я задача в
тексте = order[i-1], номерные кнопки строятся по нему. Так нумерация устойчива
к смене вида/фильтра — кнопка несёт uid, а цифра лишь косметика.

Виды (view):
  time      — по времени: ⚠️ Просрочено / 📅 Сегодня / 📆 Предстоящее / 📥 Без срока
  today     — только сегодня (+ просроченное)
  upcoming  — только будущие датированные
  projects  — по проектам Todoist
  priority  — по приоритету p1..p4
  labels    — по меткам (@label)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

_PRIO_EMOJI = {4: "🔴", 3: "🟠", 2: "🔵", 1: "⚪"}
_PRIO_NAME = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}
_MONTHS = ["", "янв", "фев", "мар", "апр", "май", "июн",
           "июл", "авг", "сен", "окт", "ноя", "дек"]


@dataclass
class _Task:
    uid: str
    content: str
    priority: int
    due_date: str | None
    due_datetime: str | None
    project_id: str | None
    labels: list[str]


def prio_emoji(p: int) -> str:
    return _PRIO_EMOJI.get(p, "⚪")


def _due_d(t: _Task) -> date | None:
    if t.due_date:
        try:
            return date.fromisoformat(t.due_date)
        except ValueError:
            return None
    return None


def _time_of(t: _Task) -> str | None:
    if t.due_datetime and "T" in t.due_datetime:
        try:
            return datetime.fromisoformat(t.due_datetime).strftime("%H:%M")
        except ValueError:
            return None
    return None


def _fmt_due(t: _Task, today: date) -> str:
    d = _due_d(t)
    if d is None:
        return ""
    tm = _time_of(t)
    if d == today:
        return f"⏰{tm}" if tm else "сегодня"
    label = f"{d.day} {_MONTHS[d.month]}" + (f" {tm}" if tm else "")
    if d < today:
        return f"⚠️{label}"
    return f"· {label}"


def _line(idx: int, t: _Task, today: date, *, with_due: bool = True) -> str:
    due = f"  {_fmt_due(t, today)}" if with_due else ""
    return f" {idx}. {prio_emoji(t.priority)} {t.content}{due}".rstrip()


def _sort_key(t: _Task) -> tuple:
    d = _due_d(t)
    return (-t.priority, d or date.max, t.content.lower())


def _to_tasks(rows: list) -> list[_Task]:
    return [_Task(r.uid, r.content, r.priority, r.due_date, r.due_datetime,
                  r.project_id, list(r.labels or [])) for r in rows]


def render(
    view: str,
    rows: list,
    project_names: dict[str, str],
    *,
    today: date | None = None,
) -> tuple[str, list[str]]:
    """rows — активные (не выполненные, не архивные) TaskRow. Возвращает (text, order)."""
    today = today or date.today()
    tasks = _to_tasks(rows)
    n = len(tasks)
    header = f"📋 Задачи · {n} активных" if n else "📋 Задачи\n\nПусто. Добавь первую задачу."
    if not tasks:
        return header, []

    sections: list[tuple[str, list[_Task]]] = []
    if view in ("time", "today", "upcoming"):
        overdue, td, upcoming, nodate = [], [], [], []
        for t in tasks:
            d = _due_d(t)
            if d is None:
                nodate.append(t)
            elif d < today:
                overdue.append(t)
            elif d == today:
                td.append(t)
            else:
                upcoming.append(t)
        if view == "today":
            sections = [("⚠️ Просрочено", overdue), ("📅 Сегодня", td)]
        elif view == "upcoming":
            sections = [("📆 Предстоящее", upcoming)]
        else:
            sections = [("⚠️ Просрочено", overdue), ("📅 Сегодня", td),
                        ("📆 Предстоящее", upcoming), ("📥 Без срока", nodate)]
    elif view == "projects":
        groups: dict[str, list[_Task]] = {}
        for t in tasks:
            name = project_names.get(t.project_id or "", "📥 Inbox") if t.project_id else "📥 Inbox"
            groups.setdefault(name, []).append(t)
        sections = [(f"🗂 {name}", ts) for name, ts in sorted(groups.items())]
    elif view == "priority":
        for p in (4, 3, 2, 1):
            ts = [t for t in tasks if t.priority == p]
            sections.append((f"{prio_emoji(p)} {_PRIO_NAME[p]}", ts))
    elif view == "labels":
        groups = {}
        for t in tasks:
            if t.labels:
                for lab in t.labels:
                    groups.setdefault(lab, []).append(t)
            else:
                groups.setdefault("без меток", []).append(t)
        sections = [(f"🏷 {name}", ts) for name, ts in sorted(groups.items())]
    else:
        sections = [("", tasks)]

    lines = [header]
    order: list[str] = []
    idx = 1
    for title, ts in sections:
        if not ts:
            continue
        ts = sorted(ts, key=_sort_key)
        lines.append("")
        if title:
            lines.append(title)
        for t in ts:
            lines.append(_line(idx, t, today))
            order.append(t.uid)
            idx += 1
    return "\n".join(lines), order
