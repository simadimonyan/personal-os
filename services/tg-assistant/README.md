# psychbot — Telegram-бот психологического трекинга

Персональный single-user бот для коротких отметок состояния. Пишет структурированные
данные в Obsidian-дневник (один файл на день), напоминает 3 раза в день, заземляет
вместо анализа. Построен по `MASTER-PLAN.md` (психология) и `ARCHITECTURE.md` (tech).

> Не коуч и не диагност. Спрашивает коротко (тон, энергия, тело), не подталкивает к
> нарративу, не ставит стрики, не упрекает за пропуски. Вечер и «накрыло» всегда
> завершаются заземлением.

## Стек
Python 3.11+ · aiogram 3.x · aiosqlite (SQLite) · APScheduler · ruamel.yaml · launchd.

---

## Быстрый старт

### 1. Бот и id
- `@BotFather` → создать бота → токен.
- `@userinfobot` → свой Telegram `OWNER_ID`.

### 2. Окружение
```bash
cd /Users/dimitrisimonyan/tg-psych-bot
# нужен системный python 3.11+ (Homebrew/python.org), не uv-standalone
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Конфиг
```bash
cp .env.example .env
# заполни BOT_TOKEN, OWNER_ID, TZ
```
`config.toml` уже содержит путь к vault и стартовое расписание (10:30 / 15:00 / 22:30).

### 4. Запуск вручную (проверка)
```bash
.venv/bin/python run.py
```
Напиши боту `/start`. Должно прийти приветствие с меню.

### 5. Установка как фоновый daemon (launchd)
```bash
./deploy/install.sh      # копирует plist в ~/Library/LaunchAgents, launchctl load
./deploy/uninstall.sh    # снять daemon (данные не трогает)
```
Бот авто-стартует при логине и авто-рестартится при краше (`KeepAlive`).

---

## Структура
```
run.py                  точка входа: бот + scheduler + outbox-worker в одном loop
app/config.py           .env + config.toml -> Settings
app/bot.py              Bot, Dispatcher, роутеры, OWNER-middleware
app/storage/            SQLite: db, миграции, репозитории
app/obsidian/           КРИТИЧНО: атомарная запись, merge frontmatter, outbox
app/domain/             metrics (реестр ключей), checkin, flags, rotation
app/handlers/           8 роутеров-сценариев (§3 плана)
app/keyboards/          inline-клавиатуры (шкалы, мультивыбор, меню)
app/prompts/            ВСЕ тексты для пользователя (ротация, подтверждения, заземление)
app/scheduler/          APScheduler: jobs, jitter
app/transcription/      точка расширения STT (NullTranscriber в MVP)
deploy/                 launchd plist + install/uninstall
tests/                  frontmatter merge, флаги, ротация
```

## Пульт автоотклика hh — `/hh`

Бот управляет сервисом `services/hh-autoapply`, но сам в браузер не ходит:
отклики шлёт крон, бот читает `history.db`, правит `config.json` и умеет
запустить разовый прогон в subprocess.

| Действие | Что делает |
|----------|-----------|
| `/hh` | Карточка: отправлено сегодня/за неделю, отсеяно моделью, средняя оценка, приглашения, статус прогона |
| 🚀 Прогнать сейчас | Разовый прогон `autoapply.py` (до 20 мин, вывод придёт в чат) |
| 🔍 Без откликов | То же, но `--dry` — только показать, что нашлось и как оценено |
| ✍️ Письмо | Правки стиля сопроводительных («пиши короче») — уходят в промпт всех будущих писем; там же кнопка «Показать пример» |
| 🎚 Порог | Минимальная оценка вакансии 0–10 для отклика |
| ⏸ Пауза | Выключает три крон-задачи `hh-autoapply-*` |

Приглашения и отказы от работодателей приходят сами: крон-задача `hh-responses`
опрашивает hh дважды в день и складывает изменения в базу, а тик планировщика
(каждые 5 мин) отправляет неотправленные. Логика — `handlers/hh.py`,
мост к сервису — `integrations/hh_agent.py`.

## Тесты
```bash
.venv/bin/python -m pytest -q
```

## Данные
- Чек-ины → `02 — Внутренний мир/Дневник состояний/YYYY-MM-DD.md` (Obsidian).
- Внутреннее состояние → `data/psychbot.db` (SQLite, gitignore).
- Логи → `logs/bot.log` (ротация; БЕЗ содержимого ответов — приватность).

## Конвенции
- Ключи YAML-метрик — только из `domain/metrics.py`.
- Тексты для пользователя — только из `prompts/`.
- Пути к vault — только через `config.toml` / `obsidian/paths.py`.
- Весь файловый I/O — через `asyncio.to_thread`; запись — атомарная (`.tmp` + `os.replace`).

Полный контракт — в `API.md`.
