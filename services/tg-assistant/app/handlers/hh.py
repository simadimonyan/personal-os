"""Управление автооткликом hh.ru из Telegram (команда /hh).

Бот здесь — пульт, а не исполнитель: отклики шлёт крон, бот показывает
статистику, включает и выключает прогон, крутит порог отбора и настраивает
стиль сопроводительных письмом «пиши короче». Тяжёлая работа уходит в
subprocess (hh_agent), event loop не блокируется.

Карточек «подтверди каждый отклик» намеренно нет: при 10 откликах в день это
10 подтверждений, а смысл автоотклика — чтобы их не было. Контроль устроен
иначе — через порог отбора и правки стиля письма.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.integrations import hh_agent, hh_stats
from app.keyboards import menu

log = logging.getLogger("assistant.hh")
router = Router(name="hh")


class HHStates(StatesGroup):
    letter_note = State()   # ждём правку стиля письма
    min_score = State()     # ждём новый порог отбора


# ────────────────────────────── рендер ──────────────────────────────

def _status_text(st: dict) -> str:
    head = "⏸ Автоотклик на паузе" if st["paused"] else "▶️ Автоотклик работает"
    mode = f"порог {st['min_score']}/10" if st["llm_enabled"] else "без LLM-отбора"

    lines = [
        f"{head} · «{st['query']}» · {mode}",
        "",
        f"Сегодня отправлено: {st['today']} из {st['target']}",
        f"За неделю: {st['week']} откликов, отсеяно моделью {st['rejected_week']}",
    ]
    if st["avg_score"]:
        lines.append(f"Средняя оценка вакансий за неделю: {st['avg_score']}/10")
    lines.append(f"Всего в истории: {st['total']}")
    lines.append(f"Последний прогон: {st['last_run']} ({st['last_status']})")

    if st["invites"]:
        lines += ["", "🎯 Приглашения:"]
        for inv in st["invites"]:
            lines.append(f"  • {inv['title']} — {inv['company']}")
    if st["pending"]:
        lines.append(f"\nОжидают ответа: {st['pending']}")
    return "\n".join(lines)


def _status_kb(paused: bool, running: bool = False) -> InlineKeyboardMarkup:
    toggle = ("▶️ Возобновить", "hh:resume") if paused else ("⏸ Пауза", "hh:pause")
    rows: list[list[InlineKeyboardButton]] = []
    # Пока прогон идёт, первой строкой — вход в живой экран, а кнопки запуска
    # прячем: второй параллельный прогон подрался бы за тот же браузер.
    if running:
        rows.append([InlineKeyboardButton(text="📡 Идёт прогон — смотреть",
                                          callback_data="hh:live")])
    else:
        rows.append([InlineKeyboardButton(text="🚀 Прогнать сейчас", callback_data="hh:run"),
                     InlineKeyboardButton(text="🔍 Без откликов", callback_data="hh:dry")])
    rows += [
        [InlineKeyboardButton(text="⏭ Пропущенные", callback_data="hh:feed"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="hh:stats:week")],
        [InlineKeyboardButton(text="✍️ Письмо", callback_data="hh:letter")],
        [InlineKeyboardButton(text="🎛 Фильтры", callback_data="hh:filters"),
         InlineKeyboardButton(text="📄 Резюме", callback_data="hh:resumes")],
        [InlineKeyboardButton(text="🎚 Порог", callback_data="hh:score")],
        [InlineKeyboardButton(text="⏱ Режим", callback_data="hh:tuning"),
         InlineKeyboardButton(text=toggle[0], callback_data=toggle[1])],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="hh:refresh"),
         InlineKeyboardButton(text="← Меню", callback_data="hh:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_status(target: Message, edit: bool = False) -> None:
    try:
        st = hh_agent.status()
    except hh_agent.HHUnavailable as e:
        await target.answer(f"hh: {e}")
        return
    prog = hh_agent.run_progress()
    text = _status_text(st)
    if prog.get("running"):
        text = f"🔴 Сейчас идёт прогон ({prog.get('phase', '—')})\n\n" + text
    kb = _status_kb(st["paused"], running=prog.get("running", False))
    if edit:
        # если текст не изменился, Telegram отвечает ошибкой — она нам не интересна
        try:
            await target.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass
    else:
        await target.answer(text, reply_markup=kb)


# ────────────────────────────── команды ──────────────────────────────

@router.message(Command("hh"))
async def cmd_hh(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_status(message)


@router.callback_query(menu.MenuCB.filter(F.action == "hh"))
async def cb_from_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await _show_status(cb.message)


@router.callback_query(F.data == "hh:menu")
async def cb_to_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer("Меню. Можно одним тапом.", reply_markup=menu.main_menu_kb())


@router.callback_query(F.data == "hh:refresh")
async def cb_refresh(cb: CallbackQuery) -> None:
    await cb.answer("обновляю")
    await _show_status(cb.message, edit=True)


@router.callback_query(F.data.in_({"hh:pause", "hh:resume"}))
async def cb_toggle(cb: CallbackQuery) -> None:
    paused = cb.data == "hh:pause"
    hh_agent.set_paused(paused)
    await cb.answer("поставил на паузу" if paused else "включил")
    await _show_status(cb.message, edit=True)


@router.callback_query(F.data.in_({"hh:run", "hh:dry"}))
async def cb_run(cb: CallbackQuery) -> None:
    dry = cb.data == "hh:dry"
    if hh_agent.run_progress().get("running"):
        await cb.answer("прогон уже идёт", show_alert=True)
        return
    await cb.answer("запускаю")

    # Запускаем и отпускаем — бот не ждёт и не убивает процесс по таймауту.
    # За ходом следит живой экран, он же пришлёт итог.
    await hh_agent.start_run(dry=dry)
    live = await cb.message.answer("⏳ запускаю прогон…", reply_markup=_live_kb(True))
    asyncio.create_task(_follow(live, report=True))


# ──────────────────────── живой ход прогона ────────────────────────

# Автообновление: сообщение редактируется, пока прогон идёт. Ограничено по
# времени и частоте — у Telegram лимит на правки, а прогон может длиться 20 мин.
LIVE_INTERVAL = 6
LIVE_MAX_SEC = 2700   # прогон реально идёт до ~20 мин; с запасом


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m} мин {s} с" if m else f"{s} с"


def _live_text(p: dict) -> str:
    if p.get("empty"):
        return "Прогонов ещё не было — история пуста."

    if p.get("running"):
        head = f"🔴 Прогон идёт · режим: {p.get('mode', '—')}"
    elif p.get("stale"):
        head = "⚠️ Прогон оборвался (процесс исчез, итог не записан)"
    else:
        head = f"✅ Прогон завершён · режим: {p.get('mode', '—')}"

    started = p.get("started_at", 0)
    ref = time.time() if p.get("running") else p.get("updated_at", started)
    lines = [head, f"Фаза: {p.get('phase', '—')} · прошло {_fmt_elapsed(ref - started)}"]

    if p.get("current"):
        lines.append(f"Сейчас: {p['current']}")

    counters = p.get("counters") or {}
    if counters:
        lines.append("")
        lines += [f"  {k.replace('_', ' ')}: {v}" for k, v in counters.items()]

    log = p.get("log") or []
    if log:
        lines += ["", "Последние события:"] + [f"  {l}" for l in log]

    if p.get("result"):
        lines += ["", f"Итог: {p['result']}"]
    return "\n".join(lines)


def _live_kb(running: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="hh:live")]]
    if not running:
        rows = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="hh:live"),
                 InlineKeyboardButton(text="📊 Статистика", callback_data="hh:stats:week")]]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "hh:live")
async def cb_live(cb: CallbackQuery) -> None:
    await cb.answer()
    p = hh_agent.run_progress()
    msg = await cb.message.answer(_live_text(p), reply_markup=_live_kb(p.get("running", False)))
    if p.get("running"):
        asyncio.create_task(_follow(msg))


async def _follow(msg: Message, report: bool = False) -> None:
    """Пока прогон идёт — обновлять сообщение на месте (снапшот сам себя догоняет).

    report=True — по завершении прислать отдельным сообщением итог. Именно его
    ждёшь, когда нажал «Прогнать сейчас»: живой экран уезжает вверх по чату,
    а итог должен остаться заметным.
    """
    # Ждём столько, сколько идёт прогон: он может длиться 20+ минут, и обрывать
    # слежение раньше — значит потерять итог (ровно это и случалось).
    deadline = time.time() + LIVE_MAX_SEC
    last = ""
    while time.time() < deadline:
        await asyncio.sleep(LIVE_INTERVAL)
        p = hh_agent.run_progress()
        text = _live_text(p)
        if text != last:   # Telegram ругается на правку без изменений
            try:
                await msg.edit_text(text, reply_markup=_live_kb(p.get("running", False)))
                last = text
            except TelegramBadRequest:
                pass
            except Exception:  # noqa: BLE001 — фоновая задача не должна ронять бота
                log.exception("live-обновление прогона упало")
                return
        if not p.get("running"):
            if report:
                await _send_report(msg, p)
            return
    if report:   # вышли по своему потолку, но прогон ещё жив — так и скажем
        await msg.answer("⏳ прогон идёт дольше обычного. Итог придёт "
                         "уведомлением, следить можно кнопкой «📡 Идёт прогон».")


async def _send_report(msg: Message, p: dict) -> None:
    """Итог прогона отдельным сообщением + обновлённая карточка."""
    counters = p.get("counters") or {}
    if p.get("stale"):
        head = ("⚠️ Прогон оборвался — процесс исчез, не дописав итог.\n"
                "Отправленное до обрыва сохранено в истории.")
    else:
        head = f"🏁 Прогон завершён\n\n{p.get('result') or 'без итога'}"
    lines = [head]
    if counters:
        lines += ["", "Что произошло:"]
        lines += [f"  {k.replace('_', ' ')}: {v}" for k, v in counters.items()]
    if p.get("log"):
        lines += ["", "Отклики:"] + [f"  {l}" for l in p["log"]]
    try:
        await msg.answer("\n".join(lines))
        await _show_status(msg)
    except Exception:  # noqa: BLE001
        log.exception("не смог отправить итог прогона")


# ────────────────── частота прогонов и уведомления ──────────────────

_NOTIFY_LABELS = {"invites": "Приглашения", "rejects": "Отказы", "runs": "Итоги прогонов"}


def _tuning_text() -> str:
    s = hh_agent.schedule_state()
    n = hh_agent.notify_settings()
    times = ", ".join(s["times"]) or "—"
    lines = [
        "⏱ Режим работы автоотклика", "",
        f"Прогонов в день: {s['runs_per_day']} ({times})",
        f"Откликов за прогон: до {s['target']}",
        f"Потолок в день: до {s['runs_per_day'] * s['target']} откликов",
        "", "Уведомления:",
    ]
    lines += [f"  {'✅' if n[k] else '◻️'} {label}" for k, label in _NOTIFY_LABELS.items()]
    lines += ["", "Приглашения и отказы бот берёт из проверки ответов "
                  "(она ходит на hh дважды в день)."]
    return "\n".join(lines)


def _tuning_kb() -> InlineKeyboardMarkup:
    s = hh_agent.schedule_state()
    n = hh_agent.notify_settings()
    runs = [InlineKeyboardButton(
        text=("• " if s["runs_per_day"] == i else "") + f"{i}/день",
        callback_data=f"hh:runs:{i}") for i in (1, 2, 3, 4)]
    targets = [InlineKeyboardButton(
        text=("• " if s["target"] == t else "") + str(t),
        callback_data=f"hh:target:{t}") for t in (5, 10, 15, 20)]
    notif = [InlineKeyboardButton(
        text=("✅ " if n[k] else "◻️ ") + label,
        callback_data=f"hh:notify:{k}") for k, label in _NOTIFY_LABELS.items()]
    return InlineKeyboardMarkup(inline_keyboard=[
        runs,
        targets,
        notif[:2], notif[2:],
        [InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")],
    ])


async def _show_tuning(target: Message, edit: bool = False) -> None:
    text, kb = _tuning_text(), _tuning_kb()
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass
        return
    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "hh:tuning")
async def cb_tuning(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_tuning(cb.message)


@router.callback_query(F.data.startswith("hh:runs:"))
async def cb_runs(cb: CallbackQuery) -> None:
    n = int(cb.data.rsplit(":", 1)[-1])
    try:
        state = hh_agent.set_runs_per_day(n)
    except hh_agent.HHUnavailable as e:
        await cb.answer(str(e), show_alert=True)
        return
    await cb.answer(f"{state['runs_per_day']} прогона(ов) в день: {', '.join(state['times'])}")
    await _show_tuning(cb.message, edit=True)


@router.callback_query(F.data.startswith("hh:target:"))
async def cb_target(cb: CallbackQuery) -> None:
    value = hh_agent.set_target(int(cb.data.rsplit(":", 1)[-1]))
    await cb.answer(f"до {value} откликов за прогон")
    await _show_tuning(cb.message, edit=True)


@router.callback_query(F.data.startswith("hh:notify:"))
async def cb_notify(cb: CallbackQuery) -> None:
    key = cb.data.rsplit(":", 1)[-1]
    if key not in _NOTIFY_LABELS:
        await cb.answer("неизвестное уведомление", show_alert=True)
        return
    state = hh_agent.toggle_notify(key)
    await cb.answer(f"{_NOTIFY_LABELS[key]}: {'включены' if state[key] else 'выключены'}")
    await _show_tuning(cb.message, edit=True)


# ──────────────────── лента пропущенных вакансий ────────────────────

# Лента живёт в одном сообщении: листание и действия правят его на месте,
# чтобы чат не зарастал карточками. Позиция едет в callback_data — состояние
# хранить негде и не нужно, а список пересобирается на каждый тап (он короткий).

_OUTCOME_WHY = {
    "rejected_by_llm": "отсеяна моделью",
    "skip": "отклик не удался",
    "unconfirmed": "hh не подтвердил отклик",
}


def _feed_text(items: list[dict], idx: int) -> str:
    if not items:
        return ("⏭ Пропущенных вакансий нет.\n\n"
                "Сюда попадают те, что бот посмотрел, но не отправил отклик: "
                "отсеянные моделью по оценке и те, где отклик сорвался.")
    v = items[idx]
    score = f"{v['score']}/10" if v.get("score") is not None else "без оценки"
    lines = [
        f"⏭ Пропущенные · {idx + 1} из {len(items)}",
        "",
        f"{v.get('title') or 'без названия'}",
        f"{v.get('company') or ''}".strip(),
    ]
    if v.get("salary"):
        lines.append(v["salary"])
    lines += ["", f"Оценка: {score} · {_OUTCOME_WHY.get(v.get('outcome'), v.get('outcome'))}"]
    if v.get("reason"):
        lines.append(f"Почему: {v['reason']}")
    if v.get("url"):
        lines += ["", v["url"]]
    return "\n".join(lines)


def _feed_kb(items: list[dict], idx: int) -> InlineKeyboardMarkup:
    if not items:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")]])
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"hh:feed:{idx - 1}"))
    nav.append(InlineKeyboardButton(text=f"{idx + 1}/{len(items)}", callback_data="hh:feed:noop"))
    if idx < len(items) - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"hh:feed:{idx + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"hh:apply:{idx}"),
         InlineKeyboardButton(text="🗑 Не интересно", callback_data=f"hh:drop:{idx}")],
        [InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")],
    ])


async def _show_feed(target: Message, idx: int = 0, edit: bool = True) -> None:
    try:
        items = hh_agent.skipped()
    except hh_agent.HHUnavailable as e:
        await target.answer(f"hh: {e}")
        return
    idx = max(0, min(idx, len(items) - 1)) if items else 0
    text, kb = _feed_text(items, idx), _feed_kb(items, idx)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            return
        except TelegramBadRequest:
            return
    await target.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "hh:feed")
async def cb_feed_open(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_feed(cb.message, 0, edit=False)


@router.callback_query(F.data.startswith("hh:feed:"))
async def cb_feed_nav(cb: CallbackQuery) -> None:
    raw = cb.data.rsplit(":", 1)[-1]
    if raw == "noop":
        await cb.answer()
        return
    await cb.answer()
    await _show_feed(cb.message, int(raw))


@router.callback_query(F.data.startswith("hh:apply:"))
async def cb_feed_apply(cb: CallbackQuery) -> None:
    idx = int(cb.data.rsplit(":", 1)[-1])
    items = hh_agent.skipped()
    if idx >= len(items):
        await cb.answer("вакансия уже не в списке", show_alert=True)
        await _show_feed(cb.message, 0)
        return
    v = items[idx]
    await cb.answer("отправляю отклик…")
    # лента остаётся на месте: прогресс и результат идут отдельным сообщением,
    # иначе список пришлось бы листать заново
    progress = await cb.message.answer(
        f"⏳ откликаюсь: {v.get('title')} — {v.get('company')}\n\nпишу письмо под вакансию…")

    result = await hh_agent.apply_single(v["id"])
    try:
        await progress.edit_text(f"{result}\n\n{v.get('title')} — {v.get('company')}")
    except TelegramBadRequest:
        pass
    # вакансия ушла из пропущенных — обновляем ленту на месте, позиция та же
    await _show_feed(cb.message, idx)


@router.callback_query(F.data.startswith("hh:drop:"))
async def cb_feed_drop(cb: CallbackQuery) -> None:
    idx = int(cb.data.rsplit(":", 1)[-1])
    items = hh_agent.skipped()
    if idx >= len(items):
        await cb.answer("вакансия уже не в списке", show_alert=True)
        await _show_feed(cb.message, 0)
        return
    hh_agent.drop_from_skipped(items[idx]["id"])
    await cb.answer("убрал из ленты")
    await _show_feed(cb.message, idx)


# ─────────────────────────── выбор резюме ───────────────────────────

def _resumes_text(resumes: list[dict], current: str) -> str:
    lines = ["📄 Резюме для откликов", ""]
    for r in resumes:
        mark = "▶️" if r["id"] == current else "  "
        lines.append(f"{mark} {r['title']}")
        if r.get("details"):
            lines.append(f"     {r['details']}")
    lines += ["", "Резюме определяет не только то, что увидит работодатель: "
                  "из него берётся профиль для сопроводительных писем."]
    return "\n".join(lines)


def _resumes_kb(resumes: list[dict], current: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=("▶️ " if r["id"] == current else "") + r["title"][:45],
        callback_data=f"hh:resume:{r['id'][:40]}",
    )] for r in resumes]
    rows.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="hh:resumes:refresh"),
                 InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_resumes(target: Message, refresh: bool = False, edit: bool = False) -> None:
    try:
        resumes = await hh_agent.list_resumes(refresh=refresh)
    except hh_agent.HHUnavailable as e:
        await target.answer(f"hh: {e}")
        return
    current = hh_agent.current_resume()
    text, kb = _resumes_text(resumes, current), _resumes_kb(resumes, current)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass
        return
    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "hh:resumes")
async def cb_resumes(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_resumes(cb.message)


@router.callback_query(F.data == "hh:resumes:refresh")
async def cb_resumes_refresh(cb: CallbackQuery) -> None:
    await cb.answer("иду на hh за списком, это ~20 секунд")
    await _show_resumes(cb.message, refresh=True, edit=True)


@router.callback_query(F.data.startswith("hh:resume:"))
async def cb_pick_resume(cb: CallbackQuery) -> None:
    prefix = cb.data.rsplit(":", 1)[-1]
    try:
        resumes = await hh_agent.list_resumes()
    except hh_agent.HHUnavailable as e:
        await cb.answer(str(e), show_alert=True)
        return
    # в callback_data влезает только начало id — ищем полный по префиксу
    picked = next((r for r in resumes if r["id"].startswith(prefix)), None)
    if picked is None:
        await cb.answer("резюме не найдено, обнови список", show_alert=True)
        return
    hh_agent.set_resume(picked["id"])
    await cb.answer(f"выбрано: {picked['title'][:40]}")
    await _show_resumes(cb.message, edit=True)


# ───────────────────────── фильтры поиска ─────────────────────────

def _filters_text(state: dict) -> str:
    cfg = hh_agent.load_config()
    lines = ["🎛 Фильтры поиска вакансий", "",
             f"Запрос: «{cfg.get('query', '—')}» "
             f"(поле {cfg.get('search_field') or 'везде'})", ""]

    def describe(name: str, title: str) -> None:
        _, allowed = hh_agent.FILTERS[name]
        chosen = state.get(name) or []
        picked = [allowed[c] for c in allowed if c in chosen]
        lines.append(f"{title}: {', '.join(picked) if picked else 'любой'}")

    describe("exp", "Опыт работы")
    describe("fmt", "Формат работы")
    lines += ["", "Тап по кнопке включает или выключает значение.",
              "Пусто = не ограничивать. Применится со следующего прогона."]
    return "\n".join(lines)


def _filters_kb(state: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name in ("exp", "fmt"):
        _, allowed = hh_agent.FILTERS[name]
        chosen = state.get(name) or []
        buttons = [InlineKeyboardButton(
            text=("✅ " if code else "") + label if code in chosen else f"◻️ {label}",
            callback_data=f"hh:filter:{name}:{code}",
        ) for code, label in allowed.items()]
        # по две кнопки в ряд — иначе подписи схлопываются в нечитаемые огрызки
        rows += [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_filters(target: Message, edit: bool = False) -> None:
    state = hh_agent.filters_state()
    text, kb = _filters_text(state), _filters_kb(state)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest:
            return
    await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "hh:filters")
async def cb_filters(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_filters(cb.message)


@router.callback_query(F.data.startswith("hh:filter:"))
async def cb_filter_toggle(cb: CallbackQuery) -> None:
    _, _, name, code = cb.data.split(":", 3)
    try:
        chosen = hh_agent.toggle_filter(name, code)
    except (ValueError, KeyError):
        await cb.answer("не знаю такой фильтр", show_alert=True)
        return
    _, allowed = hh_agent.FILTERS[name]
    await cb.answer(f"{allowed[code]}: {'включено' if code in chosen else 'выключено'}")
    await _show_filters(cb.message, edit=True)


# ─────────────────────────── статистика ───────────────────────────

def _bar(n: int, top: int, width: int = 10) -> str:
    return "▇" * max(1, round(n / top * width)) if n and top else ""


def _stats_text(d: dict) -> str:
    f, r, resp = d["funnel"], d["runs"], d["responses"]
    out = [f"📊 Статистика за {d['label']}", ""]

    # Прогоны
    if r["total"]:
        share = round(r["ok"] / r["total"] * 100)
        avg = f", в среднем {r['avg_sec']} с" if r["avg_sec"] else ""
        out.append(f"Прогоны: {r['total']} · успешных {r['ok']} ({share}%), "
                   f"с ошибкой {r['err']}{avg}")
    else:
        out.append("Прогонов за период не было")

    # Воронка
    by = f["by_outcome"]
    applied = by.get("applied", 0)
    seen = sum(by.values())
    out += ["", f"Воронка: обработано {seen} вакансий"]
    for key, label in hh_stats.OUTCOME_LABELS.items():
        if by.get(key):
            out.append(f"  {label}: {by[key]}")

    if f["with_letter"]:
        out.append(f"  из них с персональным письмом: {f['with_letter']}")

    # Оценки
    if f["scored"]:
        out += ["", f"Оценки модели: средняя {f['avg_score']:.1f}/10 "
                    f"(от {f['lo']} до {f['hi']}), оценено {f['scored']}"]
        buckets = f["buckets"]
        if buckets:
            top = max(buckets.values())
            for score in sorted(buckets, reverse=True):
                out.append(f"  {score:>2}/10 {_bar(buckets[score], top)} {buckets[score]}")

    # Ответы работодателей
    kinds = resp["by_kind"]
    invites, rejects = kinds.get("invite", 0), kinds.get("reject", 0)
    pending = kinds.get("pending", 0)
    out += ["", f"Ответы: приглашений {invites}, отказов {rejects}, молчат {pending}"]
    if applied and invites:
        out.append(f"  конверсия отклик → приглашение: {invites / applied * 100:.1f}%")
    for inv in resp["invites"]:
        out.append(f"  🎯 {inv['title']} — {inv['company']}")

    # Куда чаще всего откликались
    if f["top_companies"]:
        out += ["", "Чаще всего откликались:"]
        out += [f"  {c['company']}: {c['c']}" for c in f["top_companies"]]

    # Ошибки прогонов — самое полезное для диагностики
    if r["last_errors"]:
        out += ["", "Последние сбои прогонов:"]
        out += [f"  {when[:16]} — {why}" for when, why in r["last_errors"]]

    return "\n".join(out)


def _stats_kb(period: str) -> InlineKeyboardMarkup:
    tabs = [InlineKeyboardButton(
        text=("• " if period == key else "") + label.capitalize(),
        callback_data=f"hh:stats:{key}",
    ) for key, (label, _) in hh_stats.PERIODS.items()]
    return InlineKeyboardMarkup(inline_keyboard=[
        tabs,
        [InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")],
    ])


@router.callback_query(F.data.startswith("hh:stats:"))
async def cb_stats(cb: CallbackQuery) -> None:
    await cb.answer()
    period = cb.data.rsplit(":", 1)[-1]
    try:
        data = hh_stats.collect(period)
    except hh_agent.HHUnavailable as e:
        await cb.message.answer(f"hh: {e}")
        return
    text, kb = _stats_text(data), _stats_kb(period)
    # переключение вкладок правит то же сообщение, первый заход — новое
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)


# ─────────────────────────── стиль письма ───────────────────────────

def _letter_kb(has_notes: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✏️ Добавить правку", callback_data="hh:note_add"),
             InlineKeyboardButton(text="👀 Показать пример", callback_data="hh:note_preview")]]
    if has_notes:
        rows.append([InlineKeyboardButton(text="🗑 Сбросить правки", callback_data="hh:note_clear")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="hh:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _letter_text(notes: list[str]) -> str:
    if not notes:
        return ("✍️ Сопроводительные пишет модель под каждую вакансию.\n\n"
                "Правок стиля пока нет. Добавь — и они будут учитываться во всех "
                "будущих письмах.\n\nНапример: «пиши короче», «не упоминай Android», "
                "«всегда добавляй ссылку на GitHub».")
    lines = ["✍️ Правки стиля писем (учитываются в каждом письме):", ""]
    lines += [f"  {i}. {n}" for i, n in enumerate(notes, 1)]
    return "\n".join(lines)


@router.callback_query(F.data == "hh:letter")
async def cb_letter(cb: CallbackQuery) -> None:
    await cb.answer()
    notes = hh_agent.letter_notes()
    await cb.message.answer(_letter_text(notes), reply_markup=_letter_kb(bool(notes)))


@router.callback_query(F.data == "hh:note_add")
async def cb_note_add(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(HHStates.letter_note)
    await cb.message.answer("Что поправить в письмах? Напиши одной фразой:")


@router.message(HHStates.letter_note, F.text)
async def on_note(message: Message, state: FSMContext) -> None:
    await state.clear()
    notes = hh_agent.add_letter_note(message.text)
    await message.answer("Учту в следующих письмах.\n\n" + _letter_text(notes),
                         reply_markup=_letter_kb(bool(notes)))


@router.callback_query(F.data == "hh:note_clear")
async def cb_note_clear(cb: CallbackQuery) -> None:
    hh_agent.clear_letter_notes()
    await cb.answer("правки сброшены")
    await cb.message.edit_text(_letter_text([]), reply_markup=_letter_kb(False))


@router.callback_query(F.data == "hh:note_preview")
async def cb_note_preview(cb: CallbackQuery) -> None:
    await cb.answer("генерирую пример")
    note = await cb.message.answer("⏳ пишу письмо на последней вакансии из истории…")
    await note.edit_text(await hh_agent.preview_letter())


# ──────────────────────────── порог отбора ────────────────────────────

@router.callback_query(F.data == "hh:score")
async def cb_score(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(HHStates.min_score)
    current = hh_agent.load_config().get("llm", {}).get("min_score", 6)
    await cb.message.answer(
        f"🎚 Порог отбора — сейчас {current} из 10.\n\n"
        "Как это работает: перед откликом бот открывает вакансию, читает описание "
        "и просит модель оценить, насколько она тебе подходит — от 0 до 10. "
        "Оценка учитывает твой стек, уровень и формат работы.\n\n"
        "Откликается только на те, у кого оценка не ниже порога.\n\n"
        f"Сейчас {current} — значит вакансия с оценкой {current} пройдёт, "
        f"а с {current - 1} будет отброшена.\n\n"
        "Ниже порог — больше откликов, но среди них появится случайное.\n"
        "Выше порог — только близкие вакансии, но их мало.\n\n"
        "Посмотреть оценки с обоснованием: «🔍 Без откликов».\n\n"
        "Пришли число от 0 до 10:")


@router.message(HHStates.min_score, F.text)
async def on_score(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 10:
        await message.answer("Нужно целое число от 0 до 10. Попробуй ещё раз:")
        return
    await state.clear()
    value = hh_agent.set_min_score(int(raw))
    await message.answer(f"Порог отбора: {value}/10. Применится со следующего прогона.")
    await _show_status(message)


# ─────────────────── уведомления о приглашениях ───────────────────

async def push_invitations(bot, owner_id: int) -> int:
    """Разослать неотправленные приглашения и отказы. Вызывается планировщиком.

    Данные готовит крон-задача hh-responses (она ходит в браузер); бот только
    читает базу — поэтому вызов дешёвый и его можно делать часто.
    """
    sent_total = 0

    # итог прогона — отдельным сообщением, если включено в «⏱ Режим»
    report = hh_agent.pending_run_report()
    if report:
        try:
            await bot.send_message(owner_id, report)
            sent_total += 1
        except Exception:  # noqa: BLE001
            log.exception("не смог отправить итог прогона")

    prefs = hh_agent.notify_settings()
    items = [i for i in hh_agent.pending_invitations()
             if (i["kind"] == "invite" and prefs["invites"])
             or (i["kind"] == "reject" and prefs["rejects"])]
    if not items:
        return sent_total

    sent: list[str] = []
    for item in items:
        if item["kind"] == "invite":
            text = (f"🎯 Приглашение!\n\n{item['title']}\n{item['company']}\n"
                    f"Статус: {item['status']}")
            if item.get("url"):
                text += f"\n\n{item['url']}"
        else:
            text = f"❌ Отказ: {item['title']} — {item['company']}"
        try:
            await bot.send_message(owner_id, text)
            sent.append(item["key"])
        except Exception:  # noqa: BLE001 — недоставленное попробуем в следующий тик
            log.exception("не смог отправить уведомление по %s", item["key"])

    hh_agent.mark_announced(sent)
    return sent_total + len(sent)
