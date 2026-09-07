#!/usr/bin/env python3
"""
Смысловой слой автоотклика: отсев вакансий по описанию и персональные письма.

Идея из reference-проекта fikstt2/hh-ai-agent (ai_analyzer.py), переложенная
на claude-local-api и на два наблюдения из его граблей:

1. Батч вместо поштучного разбора. Там каждая вакансия — отдельный запрос к
   модели; здесь одним запросом оцениваются сразу N штук (~9с на запрос, так
   что 25 поштучных вызовов — это 4 минуты на пустом месте).
2. Оценка 0–10 с обоснованием вместо YES/NO. Бинарный ответ не даёт понять,
   почему выдача сузилась, и порог нельзя подкрутить, не переписав промпт.

Профиль кандидата берётся из profile.txt; если его нет — из текста резюме на
hh (и кэшируется в тот же файл, чтобы не дёргать браузер каждый прогон).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import llm
from hhdriver import BASE, CONFIG, driver

PROFILE_FILE = BASE / "profile.txt"
COVER_FILE = BASE / "cover.txt"

# Описание вакансии режем: смысл для оценки весь в первых абзацах (требования,
# стек), дальше обычно соцпакет и реклама компании — они только жгут контекст.
DESC_LIMIT = 1800


def llm_cfg() -> dict:
    return CONFIG.get("llm", {})


def enabled() -> bool:
    return bool(llm_cfg().get("enabled", True)) and llm.available()


# Страницы-заглушки hh (блок по VPN, капча, «страница не найдена») отдаются с
# кодом 200 и коротким текстом. Без этой проверки такая заглушка кэшировалась в
# profile.txt и дальше уходила в промпты как «профиль кандидата».
STUB_MARKERS = ("vpn мешает", "отключите его", "код для поддержки",
                "проверка безопасности", "страница не найдена", "captcha")
MIN_PROFILE_LEN = 500


def _looks_like_resume(text: str) -> bool:
    low = text.lower()
    return len(text) >= MIN_PROFILE_LEN and not any(m in low for m in STUB_MARKERS)


def profile_text(resume_id: str) -> str:
    """Профиль кандидата для промптов. Кэшируется в profile.txt — файл можно править руками."""
    if PROFILE_FILE.exists():
        text = PROFILE_FILE.read_text(encoding="utf-8").strip()
        if _looks_like_resume(text):
            return text
        PROFILE_FILE.unlink()  # закэширована заглушка — выбросить и перечитать
    try:
        resume = driver("hh_get_resume_text", {"resume_id": resume_id}, tries=2)
        text = re.sub(r"\n{3,}", "\n\n", str(resume or "").strip())[:6000]
    except RuntimeError:
        text = ""
    if not _looks_like_resume(text):
        # резюме не отдалось (VPN/капча) — работаем по статичному письму, в нём перечислен стек
        return COVER_FILE.read_text(encoding="utf-8").strip()
    PROFILE_FILE.write_text(text, encoding="utf-8")
    return text


def _fmt_vacancy(i: int, v: dict) -> str:
    desc = (v.get("description") or "").strip()[:DESC_LIMIT]
    return (f"### Вакансия {i}\n"
            f"Название: {v.get('title')}\n"
            f"Компания: {v.get('company')}\n"
            f"Зарплата: {v.get('salary') or 'не указана'}\n"
            f"Опыт: {v.get('experience') or 'не указан'}\n"
            f"Описание: {desc or 'описание не загрузилось'}\n")


def screen_batch(vacancies: list[dict], profile: str) -> dict[str, dict]:
    """Оценить пачку вакансий одним запросом.

    Возвращает {vacancy_id: {"score": int, "reason": str}}. При недоступности
    модели бросает LLMUnavailable — вызывающий откатывается на жёсткие фильтры.
    """
    if not vacancies:
        return {}
    listing = "\n".join(_fmt_vacancy(i + 1, v) for i, v in enumerate(vacancies))
    criteria = "\n".join(f"- {c}" for c in llm_cfg().get("criteria", []))
    prompt = f"""Ты — придирчивый технический рекрутер, отбирающий вакансии для конкретного кандидата.

ПРОФИЛЬ КАНДИДАТА:
{profile}

ДОПОЛНИТЕЛЬНЫЕ КРИТЕРИИ ОТБОРА:
{criteria or '- нет'}

Оцени каждую вакансию ниже по шкале 0–10: насколько она подходит этому кандидату.
10 — идеальное совпадение стека и уровня; 0 — вакансия вообще не про разработку на его стеке.

Снижай оценку, если:
- уровень Senior/Lead/Архитектор или требуется опыт заметно больше, чем у кандидата;
- основной стек вакансии не пересекается со стеком кандидата;
- это не про написание кода (менеджмент, поддержка, продажи, аналитика, преподавание);
- описание — пустышка без конкретики о задачах.

{listing}

Ответь ТОЛЬКО JSON-массивом без markdown и пояснений, ровно {len(vacancies)} элементов, по порядку:
[{{"n": 1, "score": 7, "reason": "краткое обоснование одной фразой на русском"}}]"""

    data = llm.ask_json(prompt, model=llm_cfg().get("model"), timeout=180)
    if not isinstance(data, list):
        raise llm.LLMUnavailable("ожидался JSON-массив оценок")

    out: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("n", 0)) - 1
            score = int(item.get("score", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(vacancies):
            vid = str(vacancies[idx].get("id"))
            out[vid] = {"score": max(0, min(10, score)),
                        "reason": str(item.get("reason", ""))[:300]}
    return out


def cover_letter(vacancy: dict, profile: str, fallback: str) -> tuple[str, bool]:
    """Персональное сопроводительное под вакансию. → (текст, сгенерировано_ли_моделью).

    Любой сбой или подозрительный результат → статичное письмо из cover.txt:
    лучше отправить проверенный текст, чем галлюцинацию модели.
    """
    desc = (vacancy.get("description") or "").strip()[:DESC_LIMIT]
    if not desc:
        return fallback, False

    # Правки стиля, надиктованные из Telegram (/hh → «Письмо»). Идут последними,
    # чтобы перебивать общие правила: это прямые указания хозяина письма.
    notes = llm_cfg().get("letter_notes", [])
    notes_block = ("\nОСОБЫЕ УКАЗАНИЯ (важнее общих правил выше):\n"
                   + "\n".join(f"- {n}" for n in notes)) if notes else ""

    prompt = f"""Напиши сопроводительное письмо для отклика на вакансию на hh.ru.

ПРОФИЛЬ КАНДИДАТА (только отсюда бери факты):
{profile}

ВАКАНСИЯ:
Название: {vacancy.get('title')}
Компания: {vacancy.get('company')}
Описание: {desc}

ПРАВИЛА:
1. Только на русском языке.
2. 3–4 коротких абзаца, без списков и заголовков.
3. Тон живой и профессиональный, без канцелярита и лести.
4. Свяжи опыт кандидата с конкретными задачами и стеком ИЗ ЭТОЙ вакансии — назови их.
5. НИКОГДА не выдумывай опыт, технологии, компании и сроки, которых нет в профиле.
6. Начни с обращения «Здравствуйте!», закончи строкой «С уважением, Димитри.»
7. Выведи ТОЛЬКО текст письма. Никаких вводных фраз, кавычек и комментариев.
{notes_block}

Образец тона (не копируй содержание):
{fallback}"""

    try:
        text = llm.ask(prompt, model=llm_cfg().get("model"), timeout=180).strip()
    except llm.LLMUnavailable:
        return fallback, False

    text = re.sub(r"^```.*?\n|```$", "", text, flags=re.DOTALL).strip()
    # Санитарная проверка: длина в разумных рамках, есть подпись, нет следов
    # английского «вот ваше письмо» и не осталось плейсхолдеров вроде [компания].
    bad = ("here is", "here's", "sure,", "конечно,", "вот письмо", "[", "{")
    lowered = text.lower()
    if not (300 <= len(text) <= 2500) or "димитри" not in lowered or any(b in lowered for b in bad):
        return fallback, False
    return text, True


if __name__ == "__main__":
    # Ручная проверка: python3 analyzer.py — покажет статус модели и профиль
    print("llm доступен:", llm.available(), "| включён в конфиге:", llm_cfg().get("enabled", True))
    p = profile_text(CONFIG.get("resume_id", ""))
    print(f"профиль: {len(p)} символов, источник:",
          "profile.txt" if PROFILE_FILE.exists() else "резюме hh / cover.txt")
    print(json.dumps(llm_cfg(), ensure_ascii=False, indent=2))
