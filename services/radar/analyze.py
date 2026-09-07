"""
Синтез трендов из собранных материалов.

Если поднят claude-local-api — сводит всё в осмысленный дайджест трендов
(группировка по темам + «почему важно»). Если сервер недоступен — отдаёт
сырой дайджест, сгруппированный по источникам (graceful fallback).
"""
from __future__ import annotations

import datetime as dt

from client import ask_claude, server_alive

PROMPT = """Ты — аналитик инфополя. Ниже сырые материалы за последние сутки из RSS-лент и Telegram-каналов.

Задача: выделить ГЛАВНЫЕ тренды и темы (не пересказывать всё подряд).
Сгруппируй по темам. Для каждого тренда:
- заголовок темы (жирным);
- 1–2 предложения сути;
- «почему важно» — одна строка, чем это значимо.
В конце — блок «⚡ На радар» из 3–5 пунктов: что стоит изучить/сделать.

Пиши по-русски, плотно, без воды и без вступлений. Формат — Markdown.

=== МАТЕРИАЛЫ ===
{items}
"""


def _format_items(items: list[dict]) -> str:
    lines = []
    for it in items:
        tag = "TG" if it["kind"] == "telegram" else "RSS"
        lines.append(f"[{tag}] ({it['source']}) {it['title']} — {it['text'][:200]}")
    return "\n".join(lines)


def _raw_digest(items: list[dict]) -> str:
    by_src: dict[str, list[dict]] = {}
    for it in items:
        by_src.setdefault(it["source"], []).append(it)
    out = ["_(claude-local-api недоступен — сырой дайджест без синтеза)_\n"]
    for src, group in by_src.items():
        out.append(f"### {src}")
        for it in group[:10]:
            link = f" — {it['url']}" if it["url"].startswith("http") else ""
            out.append(f"- {it['title']}{link}")
        out.append("")
    return "\n".join(out)


def build_digest(items: list[dict], model: str = "sonnet") -> str:
    if not items:
        return "_(новых материалов за период нет)_"
    if server_alive():
        try:
            return ask_claude(PROMPT.format(items=_format_items(items)), model=model)
        except Exception as e:  # noqa: BLE001 — не падаем, отдаём сырьё
            return f"_(синтез не удался: {e})_\n\n" + _raw_digest(items)
    return _raw_digest(items)


def header(rss_n: int, tg_n: int) -> str:
    return (
        f"## 📡 Инфополе — {dt.datetime.now():%Y-%m-%d %H:%M}  "
        f"(RSS: {rss_n} · TG: {tg_n})"
    )
