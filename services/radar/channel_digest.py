"""
Генерация постов в стиле канала @digit_code (ЦифроКод: AI&IT).

Формат канала — ОДНА новость = ОДИН пост:
  <b>Заголовок</b>
  📌 суть
  ✨ деталь
  > цитата (опционально)
  👍 вывод
  🌐 | ЦифроКод: AI&IT 💻 | Источник 📢   (ссылка на первоисточник)

Модель (через claude-local-api) только ОТБИРАЕТ значимые новости и пишет текст
полями (JSON); разметку HTML и ссылку на источник собирает код — так заголовок
гарантированно жирный, а ссылка всегда настоящая (из базы radar.db, не выдумана).
"""
from __future__ import annotations

import html
import json
import re

from client import ask_claude

CHANNEL_NAME = "ЦифроКод: AI&IT"

PROMPT = """Ты редактор Telegram-канала «ЦифроКод: AI&IT» с техно-новостями.
Ниже — пронумерованные свежие материалы за сутки (лента и Telegram-каналы).
Отбери {n} САМЫХ значимых технологических / AI / IT новостей. Политику,
не-технические и рекламные материалы — пропускай.

Пост состоит РОВНО из трёх абзацев: суть → цитата → вывод.
Для каждой выбранной новости верни объект JSON с полями:
  "idx": число — номер материала из списка (важно: точный номер источника),
  "title": цепкий заголовок новости, без эмодзи, до 90 символов,
  "lead": 1–2 предложения — суть новости с ключевой деталью,
  "quote": ОБЯЗАТЕЛЬНО — короткая ёмкая цитата или ключевая мысль из новости
           (1 предложение, без кавычек, не оставляй пустой),
  "takeaway": одно короткое предложение — почему это важно / что меняется.

Пиши живо и по-русски, без воды и клише, СТРОГО по фактам материала — ничего
не выдумывай. Верни ТОЛЬКО JSON-массив из {n} объектов, без пояснений и без
markdown-ограждения.

МАТЕРИАЛЫ:
{items}"""


def _format_items(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items):
        tag = "TG" if it.get("kind") == "telegram" else "RSS"
        lines.append(f"[{i}] ({tag}) {it.get('title', '')} — {(it.get('text') or '')[:240]}")
    return "\n".join(lines)


def _extract_json_array(raw: str) -> list:
    """Вытащить JSON-массив из ответа модели (с код-ограждением или без)."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", s, flags=re.DOTALL)
        if not m:
            raise RuntimeError(f"модель не вернула JSON-массив: {raw[:200]}")
        data = json.loads(m.group(0))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise RuntimeError("ожидался JSON-массив новостей")
    return data


def _esc(s: str) -> str:
    return html.escape((s or "").strip())


def _normalize_url(url: str) -> str:
    """Привести ссылку к валидному URL или вернуть '' (битых href не делаем)."""
    u = (url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("@") and len(u) > 1:
        return f"https://t.me/{u[1:]}"
    return ""


def _footer(channel: str, url: str) -> str:
    """Футер: 🌐 | <ссылка на канал> 💻 | <ссылка на источник> 📢."""
    cl = _normalize_url(channel)
    name = f'<a href="{html.escape(cl, quote=True)}">{CHANNEL_NAME}</a>' if cl else CHANNEL_NAME
    s = f"🌐 | {name} 💻"
    link = _normalize_url(url)
    if link:
        s += f' | <a href="{html.escape(link, quote=True)}">Источник</a> 📢'
    return s


def _render(obj: dict, url: str, channel: str) -> str:
    # Ровно три абзаца: суть → цитата → вывод.
    parts = [f"<b>{_esc(obj.get('title'))}</b>", ""]
    parts.append(f"📌 {_esc(obj['lead'])}")
    parts += ["", f"<blockquote>{_esc(obj['quote'])}</blockquote>"]
    parts += ["", f"👍 {_esc(obj['takeaway'])}"]
    parts += ["", _footer(channel, url)]
    return "\n".join(parts)


def build_post_objects(items: list[dict], n: int = 5, model: str = "sonnet",
                       channel: str = "@digit_code") -> list[dict]:
    """Вернуть список постов как {"text": html, "url": источник}."""
    if not items:
        raise RuntimeError("нет свежих материалов для дайджеста (запусти ingest)")
    raw = ask_claude(PROMPT.format(n=n, items=_format_items(items)), model=model)
    data = _extract_json_array(raw)
    out = []
    for obj in data[:n]:
        # нужны все три абзаца: суть, цитата, вывод — иначе пост неполный
        if not isinstance(obj, dict) or not all(
            (obj.get(k) or "").strip() for k in ("title", "lead", "quote", "takeaway")
        ):
            continue
        try:
            idx = int(obj.get("idx", -1))
        except (TypeError, ValueError):
            idx = -1
        url = items[idx].get("url", "") if 0 <= idx < len(items) else ""
        out.append({"text": _render(obj, url, channel), "url": _normalize_url(url)})
    if not out:
        raise RuntimeError("модель не вернула ни одной валидной новости")
    return out


def build_posts(items: list[dict], n: int = 5, model: str = "sonnet",
                channel: str = "@digit_code") -> list[str]:
    """Вернуть список готовых HTML-постов (по одному на новость)."""
    return [o["text"] for o in build_post_objects(items, n=n, model=model, channel=channel)]


# ── Пост из произвольного источника (YouTube/статья/TG-пост) ──────────────────
SOURCE_PROMPT = """Ты редактор Telegram-канала «ЦифроКод: AI&IT» с техно-новостями.
Ниже — материал из {kind} (расшифровка/текст). Сделай из него ОДИН пост канала.

Пост состоит РОВНО из трёх абзацев: суть → цитата → вывод.
Верни объект JSON с полями:
  "title": цепкий заголовок, без эмодзи, до 90 символов,
  "lead": 1–2 предложения — главная суть материала с ключевой деталью,
  "quote": ОБЯЗАТЕЛЬНО — короткая ёмкая цитата или ключевая мысль из материала
           (1 предложение, без кавычек),
  "takeaway": одно короткое предложение — почему это важно / что меняется.

Пиши живо и по-русски, без воды и клише, СТРОГО по фактам материала — ничего
не выдумывай. Если материал не про технологии/AI/IT — всё равно сделай пост по
его сути. Верни ТОЛЬКО JSON-объект, без пояснений и markdown-ограждения.

МАТЕРИАЛ:
{content}"""

_KIND_RU = {"youtube": "видео на YouTube", "web": "статьи", "telegram": "поста",
            "text": "текста"}


def build_from_content(content: str, source_url: str = "", kind: str = "text",
                       model: str = "sonnet", channel: str = "@digit_code") -> dict:
    """Сгенерировать один пост из сырого контента. Вернуть {"text","url"}."""
    content = (content or "").strip()
    if len(content) < 40:
        raise RuntimeError("слишком мало текста в источнике для поста")
    prompt = SOURCE_PROMPT.format(kind=_KIND_RU.get(kind, "материала"),
                                  content=content[:12000])
    raw = ask_claude(prompt, model=model)
    data = _extract_json_array(raw)
    obj = data[0] if data else {}
    if not all((obj.get(k) or "").strip() for k in ("title", "lead", "quote", "takeaway")):
        raise RuntimeError("модель не вернула полный пост (нужны title/lead/quote/takeaway)")
    return {"text": _render(obj, source_url, channel), "url": _normalize_url(source_url)}


# Обратная совместимость: build() возвращает один склеенный текст (если где-то ещё зовут).
def build(items: list[dict], n: int = 5, model: str = "sonnet",
          channel: str = "@digit_code") -> str:
    return "\n\n".join(build_posts(items, n=n, model=model, channel=channel))
