#!/usr/bin/env python3
"""
tg-blog-editor — бот-редактор блога @digit_code (наследник Feed-Conveyor).

Human-in-the-loop публикация: ничего не уходит в канал, пока ты не нажал ✅.
Источник постов — radar.db (тот же движок, что делает дайджест), формат — стиль
канала (3 абзаца: суть → цитата → вывод).

Точки входа (argparse):
  bot.py run          — запустить бота (long-polling): команды и кнопки ревью.
  bot.py enqueue [--n] — СГЕНЕРИТЬ N черновиков и прислать их владельцу на ревью.
                         Вызывается кроном вместо авто-публикации (рекомендации).
  bot.py autopublish  — опубликовать черновики, провисевшие без решения дольше
                         AUTO_PUBLISH_HOURS (страховка, тоже вызывает крон).

Кнопки под каждым черновиком:
  ✅ Опубликовать · 🔄 Перегенерировать · ✏️ Править · ⏭ Пропустить
Публикация — самим ботом (бот должен быть админом канала).

Конфиг — .env: BOT_TOKEN, OWNER_ID, CHANNEL, AUTO_PUBLISH_HOURS.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
CHANNEL = os.environ.get("CHANNEL", "@digit_code").strip()
AUTO_PUBLISH_HOURS = float(os.environ.get("AUTO_PUBLISH_HOURS", "6"))
DRAFTS_PER_RUN = int(os.environ.get("DRAFTS_PER_RUN", "5"))

RADAR_DIR = BASE.parent / "radar"
# radar живёт во фреймворочном питоне (numpy/onnx/...), а сам бот — в своём venv.
# Поэтому radar всегда зовём явным интерпретатором, а не sys.executable.
RADAR_PYTHON = os.environ.get(
    "RADAR_PYTHON",
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
)
DB_PATH = BASE / "data" / "queue.db"
# Durable-память опубликованного: общий артефакт для бота, радара и SMM-агента.
PUBLISHED_LOG = BASE / "data" / "published.json"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Память опубликованных постов ──────────────────────────────────────────────
def _title_of(text: str) -> str:
    m = re.search(r"<b>(.*?)</b>", text or "", re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def load_published() -> list[dict]:
    if PUBLISHED_LOG.exists():
        try:
            return json.loads(PUBLISHED_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def record_published(url: str, text: str, by: str = "owner") -> None:
    """Запомнить опубликованный пост (url, заголовок, время, кем)."""
    PUBLISHED_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = load_published()
    log.append({"url": url or "", "title": _title_of(text),
                "ts": time.time(), "by": by})
    PUBLISHED_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2),
                             encoding="utf-8")


# ── Очередь черновиков (SQLite) ───────────────────────────────────────────────
def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending|published|skipped
            chat_id INTEGER,
            message_id INTEGER,
            created_at REAL,
            decided_at REAL,
            decided_by TEXT,                           -- owner|auto
            source_spec TEXT,                          -- JSON спека источника (для 🔄); NULL = из новостей
            variants TEXT,                             -- JSON [{text,url}] альтернативные генерации
            variant_idx INTEGER DEFAULT 0              -- текущий показанный вариант
        )"""
    )
    # миграция старой базы: добавить недостающие колонки
    cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)")]
    for col, ddl in (("source_spec", "ALTER TABLE drafts ADD COLUMN source_spec TEXT"),
                     ("variants", "ALTER TABLE drafts ADD COLUMN variants TEXT"),
                     ("variant_idx", "ALTER TABLE drafts ADD COLUMN variant_idx INTEGER DEFAULT 0")):
        if col not in cols:
            conn.execute(ddl)
    conn.commit()
    return conn


def add_draft(conn, text: str, url: str, source_spec: str | None = None) -> int:
    variants = json.dumps([{"text": text, "url": url}], ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO drafts(text,url,status,created_at,source_spec,variants,variant_idx) "
        "VALUES(?,?,'pending',?,?,?,0)",
        (text, url, time.time(), source_spec, variants),
    )
    conn.commit()
    return cur.lastrowid


# ── Варианты генерации (листание стрелками) ───────────────────────────────────
def get_variants(d: dict) -> list[dict]:
    raw = d.get("variants")
    if raw:
        try:
            v = json.loads(raw)
            if v:
                return v
        except json.JSONDecodeError:
            pass
    return [{"text": d["text"], "url": d.get("url", "")}]


def _save_variants(conn, did: int, variants: list[dict], idx: int) -> None:
    cur = variants[idx]
    conn.execute(
        "UPDATE drafts SET variants=?, variant_idx=?, text=?, url=? WHERE id=?",
        (json.dumps(variants, ensure_ascii=False), idx, cur["text"], cur.get("url", ""), did),
    )
    conn.commit()


def add_variant(conn, did: int, text: str, url: str) -> tuple[int, int]:
    """Добавить новую генерацию и сделать её текущей. Вернуть (idx, всего)."""
    vs = get_variants(get_draft(conn, did))
    vs.append({"text": text, "url": url})
    idx = len(vs) - 1
    _save_variants(conn, did, vs, idx)
    return idx, len(vs)


def shift_variant(conn, did: int, delta: int) -> tuple[int, int]:
    """Переключить текущий вариант на delta (стрелки). Вернуть (idx, всего)."""
    d = get_draft(conn, did)
    vs = get_variants(d)
    idx = (int(d.get("variant_idx") or 0) + delta) % len(vs)
    _save_variants(conn, did, vs, idx)
    return idx, len(vs)


def set_current_variant_text(conn, did: int, text: str, url: str) -> None:
    """Заменить текст текущего варианта (после ручной правки)."""
    d = get_draft(conn, did)
    vs = get_variants(d)
    idx = int(d.get("variant_idx") or 0)
    vs[idx] = {"text": text, "url": url}
    _save_variants(conn, did, vs, idx)


def get_draft(conn, did: int) -> dict | None:
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    if not row:
        return None
    cols = [c[0] for c in conn.execute("SELECT * FROM drafts LIMIT 0").description]
    return dict(zip(cols, row))


def set_card(conn, did: int, chat_id: int, message_id: int) -> None:
    conn.execute("UPDATE drafts SET chat_id=?, message_id=? WHERE id=?",
                 (chat_id, message_id, did))
    conn.commit()


def decide(conn, did: int, status: str, by: str) -> None:
    conn.execute("UPDATE drafts SET status=?, decided_at=?, decided_by=? WHERE id=?",
                 (status, time.time(), by, did))
    conn.commit()


def update_text(conn, did: int, text: str, url: str) -> None:
    conn.execute("UPDATE drafts SET text=?, url=? WHERE id=?", (text, url, did))
    conn.commit()


def pending_overdue(conn, hours: float) -> list[dict]:
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT id FROM drafts WHERE status='pending' AND created_at < ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()
    return [get_draft(conn, r[0]) for r in rows]


# ── Генерация постов через radar ──────────────────────────────────────────────
def used_urls() -> set[str]:
    """Ссылки историй, которые уже опубликованы или отклонены — их не рекомендуем.
    Источники памяти: очередь (published/skipped) + durable-лог published.json."""
    conn = db()
    rows = conn.execute(
        "SELECT DISTINCT url FROM drafts WHERE status IN ('published','skipped') AND url<>''"
    ).fetchall()
    used = {r[0] for r in rows}
    used |= {p["url"] for p in load_published() if p.get("url")}
    return used


def run_ingest() -> None:
    """Догнать парсер новостей (свежий пул историй)."""
    subprocess.run(
        [RADAR_PYTHON, str(RADAR_DIR / "radar.py"), "ingest", "--vectorize-pending"],
        cwd=str(RADAR_DIR), capture_output=True, text=True, timeout=900,
    )


def _draft_posts_call(n: int, exclude: set[str] | None) -> list[dict]:
    cmd = [RADAR_PYTHON, str(RADAR_DIR / "radar.py"), "draft-posts",
           "--n", str(n), "--channel", CHANNEL]
    exf = None
    if exclude:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(sorted(exclude), f, ensure_ascii=False)
        f.close()
        exf = f.name
        cmd += ["--exclude-file", exf]
    try:
        proc = subprocess.run(cmd, cwd=str(RADAR_DIR), capture_output=True,
                              text=True, timeout=300)
    finally:
        if exf:
            Path(exf).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"radar draft-posts: {(proc.stderr or proc.stdout)[:300]}")
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else "[]"
    return json.loads(line)


def generate_drafts(n: int, exclude: set[str] | None = None,
                    allow_ingest: bool = True) -> list[dict]:
    """N постов из ленты, исключая уже использованные истории. Если свежих мало —
    догоняет парсер новостей и пробует ещё раз (другие новости)."""
    if exclude is None:
        exclude = used_urls()
    # модель иногда отдаёт не-JSON (json.loads падает) — повторяем до 3 раз с backoff
    posts = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            posts = _draft_posts_call(n, exclude)
            break
        except (json.JSONDecodeError, RuntimeError) as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    if posts is None:
        raise RuntimeError(f"radar draft-posts не дал JSON после ретраев: {last_err}")
    if len(posts) < n and allow_ingest:
        run_ingest()  # повторы → подтянуть свежее и взять другое
        try:
            posts = _draft_posts_call(n, exclude)
        except (json.JSONDecodeError, RuntimeError):
            pass  # уже есть частичный результат — отдаём что есть
    return posts


# ── Источники от владельца (TG-пост / статья / YouTube) → черновик ────────────
URL_RE = re.compile(r"https?://\S+")
YOUTUBE_RE = re.compile(r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)", re.I)


def first_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(").,") if m else ""


def classify_source(text: str, is_forward: bool) -> tuple[str, dict]:
    """Определить тип источника и собрать spec для process_source.py."""
    url = first_url(text)
    if url and YOUTUBE_RE.search(url):
        return "youtube", {"kind": "youtube", "url": url}
    if is_forward and not url:
        return "telegram", {"kind": "telegram", "text": text}
    if url:
        return "web", {"kind": "web", "url": url}
    if text.strip():
        return "text", {"kind": "text", "text": text}
    return "", {}


def process_source(spec: dict) -> dict:
    """Запустить воркер извлечения+генерации. Вернуть {"text","url","kind","chars"}."""
    proc = subprocess.run(
        [RADAR_PYTHON, str(BASE / "process_source.py")],
        input=json.dumps(spec, ensure_ascii=False),
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "process_source failed").strip()[:300])
    line = (proc.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


# ── Telegram Bot API (stdlib, для CLI-режимов enqueue/autopublish) ─────────────
# Под активным VPN (Happ Plus и пр.) api.telegram.org ПЕРЕМЕЖАЮЩЕ отдаёт TLS-цепочку
# с самоподписанным корнем (TLS-инспекция), которому не доверяет ни certifi, ни даже
# системный keychain macOS. Чтобы дайджест всё равно доставлялся, контексты пробуются
# по убыванию строгости:
#   1) certifi (дефолт Python)         — обычная строгая проверка;
#   2) системный keychain (truststore) — если корень VPN установлен в систему;
#   3) БЕЗ верификации — последний резерв ТОЛЬКО для api.telegram.org под своим VPN.
# Нормально работают (1)/(2); (3) включается лишь в окно MITM и пишет предупреждение.
def _ssl_contexts() -> list[tuple[str, ssl.SSLContext]]:
    ctxs = [("certifi", ssl.create_default_context())]
    try:
        import truststore
        ctxs.append(("keychain", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    except Exception:
        pass
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    ctxs.append(("unverified", unverified))
    return ctxs


def api(method: str, **params) -> dict:
    if "reply_markup" in params and not isinstance(params["reply_markup"], str):
        params["reply_markup"] = json.dumps(params["reply_markup"], ensure_ascii=False)
    data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    contexts = _ssl_contexts()
    last_err: Exception | None = None
    for attempt in range(3):                        # 3 попытки с backoff
        for label, ctx in contexts:                 # certifi → keychain → unverified
            req = urllib.request.Request(f"{API}/{method}", data=data,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                    res = json.loads(r.read().decode("utf-8"))
                if not res.get("ok"):
                    raise RuntimeError(f"{method}: {res.get('description')}")
                if label == "unverified":
                    print(f"[api] ⚠ TLS не проверен (MITM VPN), отправлено без верификации: {method}",
                          file=sys.stderr, flush=True)
                return res["result"]
            except ssl.SSLError as e:
                last_err = e                        # контекст не доверяет цепочке — следующий
                continue
            except urllib.error.URLError as e:
                last_err = e
                # ВАЖНО: при MITM-инспекции VPN urlopen оборачивает SSL-ошибку
                # в URLError(reason=ssl.SSLError). Это НЕ сетевой блип — это
                # недоверие к цепочке, поэтому пробуем следующий контекст
                # (keychain → unverified), а не уходим на ретрай того же certifi.
                if isinstance(e.reason, ssl.SSLError):
                    continue
                break                               # настоящий сетевой блип — на ретрай
            except (TimeoutError, OSError) as e:
                # read timeout / обрыв соединения при чтении ответа: urlopen НЕ
                # заворачивает их в URLError (приходят из getresponse()), поэтому
                # ловим отдельно — иначе таймаут чтения роняет весь джоб (digest).
                last_err = e
                break                               # на ретрай с backoff
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{method}: сеть/TLS недоступны после ретраев: {last_err}")


def review_keyboard(did: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Опубликовать", "callback_data": f"pub:{did}"}],
        [{"text": "🔄 Ещё вариант", "callback_data": f"regen:{did}"},
         {"text": "⏭ Пропустить", "callback_data": f"skip:{did}"}],
        [{"text": "✏️ Править", "callback_data": f"edit:{did}"}],
    ]}


def card_text(idx: int, total: int, text: str) -> str:
    return f"📝 <b>Черновик {idx}/{total}</b> — на ревью\n\n{text}"


# ── CLI: enqueue (рекомендации в бота) ────────────────────────────────────────
def cmd_enqueue(n: int) -> None:
    if not BOT_TOKEN or not OWNER_ID:
        raise SystemExit("нет BOT_TOKEN/OWNER_ID в .env")
    drafts = generate_drafts(n)
    if not drafts:
        api("sendMessage", chat_id=OWNER_ID,
            text="📭 Свежих материалов для черновиков нет.")
        print("enqueue: 0 черновиков")
        return
    conn = db()
    api("sendMessage", chat_id=OWNER_ID,
        text=f"🗞 <b>Рекомендации к публикации</b> — {len(drafts)} шт. "
             f"Просмотри и реши по каждой. Без решения через "
             f"{int(AUTO_PUBLISH_HOURS)} ч — опубликую сама.",
        parse_mode="HTML")
    for i, d in enumerate(drafts, 1):
        did = add_draft(conn, d["text"], d.get("url", ""))
        msg = api("sendMessage", chat_id=OWNER_ID,
                  text=card_text(i, len(drafts), d["text"]),
                  parse_mode="HTML", disable_web_page_preview=False,
                  reply_markup=review_keyboard(did))
        set_card(conn, did, OWNER_ID, msg["message_id"])
    print(f"enqueue: отправлено {len(drafts)} черновиков владельцу")


# ── CLI: autopublish (страховка по таймауту) ──────────────────────────────────
def cmd_autopublish() -> None:
    if not BOT_TOKEN:
        raise SystemExit("нет BOT_TOKEN в .env")
    conn = db()
    overdue = pending_overdue(conn, AUTO_PUBLISH_HOURS)
    if not overdue:
        print("autopublish: просроченных черновиков нет")
        return
    done = 0
    for d in overdue:
        try:
            api("sendMessage", chat_id=CHANNEL, text=d["text"],
                parse_mode="HTML", disable_web_page_preview=False)
            decide(conn, d["id"], "published", "auto")
            record_published(d.get("url", ""), d["text"], "auto")
            done += 1
            if d.get("chat_id") and d.get("message_id"):
                api("editMessageReplyMarkup", chat_id=d["chat_id"],
                    message_id=d["message_id"], reply_markup={"inline_keyboard": []})
                api("sendMessage", chat_id=d["chat_id"],
                    text=f"⏰ Черновик #{d['id']} авто-опубликован "
                         f"(прошло > {int(AUTO_PUBLISH_HOURS)} ч без решения).")
        except Exception as e:  # noqa: BLE001
            print(f"autopublish: #{d['id']} ошибка: {e}", file=sys.stderr)
    print(f"autopublish: опубликовано {done}/{len(overdue)}")


# ── CLI: published (память опубликованного — для SMM-агента и человека) ───────
def cmd_published(limit: int) -> None:
    import datetime as _dt
    log = load_published()
    if not log:
        print("опубликованных постов пока нет")
        return
    for p in log[-limit:]:
        when = _dt.datetime.fromtimestamp(p["ts"]).strftime("%Y-%m-%d %H:%M")
        print(f"{when} [{p.get('by', '?')}] {p.get('title', '')[:64]} — {p.get('url', '')}")
    print(f"— всего опубликовано: {len(log)}")


# ── RUN: long-polling бот (aiogram) ───────────────────────────────────────────
def cmd_run() -> None:
    if not BOT_TOKEN or not OWNER_ID:
        raise SystemExit("нет BOT_TOKEN/OWNER_ID в .env")
    asyncio.run(_run_bot())


async def _run_bot() -> None:
    import logging
    from aiogram import Bot, Dispatcher, F
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.filters import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                               InlineKeyboardMarkup, Message)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    def owner_only(uid: int) -> bool:
        return uid == OWNER_ID

    def kb(did: int, vs: list | None = None, idx: int = 0) -> InlineKeyboardMarkup:
        rows = []
        if vs and len(vs) > 1:  # стрелки листают варианты
            rows.append([
                InlineKeyboardButton(text="◀️", callback_data=f"nav:{did}:-1"),
                InlineKeyboardButton(text=f"{idx + 1}/{len(vs)}", callback_data="noop"),
                InlineKeyboardButton(text="▶️", callback_data=f"nav:{did}:1"),
            ])
        rows.append([InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{did}")])
        rows.append([
            InlineKeyboardButton(text="🔄 Ещё вариант", callback_data=f"regen:{did}"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip:{did}"),
        ])
        rows.append([InlineKeyboardButton(text="✏️ Править", callback_data=f"edit:{did}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def render_card(did: int) -> tuple[str, InlineKeyboardMarkup]:
        """Текст карточки + клавиатура для текущего варианта черновика."""
        conn = db()
        d = get_draft(conn, did)
        vs = get_variants(d)
        idx = int(d.get("variant_idx") or 0)
        src = "из источника" if d.get("source_spec") else "из ленты"
        head = f"📝 <b>Черновик #{did}</b> ({src})"
        if len(vs) > 1:
            head += f" · вариант {idx + 1}/{len(vs)}"
        head += " — на ревью"
        return f"{head}\n\n{vs[idx]['text']}", kb(did, vs, idx)

    class EditFlow(StatesGroup):
        waiting = State()

    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        if not owner_only(m.from_user.id):
            return
        await m.answer(
            "👋 Я редактор блога <b>@digit_code</b>.\n\n"
            "<b>Кидай мне источники прямо в чат</b> — соберу из них пост:\n"
            "• 🎬 ссылку на YouTube (разберу субтитры)\n"
            "• 📰 ссылку на статью (вытащу текст)\n"
            "• 💬 пересланный TG-пост\n"
            "• 📝 просто текст/заметку\n"
            "Обрабатываю в фоне и параллельно — можно кидать несколько подряд.\n\n"
            "<b>Команды:</b>\n"
            f"• /draft [N] — взять N свежих новостей из ленты (по умолч. {DRAFTS_PER_RUN})\n"
            "• /pending — показать неразобранные черновики\n\n"
            "Под черновиком: ✅ опубликовать · 🔄 ещё вариант · ✏️ править · ⏭ "
            "пропустить. Когда вариантов несколько — листай их стрелками ◀️ ▶️ и "
            "публикуй тот, что нравится.\n"
            "Каждый день в 10:00 пришлю рекомендации сама (без повторов уже "
            "опубликованного).")

    @dp.message(Command("draft"))
    async def draft(m: Message) -> None:
        if not owner_only(m.from_user.id):
            return
        parts = (m.text or "").split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else DRAFTS_PER_RUN
        await m.answer(f"⏳ Генерирую {n} черновик(ов)…")
        try:
            drafts = await asyncio.to_thread(generate_drafts, n)
        except Exception as e:  # noqa: BLE001
            await m.answer(f"❌ Ошибка генерации: {e}")
            return
        if not drafts:
            await m.answer("📭 Свежих материалов нет.")
            return
        conn = db()
        for d in drafts:
            did = add_draft(conn, d["text"], d.get("url", ""))
            text, markup = render_card(did)
            msg = await m.answer(text, reply_markup=markup)
            set_card(conn, did, msg.chat.id, msg.message_id)

    @dp.message(Command("pending"))
    async def pending(m: Message) -> None:
        if not owner_only(m.from_user.id):
            return
        conn = db()
        rows = conn.execute(
            "SELECT id FROM drafts WHERE status='pending' ORDER BY created_at").fetchall()
        if not rows:
            await m.answer("✅ Неразобранных черновиков нет.")
            return
        for (did,) in rows:
            text, markup = render_card(did)
            msg = await m.answer(text, reply_markup=markup)
            set_card(conn, did, msg.chat.id, msg.message_id)

    @dp.callback_query(F.data.startswith("pub:"))
    async def cb_pub(c: CallbackQuery) -> None:
        if not owner_only(c.from_user.id):
            return
        did = int(c.data.split(":")[1])
        conn = db()
        d = get_draft(conn, did)
        if not d or d["status"] != "pending":
            await c.answer("Уже обработан", show_alert=False)
            return
        try:
            await bot.send_message(CHANNEL, d["text"], disable_web_page_preview=False)
            decide(conn, did, "published", "owner")
            record_published(d.get("url", ""), d["text"], "owner")
            await c.message.edit_text(d["text"] + f"\n\n✅ <b>Опубликовано</b> в {CHANNEL}")
            await c.answer("Опубликовано ✅")
        except Exception as e:  # noqa: BLE001
            await c.answer(f"Ошибка: {e}", show_alert=True)

    @dp.callback_query(F.data.startswith("skip:"))
    async def cb_skip(c: CallbackQuery) -> None:
        if not owner_only(c.from_user.id):
            return
        did = int(c.data.split(":")[1])
        conn = db()
        d = get_draft(conn, did)
        if not d or d["status"] != "pending":
            await c.answer("Уже обработан")
            return
        decide(conn, did, "skipped", "owner")
        await c.message.edit_text(d["text"] + "\n\n⏭ <b>Пропущено</b>")
        await c.answer("Пропущено")

    @dp.callback_query(F.data.startswith("regen:"))
    async def cb_regen(c: CallbackQuery) -> None:
        if not owner_only(c.from_user.id):
            return
        did = int(c.data.split(":")[1])
        conn = db()
        d = get_draft(conn, did)
        if not d or d["status"] != "pending":
            try:
                await c.answer("Уже обработан")
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            await c.answer("🔄 Генерирую ещё вариант…")
        except Exception:  # noqa: BLE001 — старый callback после рестарта
            pass
        try:
            spec = d.get("source_spec")
            if spec:  # из источника → пере-обработать тот же источник (другая формулировка)
                res = await asyncio.to_thread(process_source, json.loads(spec))
            else:     # из ленты → ДРУГАЯ новость (исключаем уже показанные и использованные)
                exclude = used_urls() | {v.get("url", "") for v in get_variants(d) if v.get("url")}
                got = await asyncio.to_thread(generate_drafts, 1, exclude)
                if not got:
                    raise RuntimeError("нет других свежих новостей (попробуй позже)")
                res = got[0]
        except Exception as e:  # noqa: BLE001
            await c.message.answer(f"❌ Не вышло сгенерировать вариант: {e}")
            return
        add_variant(conn, did, res["text"], res.get("url", ""))
        text, markup = render_card(did)
        await c.message.edit_text(text, reply_markup=markup)

    @dp.callback_query(F.data.startswith("nav:"))
    async def cb_nav(c: CallbackQuery) -> None:
        if not owner_only(c.from_user.id):
            return
        _, sid, sdelta = c.data.split(":")
        did = int(sid)
        conn = db()
        d = get_draft(conn, did)
        if not d or d["status"] != "pending":
            try:
                await c.answer("Уже обработан")
            except Exception:  # noqa: BLE001
                pass
            return
        shift_variant(conn, did, int(sdelta))
        text, markup = render_card(did)
        try:
            await c.message.edit_text(text, reply_markup=markup)
        except Exception:  # noqa: BLE001 — то же сообщение/слишком старое
            pass
        try:
            await c.answer()
        except Exception:  # noqa: BLE001
            pass

    @dp.callback_query(F.data == "noop")
    async def cb_noop(c: CallbackQuery) -> None:
        try:
            await c.answer()
        except Exception:  # noqa: BLE001
            pass

    @dp.callback_query(F.data.startswith("edit:"))
    async def cb_edit(c: CallbackQuery, state: FSMContext) -> None:
        if not owner_only(c.from_user.id):
            return
        did = int(c.data.split(":")[1])
        await state.set_state(EditFlow.waiting)
        await state.update_data(did=did)
        await c.answer()
        await c.message.answer(
            f"✏️ Пришли новый текст для черновика #{did} (HTML-разметка "
            "разрешена: &lt;b&gt;, &lt;blockquote&gt;, &lt;a&gt;).")

    @dp.message(EditFlow.waiting)
    async def edit_apply(m: Message, state: FSMContext) -> None:
        if not owner_only(m.from_user.id):
            return
        data = await state.get_data()
        did = int(data["did"])
        await state.clear()
        conn = db()
        d = get_draft(conn, did)
        if not d or d["status"] != "pending":
            await m.answer("Черновик уже обработан.")
            return
        set_current_variant_text(conn, did, m.html_text, d.get("url", ""))
        text, markup = render_card(did)
        msg = await m.answer(text, reply_markup=markup)
        set_card(conn, did, msg.chat.id, msg.message_id)

    async def _handle_source(m: Message, kind: str, spec: dict) -> None:
        labels = {"youtube": "🎬 YouTube", "web": "📰 статью",
                  "telegram": "💬 TG-пост", "text": "📝 текст"}
        note = await m.answer(
            f"🔄 Принял {labels.get(kind, kind)} — обрабатываю в фоне "
            "(извлекаю контент → пишу пост)…")
        try:
            res = await asyncio.to_thread(process_source, spec)
        except Exception as e:  # noqa: BLE001
            await note.edit_text(f"❌ Не вышло обработать источник: {e}")
            return
        conn = db()
        did = add_draft(conn, res["text"], res.get("url", ""),
                        source_spec=json.dumps(spec, ensure_ascii=False))
        try:
            await note.delete()
        except Exception:  # noqa: BLE001
            pass
        text, markup = render_card(did)
        msg = await m.answer(text, reply_markup=markup)
        set_card(conn, did, msg.chat.id, msg.message_id)

    @dp.message(F.from_user.id == OWNER_ID)
    async def incoming_source(m: Message) -> None:
        body = (m.text or m.caption or "").strip()
        is_forward = bool(
            getattr(m, "forward_origin", None)
            or getattr(m, "forward_from", None)
            or getattr(m, "forward_from_chat", None)
        )
        kind, spec = classify_source(body, is_forward)
        if not kind:
            await m.answer(
                "Пришли мне источник для поста: пересланный TG-пост, ссылку на "
                "статью или ссылку на YouTube — соберу черновик. "
                "Или /draft — взять свежие новости.")
            return
        spec["model"] = "sonnet"
        # параллельно: каждый источник — своя фоновая задача
        asyncio.create_task(_handle_source(m, kind, spec))

    db()  # инициализировать схему
    print(f"[blog-editor] бот запущен · owner={OWNER_ID} · канал={CHANNEL}", flush=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main() -> None:
    p = argparse.ArgumentParser(prog="blog-editor")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="long-polling бот (команды + кнопки ревью)")
    pe = sub.add_parser("enqueue", help="прислать владельцу N черновиков на ревью")
    pe.add_argument("--n", type=int, default=DRAFTS_PER_RUN)
    sub.add_parser("autopublish", help="опубликовать просроченные черновики")
    pp = sub.add_parser("published", help="показать память опубликованных постов")
    pp.add_argument("--limit", type=int, default=30)
    args = p.parse_args()
    if args.cmd == "run":
        cmd_run()
    elif args.cmd == "enqueue":
        cmd_enqueue(args.n)
    elif args.cmd == "autopublish":
        cmd_autopublish()
    elif args.cmd == "published":
        cmd_published(args.limit)


if __name__ == "__main__":
    main()
