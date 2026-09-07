"""Рендер телеграфного нарратива по слотам с метками времени (§4.2 MASTER-PLAN).

Каждый слот = секция с заголовком `## <emoji> <Слот> · HH:MM`.
Тело — телеграфно, фактами, без эссе.

Секции имеют машинно-распознаваемый якорь по эмодзи+слову слота — это позволяет
edit_last находить и заменять последнюю секцию (read-modify-write).
"""

from __future__ import annotations

from typing import Any

SLOT_HEADERS = {
    "morning": ("☀️", "Утро"),
    "day": ("🌤", "День"),
    "evening": ("🌙", "Вечер"),
    "situational": ("⚡", "Ситуативно"),
    "note": ("📝", "Заметка"),
    "agency": ("🧭", "Развилка"),
    "deed": ("📌", "Поступок"),
    "habits": ("🌱", "Привычки"),
}

# --- агентность / авторство (действия, не состояние) ---
_AGENCY_FORK_RU = {
    "sam": "решил сам",
    "obey": "подчинился",
    "spite": "поспорил-назло",
    "none": "развилки не было",
}
_ADDRESSEE_RU = {
    "close": "близкие",
    "friends": "друзья",
    "work": "дело",
    "self": "сам с собой",
}
_BASE_RU = {"sleep": "сон", "body": "тело", "walk": "ходьба"}
_DEED_KIND_RU = {
    "A": "столкнулся / накрыло",
    "B": "сказал тяжёлое / попросил",
    "C": "начал контакт первым",
}
_DEED_ACTION_RU = {
    "out": "нашёл выход",
    "swallow": "проглотил из страха",
    "burst": "сорвался",
}
_DEED_ADDRESSEE_RU = {"support": "тому, кто поддержит", "judge": "тому, кто оценивает"}

# человекочитаемые подписи для машинных ключей (для нарратива)
_EMOTION_RU = {
    "anxiety": "тревога",
    "shame": "стыд",
    "envy": "зависть",
    "loneliness": "одиночество",
    "anger": "злость",
    "sadness": "грусть",
    "apathy": "апатия",
    "joy": "радость",
    "calm": "спокойствие",
    "hope": "надежда",
    "unreadable": "не считывается",
}

_RUMINATION_RU = {0: "нет", 1: "немного", 2: "сильно"}
_CONTACT_RU = {
    "да_глубокий": "глубокий",
    "да_поверхностный": "поверхностный",
    "нет": "не было",
}


def fmt_valence(v: int) -> str:
    """Тон: 0 -> «0», положительные -> «+N», отрицательные -> «−N» (мин. знак U+2212)."""
    if v == 0:
        return "0"
    if v > 0:
        return f"+{v}"
    return f"−{abs(v)}"


def _ru_emotions(values: Any) -> str:
    if not values:
        return ""
    items = values if isinstance(values, (list, tuple)) else [values]
    return ", ".join(_EMOTION_RU.get(v, str(v)) for v in items)


def _ru_zones(values: Any) -> str:
    if not values:
        return ""
    items = values if isinstance(values, (list, tuple)) else [values]
    pretty = [v.replace("_", " ") for v in items]
    return ", ".join(pretty)


def _ru_list(values: Any) -> str:
    if not values:
        return ""
    items = values if isinstance(values, (list, tuple)) else [values]
    return ", ".join(str(v).replace("_", " ") for v in items)


def section_header(slot: str, hhmm: str) -> str:
    emoji, name = SLOT_HEADERS.get(slot, ("•", slot))
    return f"## {emoji} {name} · {hhmm}"


def render_section(slot: str, hhmm: str, answers: dict[str, Any], free_text: str | None = None) -> str:
    """Рендерит одну секцию слота телеграфно."""
    lines: list[str] = [section_header(slot, hhmm)]
    a = answers

    if slot == "morning":
        bits = []
        if a.get("valence") is not None:
            bits.append(f"Тон {fmt_valence(a['valence'])}")
        if a.get("arousal") is not None:
            bits.append(f"Энергия {a['arousal']}")
        if bits:
            lines.append(" / ".join(bits) + ".")
        if a.get("sleep_quality"):
            lines.append(f"Сон: {a['sleep_quality']}.")
        em = _ru_emotions(a.get("emotions"))
        if em:
            lines.append(f"Первая эмоция: {em}.")
        body_bits = []
        if a.get("body_word"):
            body_bits.append(f"«{a['body_word']}»")
        zones = _ru_zones(a.get("body_tension"))
        if zones:
            body_bits.append(f"({zones})")
        if body_bits:
            lines.append("Тело: " + " ".join(body_bits) + ".")

    elif slot == "day":
        if a.get("arousal") is not None:
            lines.append(f"Энергия {a['arousal']}.")
        em = _ru_emotions(a.get("emotions"))
        if em:
            lines.append(f"{em.capitalize()}.")
        if a.get("trigger_type") and a["trigger_type"] not in ("не_было", "неизвестно"):
            lines.append(f"Поддело: {a['trigger_type'].replace('_', ' ')}.")
        zones = _ru_zones(a.get("body_tension"))
        if zones:
            lines.append(f"Тело: {zones}.")

    elif slot == "evening":
        bits = []
        if a.get("valence") is not None:
            bits.append(f"Тон {fmt_valence(a['valence'])}")
        if bits:
            lines.append(" / ".join(bits) + ".")
        sub = []
        if a.get("anxiety") is not None:
            sub.append(f"Тревога {a['anxiety']}/10")
        if a.get("self_criticism") is not None:
            sub.append(f"Критик {a['self_criticism']}/10")
        if sub:
            lines.append(" · ".join(sub) + ".")
        if a.get("rumination_level") is not None:
            rl = _RUMINATION_RU.get(a["rumination_level"], str(a["rumination_level"]))
            topics = _ru_list(a.get("rumination_topics"))
            if topics:
                lines.append(f"Руминации: {topics} — {rl}.")
            else:
                lines.append(f"Руминации: {rl}.")
        if a.get("felt_vs_analyzed") is not None:
            lines.append(f"Прожил/продумал: {a['felt_vs_analyzed']}/10.")
        if a.get("human_contact"):
            lines.append(f"Контакт: {_CONTACT_RU.get(a['human_contact'], a['human_contact'])}.")
        reg = _ru_list(a.get("regulation_used"))
        if reg:
            lines.append(f"Помогло: {reg}.")
        if a.get("resource_note"):
            lines.append(f"Ресурс дня: {a['resource_note']}.")

    elif slot == "situational":
        em = _ru_emotions(a.get("emotions"))
        trig = a.get("trigger_type")
        head = em or (trig.replace("_", " ") if trig else "")
        zones = _ru_zones(a.get("body_tension"))
        parts = []
        if head:
            parts.append(f"Накрыло: {head}")
        if zones:
            parts.append(zones)
        if a.get("intensity") is not None:
            parts.append(f"интенсивность {a['intensity']}/10")
        if parts:
            lines.append(". ".join(parts) + ".")

    elif slot == "agency":
        fork = a.get("agency_fork")
        if fork:
            fork_ru = _AGENCY_FORK_RU.get(fork, fork)
            addr = a.get("agency_addressee")
            if fork != "none" and addr:
                lines.append(f"Развилка: {fork_ru} (кому: {_ADDRESSEE_RU.get(addr, addr)}).")
            else:
                lines.append(f"Развилка: {fork_ru}.")
        base = a.get("base_done")
        if base is not None:
            items = base if isinstance(base, (list, tuple)) else [base]
            if items:
                lines.append("База: " + ", ".join(_BASE_RU.get(b, b) for b in items) + ".")
            else:
                lines.append("База: не было.")

    elif slot == "habits":
        done = a.get("habits_done") or []
        slip = a.get("habits_slip") or []
        if done:
            lines.append("Сделал: " + ", ".join(done) + ".")
        if slip:
            lines.append("Сорвался: " + ", ".join(slip) + ".")
        if not done and not slip:
            lines.append("Сегодня ничего не отметил.")

    elif slot == "deed":
        kind = a.get("deed_kind")
        if kind:
            lines.append(f"{_DEED_KIND_RU.get(kind, kind).capitalize()}.")
        action = a.get("deed_action")
        if action:
            lines.append(f"Что сделал: {_DEED_ACTION_RU.get(action, action)}.")
        addr = a.get("deed_addressee")
        if addr:
            lines.append(f"Кому: {_DEED_ADDRESSEE_RU.get(addr, addr)}.")

    # свободный текст / заметка пользователя — телеграфно курсивом-цитатой
    if free_text:
        lines.append(f"> {free_text.strip()}")
    elif a.get("free_text"):
        lines.append(f"> {str(a['free_text']).strip()}")

    return "\n".join(lines).rstrip()


def render_title(date: str) -> str:
    return f"# Состояние · {date}"


def append_section(body: str, section: str) -> str:
    """Дописывает секцию в конец тела, отделяя пустой строкой."""
    body = body.rstrip()
    if not body:
        return section + "\n"
    return body + "\n\n" + section + "\n"
