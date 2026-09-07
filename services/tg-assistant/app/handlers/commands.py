"""Команды: /start /menu /help /settings /sos + точки входа в чек-ины из меню.

Меню — inline-кнопки (см. keyboards/menu.py). Текстовые хендлеры по BTN_*
оставлены как совместимость: у клиента может быть закэширована старая
reply-клавиатура, и её нажатия должны продолжать работать.

OWNER_ID-фильтр обеспечивается outer middleware (см. _owner.py), здесь не дублируем.
Все тексты — нейтрально-принимающий тон (§5.3).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.handlers import (
    agency,
    checkin_day,
    checkin_evening,
    checkin_morning,
    deed,
    habits,
    review,
    situational,
)
from app.handlers._service import AppContext
from app.keyboards import menu
from app.storage.repositories import Repositories

router = Router(name="commands")


START_TEXT = (
    "Привет. Я тихий помощник для коротких отметок состояния.\n\n"
    "Не коуч и не диагност — просто рядом. Спрашиваю коротко, чаще одним тапом: "
    "тон, энергия, что в теле. Анализировать не прошу.\n\n"
    "Три коротких отметки в день: утром, днём, вечером. Можно пропускать — "
    "никаких серий и упрёков.\n\n"
    "Если накрыло в любой момент — жми «🆘 Накрыло» под сообщением или /sos.\n\n"
    "/menu — кнопки · /sos — накрыло · /help — как это работает · /settings — расписание"
)

HELP_TEXT = (
    "Как это работает:\n\n"
    "• Три отметки в день — утро, день, вечер. Каждая 20–40 секунд, одним тапом.\n"
    "• «🆘 Накрыло» (или /sos) — если тяжело прямо сейчас. Два вопроса, без разбора.\n"
    "• «🧭 Развилка» и «📌 Поступок» — про твои действия, не про состояние. "
    "Что это и зачем — команда /author.\n"
    "• «🌱 Привычки» — свои привычки (хорошие и вредные), всё кнопками: "
    "➕ добавить, 🗑 убрать, ✅ отметить за день (хорошую — сделал, вредную — "
    "сорвался). Счётчиков в отметке нет — динамика в 📈 Аналитике.\n"
    "• «📝 Заметка» — кинуть мысль; сохраню отдельной заметкой с тегами.\n"
    "• «✏️ Поправить последнее» — заменить текст последней записи. После правки "
    "появится «↩️ Отменить правку» (или команда /undo).\n"
    "• «📊 Сегодня» — что отмечено сегодня, заметки и накрывало ли.\n"
    "• «📈 Аналитика» — карта состояний за месяц (как на GitHub), тап по дню — "
    "стата дня, «Σ за месяц» — сводка.\n"
    "• «✅ Задачи» — задачи с синхронизацией Obsidian и Todoist: тап по кружку — "
    "выполнить, 🗑 — удалить, «➕ Добавить» или /add текст.\n"
    "• В любом выборе есть «✏️ Своё» — вписать ответ текстом.\n\n"
    "Напоминание по каждому слоту приходит ровно один раз в день — не отметил, "
    "и ладно, дёргать не буду. Отметить можно в любой момент кнопкой в меню.\n"
    "Всё пишется в твой Obsidian — один файл на день.\n"
    "Пропуски — это нормально. Никаких стриков.\n\n"
    "/settings — посмотреть и поменять время напоминаний."
)


# Полное простое объяснение блока авторства — вызывается командой /author.
AUTHOR_TEXT = (
    "🧭📌 Авторство — как это работает\n\n"
    "Утренний/дневной/вечерний чек-ин меряют СОСТОЯНИЕ — как тебе. "
    "«Развилка» и «Поступок» меряют другое — ДЕЙСТВИЯ. Не «какой ты», "
    "а «что ты сделал». Их не сводят в балл, стриков нет, пропуск — это ноль, "
    "а не минус.\n\n"
    "Одна мерка на всё: РЕШИЛ САМ · БЫЛО СТРАШНО · СДЕЛАЛ.\n\n"
    "🧭 Развилка (раз в день)\n"
    "Был ли сегодня момент выбора — и кто выбрал.\n"
    "• Решил сам — решение твоё: не по приказу и не назло. Проверка: выбрал бы "
    "то же, даже если бы близкий сказал «да», и даже если бы никто не узнал?\n"
    "• Подчинился — сделал, потому что сказали/ждут.\n"
    "• Поспорил-назло — пошёл против только чтобы не подчиниться (это тоже не "
    "твой выбор — просто наоборот).\n"
    "• Развилки не было — честный ответ, не провал.\n"
    "Кому это относилось — обобщённо: близкие / друзья / дело / сам с собой. "
    "Имена не нужны — важна роль, а не человек.\n\n"
    "📌 Поступок (когда случилось, без напоминаний)\n"
    "Ловится постфактум: «стало нормально с собой — что я только что сделал "
    "с живым человеком?»\n"
    "• Столкнулся/накрыло → что сделал: вышел, проглотил из страха или сорвался.\n"
    "• Сказал тяжёлое / попросил — кому: тому, кто поддержит, или тому, кто "
    "оценивает.\n"
    "• Начал контакт первым.\n\n"
    "📅 Обзор недели — 3 вопроса: где действовал (только один или с живыми людьми), "
    "спорил ли по делу, кому нёс важное.\n"
    "🗓 Обзор месяца — 7 фактов да/нет про действия за месяц. Что не отметил = "
    "не было. Итога и суммы нет.\n\n"
    "Зачем: страх не значит «нельзя» — страх значит «важно». Мерка «решил и сделал» "
    "проверяется сразу, её ставишь ты сам, и оценку по ней не надо ни у кого "
    "отстаивать."
)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    # снимаем старую reply-клавиатуру: она кэшируется на клиенте и висела бы
    # под inline-меню, дублируя те же кнопки
    await message.answer("Меню теперь под сообщением.", reply_markup=menu.drop_reply_kb())
    await message.answer(START_TEXT, reply_markup=menu.main_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer("Меню. Можно одним тапом.", reply_markup=menu.main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    # меню прикреплено к справке: inline-кнопки уезжают с историей чата,
    # и /help — второе место, где их удобно достать
    await message.answer(HELP_TEXT, reply_markup=menu.main_menu_kb())


@router.message(Command("author"))
async def cmd_author(message: Message) -> None:
    # полное простое объяснение блока авторства (Развилка/Поступок/обзоры)
    await message.answer(AUTHOR_TEXT, reply_markup=menu.main_menu_kb())


@router.message(Command("settings"))
async def cmd_settings(message: Message, repos: Repositories) -> None:
    rows = await repos.schedule.all()
    by_slot = {r.slot: r for r in rows}
    lines = ["Расписание напоминаний (локальное время):", ""]
    names = {"morning": "☀️ Утро", "day": "🌤 День", "evening": "🌙 Вечер"}
    for slot in ("morning", "day", "evening"):
        r = by_slot.get(slot)
        if not r:
            continue
        status = "" if r.enabled else "  (выключено)"
        lines.append(f"{names[slot]}: {r.window_start}–{r.window_end}{status}")
    lines += [
        "",
        "Поменять время:",
        "  /set_time morning 11:00 11:30",
        "Включить/выключить слот:",
        "  /toggle_slot day off",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("reload_schedule"))
async def cmd_reload_schedule(message: Message, scheduler) -> None:  # noqa: ANN001
    await scheduler.reload_all()
    await message.answer("Расписание перечитано и применено.")


@router.message(Command("set_time"))
async def cmd_set_time(message: Message, repos: Repositories) -> None:
    parts = (message.text or "").split()
    if len(parts) != 4 or parts[1] not in ("morning", "day", "evening"):
        await message.answer("Формат: /set_time <morning|day|evening> ЧЧ:ММ ЧЧ:ММ")
        return
    slot, start, end = parts[1], parts[2], parts[3]
    if not (_valid_hhmm(start) and _valid_hhmm(end)):
        await message.answer("Время в формате ЧЧ:ММ, например 10:30.")
        return
    await repos.schedule.update_window(slot, start, end)
    await message.answer(
        f"Готово. {slot}: {start}–{end}. Применится при следующем запуске планировщика.\n"
        "Чтобы применить сразу — /reload_schedule."
    )


@router.message(Command("toggle_slot"))
async def cmd_toggle_slot(message: Message, repos: Repositories) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in ("morning", "day", "evening") or parts[2] not in ("on", "off"):
        await message.answer("Формат: /toggle_slot <morning|day|evening> <on|off>")
        return
    slot, onoff = parts[1], parts[2]
    await repos.schedule.set_enabled(slot, onoff == "on")
    await message.answer(f"{slot}: {'включён' if onoff == 'on' else 'выключен'}. /reload_schedule для применения.")


def _valid_hhmm(value: str) -> bool:
    try:
        h, m = value.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False


# ---- кнопки меню -> запуск сценариев вручную ----
# Стартеры принимают Message и отвечают в чат. У callback берём cb.message
# (сообщение бота) — ответ уходит в тот же чат, логика сценариев не меняется.

@router.message(F.text == menu.BTN_OVERWHELMED)
@router.message(Command("sos"))
async def menu_overwhelmed(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await situational.start_situational(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "sos"))
async def cb_overwhelmed(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await situational.start_situational(cb.message, state, ctx)


@router.message(F.text == menu.BTN_MORNING)
async def menu_morning(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await checkin_morning.start_morning(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "morning"))
async def cb_morning(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await checkin_morning.start_morning(cb.message, state, ctx)


@router.message(F.text == menu.BTN_DAY)
async def menu_day(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await checkin_day.start_day(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "day"))
async def cb_day(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await checkin_day.start_day(cb.message, state, ctx)


@router.message(F.text == menu.BTN_EVENING)
async def menu_evening(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await checkin_evening.start_evening(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "evening"))
async def cb_evening(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await checkin_evening.start_evening(cb.message, state, ctx)


# ---- агентность / авторство ----

@router.message(F.text == menu.BTN_AGENCY)
@router.message(Command("fork"))
async def menu_agency(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await agency.start_agency(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "agency"))
async def cb_agency(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await agency.start_agency(cb.message, state, ctx)


@router.message(F.text == menu.BTN_DEED)
@router.message(Command("deed"))
async def menu_deed(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await deed.start_deed(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "deed"))
async def cb_deed(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await deed.start_deed(cb.message, state, ctx)


@router.message(F.text == menu.BTN_HABITS)
async def menu_habits(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await habits.start_habits(message, state, ctx)


@router.callback_query(menu.MenuCB.filter(F.action == "habits"))
async def cb_habits(cb: CallbackQuery, state: FSMContext, ctx: AppContext) -> None:
    await cb.answer()
    await habits.start_habits(cb.message, state, ctx)


@router.message(Command("week"))
async def cmd_week(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await review.start_weekly(message, state, ctx)


@router.message(Command("month"))
async def cmd_month(message: Message, state: FSMContext, ctx: AppContext) -> None:
    await review.start_monthly(message, state, ctx)
