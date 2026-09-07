# API / Контракт бота — psychbot

Документ описывает «контракт» бота: команды, callback-протокол inline-кнопок,
схему данных (SQLite + frontmatter Obsidian), флаги риска и точки расширения.
Для single-user Telegram-бота это аналог API-спецификации.

> Стек: Python 3.11+, aiogram 3.x, aiosqlite, APScheduler, ruamel.yaml.
> Источники требований: `MASTER-PLAN.md` (психология), `ARCHITECTURE.md` (tech).

---

## 1. Аутентификация / авторизация

Single-user. Авторизация = сравнение `from_user.id` с `OWNER_ID` (из `.env`).
Реализована как **outer middleware** `OwnerOnlyMiddleware` на `Dispatcher.update`:
любой апдейт не от владельца **молча игнорируется** (без ответа), факт пишется в лог
как `ignored update from non-owner`.

Секреты (`BOT_TOKEN`, `OWNER_ID`) — только в `.env` (gitignore), не в коде/конфиге.

---

## 2. Команды (Message commands)

| Команда | Назначение | Ответ |
|---|---|---|
| `/start` | Приветствие + инструкция, показать reply-меню | Текст + `ReplyKeyboard` |
| `/menu` | Показать reply-меню | `ReplyKeyboard` |
| `/help` | Как работает бот | Текст |
| `/settings` | Показать расписание + подсказки по изменению | Текст |
| `/set_time <slot> ЧЧ:ММ ЧЧ:ММ` | Изменить окно слота (morning/day/evening) | Подтверждение |
| `/toggle_slot <slot> <on\|off>` | Включить/выключить слот | Подтверждение |
| `/reload_schedule` | Применить изменения расписания немедленно | Подтверждение |

Ошибки валидации команд возвращают человекочитаемую подсказку формата, не падают.

### Reply-меню (кнопки, §3.4 плана)
`🆘 Накрыло` (всегда видна) · `☀️ Утро` · `🌤 День` · `🌙 Вечер` · `📝 Заметка` · `✏️ Поправить последнее`.

---

## 3. Callback-протокол (inline-кнопки)

Все callback'и типизированы через `CallbackData` factory (никакого ручного парсинга строк).

| Factory | prefix | Поля | Где |
|---|---|---|---|
| `ScaleCB` | `scale` | `metric:str, value:int` | шкалы valence/arousal/anxiety/self_criticism/felt_vs_analyzed/intensity |
| `EmotionCB` | `emo` | `value:str` | мультивыбор эмоций (toggle) |
| `BodyCB` | `body` | `value:str` | мультивыбор зон тела (toggle) |
| `TriggerCB` | `trig` | `value:str` | категория триггера (single) |
| `SituWhatCB` | `situw` | `value:str` (`emo:*`/`trig:*`) | ситуативное «на что похоже» |
| `RegulationCB` | `reg` | `value:str` | мультивыбор регуляции (toggle) |
| `EnumCB` | `enum` | `metric:str, value:str` | sleep_quality/rumination_level/human_contact |
| `RuminationTopicCB` | `rumtop` | `value:str` | мультивыбор тем руминации (toggle) |
| `ActionCB` | `act` | `name:str` | `expand`/`enough`/`skip`/`done`/`yes`/`no` |

**Мультивыбор** (ADR-6): toggle редактирует ОДНО сообщение, выбранное помечается `✓`,
завершение — кнопкой «Готово» (`ActionCB(name="done")`). Сообщения не плодятся.

---

## 4. Сценарии (FSM)

Каждый сценарий — отдельный `Router` + `StatesGroup`. Все запускаются либо
планировщиком, либо вручную из меню через `start_*(message, state, ctx)`.

| Слот | Router | Поток (§3 плана) |
|---|---|---|
| Утро | `checkin_morning` | valence → arousal → ротация(sleep/emotion) → опц. body_word → запись |
| День | `checkin_day` | arousal → emotions* → body_tension* → trigger → запись |
| Вечер | `checkin_evening` | valence → anxiety → self_criticism → rumination(→topics*) → [Расширить→deep / Хватит] → **всегда заземление** |
| Ситуативно | `situational` | body_tension* → emotion/trigger → intensity → опц. текст → **всегда заземление** |
| Заметка | `notes` | свободный текст → секция `📝 Заметка` в дневник |
| Голос | `voice` | сохранить .ogg в attachments + плейсхолдер `![[voice_*.ogg]]` (без транскрипции) |
| Правка | `edit_last` | показать последнюю секцию → новый текст → заменить (атомарно) |

`*` — мультивыбор. **Deep dive вечера:** felt_vs_analyzed → human_contact →
ресурс/радость ИЛИ злость (ротация 1:3) → regulation* → опц. свободное поле.

**Тон (§5.3):** нейтрально-принимающий. Никаких «молодец/серия/ты пропустил».
После низких баллов — нейтральное подтверждение без советов. Вечер и ситуативный
ВСЕГДА завершаются заземлением (`prompts/grounding.py`), не вопросом.

---

## 5. Схема данных

### 5.1 SQLite (`data/psychbot.db`) — внутреннее состояние, source-of-truth ответов

| Таблица | Назначение |
|---|---|
| `checkins` | сессия чек-ина: `answers_json`, `flags_json`, `status`, `written` |
| `schedule` | окна слотов (`window_start/end`, `enabled`) |
| `settings` | key-value (`light_day_weekday`, `tone`, …) |
| `outbox` | очередь надёжной записи в Obsidian (ретраи + backoff) |
| `rotation_state` | анти-повтор формулировок (`last_index` на пул) |

Все запросы параметризованные (репозитории в `storage/repositories.py`).

### 5.2 Obsidian frontmatter (презентационный слой, `YYYY-MM-DD.md`)

Один файл на день, append по слотам. Ключи — **только** из `domain/metrics.py`.

**Правила записи (КРИТИЧНО, §4.3 плана):**
- слотовые метрики получают суффикс: `valence_morning`, `arousal_day`, …;
- неспрошенная метрика = `null` или ключ опущен — **никогда 0**;
- списочные метрики (emotions, body_tension, triggers, regulation_used,
  rumination_topics, flags) **объединяются (union)** между слотами;
- порядок ключей стабилен (`FRONTMATTER_KEY_ORDER`);
- запись атомарна: `.tmp` + `os.replace()` + `asyncio.Lock` per-date.

### 5.3 Реестр метрик (`domain/metrics.py`)
Единственный источник истины ключей. `MetricSpec(key, title, type, scale, options, per_slot)`.
Палитры: `EMOTIONS`, `BODY_ZONES`, `TRIGGER_TYPES`, `REGULATION`, `SLEEP_QUALITY`,
`RUMINATION_TOPICS`, `HUMAN_CONTACT`.

---

## 6. Флаги риска (§6 плана)

Считаются в `domain/flags.py` ПОСЛЕ записи, **не показываются** пользователю и
**не диагностируют** — влияют только на тон следующего сообщения.

| Флаг | Условие (стартовое, калибруется) | Реакция |
|---|---|---|
| `flag_self_attack` | маркеры «ненавижу себя/тупой/жалкий…» в тексте | перенаправить агрессию с себя |
| `flag_apathy` | apathy ≥4 дней подряд И arousal ≤3 | снизить требования до 1 телесного действия |
| `flag_jealousy` | ревность ≥2×/день | заземляющий микро-промпт, без расследования |
| `flag_isolation` | human_contact=нет ≥3 дней | «напиши человеку, а не мне» |
| `flag_perform` | felt_vs_analyzed ≤3 | «сегодня без анализа: тело — одно слово» |

Приоритет реакции: self_attack > apathy > jealousy > isolation > perform.
Пороги вынесены в константы вверху `flags.py` для лёгкой калибровки психологом.

---

## 7. Надёжность записи (outbox)

`OutboxWorker` каждые `poll_interval` (30с) берёт `done=0` записи с наступившим
`next_attempt_at`, пишет в vault. Успех → `done=1` + `checkin.written=1`. Ошибка →
`attempts++` и exponential backoff (`base*2^attempts`, потолок 1800с). Гарантия:
завершённый чек-ин не теряется, даже если Яндекс.Диск занят. Пользователю
подтверждение отправляется сразу (данные уже в SQLite).

---

## 8. Точки расширения

- `transcription/transcriber.py` — `Transcriber` + `NullTranscriber` (MVP). Для
  расшифровки ГС добавить `LocalWhisperTranscriber` (рекомендация §6 ARCHITECTURE),
  остальной код не меняется.
- Пороги флагов и времена слотов — настраиваемы (БД `settings`/`schedule`, `config.toml`).

---

## 9. Узкие места (✅/⚠️/❓)

- ✅ **Надёжно:** атомарная запись + outbox-ретраи; OWNER-фильтр; параметризованный SQL;
  null-правила frontmatter; ротация без повторов; FSM по сценариям плана.
- ⚠️ **Наблюдать:** конкуренция записи близких слотов (закрыто lock + тестами);
  misfire APScheduler после сна Mac (намеренно не догоняем); конфликты Яндекс.Диска
  при ручной правке в Obsidian (single-writer + свежее чтение митигируют).
- ❓ **Вне dev:** финальные тексты этической эскалации и калибровка порогов флагов —
  за lead-психологом до запуска; выбор STT при появлении ключа.
