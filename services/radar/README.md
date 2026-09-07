# Personal OS Radar

Агрегатор инфополя: собирает свежее из **RSS-лент** и твоих **Telegram-каналов**,
отбрасывает уже виденное, сводит в **дайджест трендов** (через локальный Claude) и
кладёт в Obsidian и/или Telegram. Чтобы всегда быть в курсе — без ручного листания.

Зависимостей нет — стандартная библиотека Python 3.11+ плюс уже стоящие драйверы
Obsidian/Telegram. Сам по расписанию **не крутится** — его запускает
[`cron`-сервис](../cron) (планировщик), это разделение «способность ↔ расписание».

---

## Как работает

```
RSS-ленты  ─┐
            ├─► сбор (свежее, за lookback_hours) ─► дедуп (state.json)
TG-каналы  ─┘                                            │
                                                         ▼
                              синтез трендов через claude-local-api
                              (если выключен — сырой дайджест по источникам)
                                                         │
                                          ┌──────────────┴──────────────┐
                                          ▼                             ▼
                                  Obsidian (Инфополе.md)        Telegram (канал/Избранное)
```

---

## Команды

```bash
cd ~/Desktop/personal\ os/services/radar

# Векторная база новостей (RSS → SQLite + эмбеддинги)
./radar.py ingest        # собрать свежий RSS и векторизовать новое в radar.db (фоновая задача)
./radar.py vectorize     # догнать эмбеддинги для строк без вектора
./radar.py search "запрос" [k]   # семантический поиск по накопленным новостям
./radar.py db-stats      # сколько новостей в базе, по источникам

# Дайджест
./radar.py run --dry     # собрать и НАПЕЧАТАТЬ дайджест (без доставки)
./radar.py run           # собрать и доставить по настройкам sources.json

# Прочее
./radar.py test          # проверить, что ленты и TG-драйвер отвечают
./radar.py sources       # показать текущие источники
./radar.py discover-tg   # вывести список твоих каналов для sources.json
```

---

## Векторная база новостей (фон)

RSS-новости копятся в `radar.db` (SQLite) и векторизуются локальной ONNX-моделью
**multilingual-e5-small** (`model/`, onnxruntime + tokenizers — без torch, легко для фона).
Размерность 384, эмбеддинг хранится BLOB-ом (struct float32). Дедуп — по `uid`
(ссылка/идентификатор), повторный `ingest` ничего не дублирует.

> e5 требует префиксы: документы кодируются как `passage: …`, запросы — `query: …`
> (это уже зашито в `store.py`).

**Запуск в фоне — через cron-сервис** (нужен framework-python3, в нём onnxruntime):
```bash
cd ../cron
./cronctl.py add news-ingest --schedule "@every 1h" \
  --shell "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           '/Users/dimitrisimonyan/Desktop/personal os/services/radar/radar.py' ingest --vectorize-pending"
```
Чтобы это реально крутилось без терминала — установить cron как сервис:
`cd ../cron && bash deploy/install.sh`.

**Требования:** `onnxruntime`, `tokenizers`, `numpy` (уже стоят в framework-python3).
`run`-дайджест дополнительно использует claude-local-api для синтеза трендов.

---

## Настройка источников — sources.json

```json
{
  "settings": {
    "lookback_hours": 24,
    "max_per_source": 15,
    "model": "sonnet",
    "digest_obsidian": "10 — Claude/Рабочее пространство/personal-os/Инфополе.md",
    "digest_telegram": null
  },
  "rss": [
    { "name": "Hacker News", "url": "https://hnrss.org/frontpage" }
  ],
  "telegram_channels": [
    { "name": "Дуров", "chat": "@durov" }
  ]
}
```

- `digest_obsidian` — заметка, куда дозаписывать дайджест (null — выключить).
- `digest_telegram` — куда слать дайджест: `"me"` (Избранное), `"@свой_канал"` или null.
  Если оба null — дайджест печатается в консоль.
- `model` — для синтеза трендов: `haiku`/`sonnet`/`opus` (нужен поднятый claude-local-api).

**Добавить TG-каналы:** `./radar.py discover-tg` покажет всё, на что ты подписан в
формате готовых строк — скопируй нужные в `telegram_channels`.

Стартовые RSS-ленты (можно менять): Hacker News, TechCrunch, The Verge, Habr, VC.ru,
MIT Tech Review.

---

## Запуск по расписанию (через cron-сервис)

Два дайджеста в день — утром и вечером:

```bash
cd ../cron
./cronctl.py add infofield --schedule "0 9,21 * * *" \
  --shell "/usr/bin/python3 '/Users/dimitrisimonyan/Desktop/personal os/services/radar/radar.py' run" \
  --desc "Дайджест инфополя 2x/день"

./cronctl.py run infofield   # проверить сейчас
```

Доставку (Obsidian/Telegram) radar делает сам по `sources.json`, поэтому в cron-задаче
приёмник не нужен — только запуск.

---

## Что нужно для полного режима

- **Синтез трендов** работает, когда поднят
  [`claude-local-api`](../claude-local-api-main) (`python server.py`). Без него radar
  всё равно отдаёт сырой дайджест, сгруппированный по источникам.
- **TG-парсинг** использует `~/.claude/skills/telegram/driver.cjs` (уже настроен).

---

## Файлы

```
radar/
├── radar.py       — оркестратор + CLI
├── collectors.py  — сбор: RSS (urllib+xml) и Telegram (driver.cjs)
├── analyze.py     — синтез трендов (claude-local-api) + fallback
├── client.py      — клиент к claude-local-api
├── sources.json   — ленты, каналы, настройки
├── state.json     — что уже видели (дедуп; создаётся автоматически)
└── logs/radar.log
```
