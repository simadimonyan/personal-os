---
name: personal-os
description: "Мастер-оркестратор Personal OS Димитри. ИСПОЛЬЗУЙ ЭТОТ СКИЛЛ ДЛЯ ЛЮБОЙ ЗАДАЧИ. Определяет какая команда работает над задачей, координирует взаимодействие между командами, управляет всеми агентами системы. Команды: Personal OS (Obsidian+Telegram+hh), Psych Team (психоанализ), Startup Lab (продукты+маркетинг+HR), Dev Lab (разработка+дизайн). Триггеры: любой запрос, 'запусти', 'сделай', 'помоги', 'придумай', 'проанализируй', 'разработай', 'напиши', 'исследуй', 'улучши', 'обнови', 'повтори', а также прямые запросы к командам или агентам."
---

# Personal OS — Мастер-оркестратор

Единая точка входа для всей системы агентов Димитри. Анализирую запрос, определяю команду или комбинацию команд, запускаю агентов в режиме Agent Teams, координирую кросс-командную работу.

---

## Команды системы

| Команда | Ключевые агенты | Когда |
|---------|----------------|-------|
| **Personal OS** | knowledge-agent, action-agent | Obsidian, Telegram, hh.ru, утренний брифинг |
| **Psych Team** | cognitive, emotional, relational, lead-psychologist | Самоанализ, психологический портрет, паттерны |
| **Startup Lab** | market-researcher, product-manager, growth-marketer, smm-specialist, business-psychologist, hr-specialist | Продукты, рынок, контент, HR, бизнес-трекинг |
| **Dev Lab** | tech-lead, frontend, backend, ui-web, ui-mobile, ux-researcher, ux-architect, code-reviewer, design-reviewer, tech-writer | Разработка, дизайн-система, UX/UI, документация |
| **Research Lab** | research-scout, deep-researcher, pattern-finder, idea-generator, academic-writer, simplifier | Рисёрч, NotebookLM, научные статьи по ГОСТ, идеи, скрытые связи, простые объяснения |
| **Office Bureau** | office-scout, document-writer, data-visualizer, presentation-designer | Документы, отчёты, договоры, графики, презентации по ГОСТ и делопроизводству |
| **Tutor Lab** | learning-needs-analyst, curriculum-architect, lecturer, exercise-designer, assessment-designer, learning-coach | Обучение: программа курса, лекции, задания, тесты, план обучения по теме |

### Системные команды (обслуживание знаний)

| Команда | Агенты/инструменты | Когда |
|---------|-------------------|-------|
| **brainstorm** | vault-indexer + vsearch (brainstorm/build_graph) | Обновить L4-память (семантический граф): новые скрытые связи между разделами |
| **update-memory** | vault-indexer + memory-extractor + vsearch | Обновить всю L-память агентов (L0→L1→L2→L3→L4), переиндексировать заметки, перестроить граф |

Триггеры: `brainstorm`, `найди связи`, `скрытые связи`, `обнови граф` → **brainstorm**;
`update-memory`, `обнови память`, `обнови L-память`, `проиндексируй`, `актуализируй персону` → **update-memory**.

---

## Phase 0: Контекст и загрузка знаний

### 0a. vault-indexer (ПЕРВЫЙ ШАГ — всегда)

Перед любой командой которая работает с Obsidian — запускать `vault-indexer` агента:

```
Agent(vault-indexer): проверь индекс, обнови если нужно, найди новые скрытые связи
```

Агент работает быстро (inkremental), возвращает одну строку статуса и обновляет семантический граф.
Пропустить только если: задача не касается Obsidian, или vault-indexer уже запускался < 30 мин назад в этой сессии.

### 0b. Чтение памяти (L3 → L2 → L1 → L4)

Память Personal OS — 5-уровневая: `L0 Raw → L1 Atomic → L2 Scene → L3 Persona → L4 Semantic Graph`.
L0–L3 — линейная память; L4 — ассоциативная (семантический граф). Подробнее: `10 — Claude/Memory/README.md`.

```bash
# L3 Persona — всегда, даёт персонализацию поведения
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"path":"10 — Claude/Memory/L3 Persona/persona.md"}'

# L2 Scene Block — если задача относится к известной сцене
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"path":"10 — Claude/Memory/L2 Scene Blocks/L2 {сцена}.md"}'
# ядро сцены тонкое; детали по дням — в L2 Scene Blocks/{сцена}/{сцена} {дата}.md (читать только нужный день)

# L1 последние факты — если нужна свежая детализация
node ~/.claude/skills/obsidian/driver.mjs obsidian_recent '{"section":"10","limit":3}'

# L4 Semantic Graph — скрытые связи по теме (ассоциативная память)
python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py brainstorm "{тема}" 10
```

### 0c. База знаний Obsidian

Перед запуском любой команды прочитай релевантный контекст:

```bash
# Контекст агентов которые будут задействованы
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"{agent-name}"}'

# Профиль Димитри если задача личная
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"профиль", "limit":5}'

# Тема задачи — не повторяй уже сделанное
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}", "limit":10}'
```

**Три слоя контекста — использовать перед каждой задачей:**
```bash
# 1. Текстовый (быстрый, точные слова)
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}","limit":5}'

# 2. Семантический (по смыслу)
python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py hybrid "{тема}" 10

# 3. Скрытые связи (неожиданные пересечения между разделами)
python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py brainstorm "{тема}" 10
```

**Граф базы знаний (L4-память)** — читать для структурного/ассоциативного контекста:
```bash
open ~/Desktop/personal\ os/tools/obsidian/tools/graph/graph.html
python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py build_graph
```
Инструменты: `~/Desktop/personal os/tools/obsidian/tools/` (vsearch + graph)
Лог связей L4: индекс `10 — Claude/Memory/L4 Semantic Graph/Semantic Graph Log.md` + файлы-дни `L4 Semantic Graph/Log/Graph Log {дата}.md`

### 0b. Контекст рабочей папки

```
10 — Claude/Рабочее пространство/{команда}/ существует + запрос на доработку → частичный перезапуск
10 — Claude/Рабочее пространство/{команда}/ существует + новая задача → архивировать в _prev/, начать заново
10 — Claude/Рабочее пространство/{команда}/ нет → первый запуск
```

### 0c. Загрузка контекста агентов

Читай контекст задействованных агентов **автоматически** (не спрашивай пользователя):
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"{agent-name}"}'
```

Читай последнюю сессию активной команды:
```bash
VAULT="/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
SESSIONS="$VAULT/10 — Claude/Контекст и Сессии/{команда}"
ls "$SESSIONS/" 2>/dev/null | sort | tail -1
# прочитать найденный файл
```

---

## Протокол сессий (ОБЯЗАТЕЛЬНО)

### После завершения любой сессии — сохранить через sync-claude-sessions

```bash
VAULT="/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
VAULT_DIR="$VAULT" \
CLAUDE_SESSIONS_OUTPUT="$VAULT/10 — Claude/Контекст и Сессии/{Команда}" \
  python3 ~/.claude/skills/sync-claude-sessions/scripts/claude-sessions export \
  "$(ls -t ~/.claude/projects/-Users-dimitrisimonyan/*.jsonl | head -1)"
```

Команды → папки:
- Personal OS → `10 — Claude/Контекст и Сессии/Personal OS`
- Psych Team → `10 — Claude/Контекст и Сессии/Psych Team`
- Startup Lab → `10 — Claude/Контекст и Сессии/Startup Lab`
- Dev Lab → `10 — Claude/Контекст и Сессии/Dev Lab`

---

## Персонажи (режимы ответа)

Если запрос начинается с имени персонажа — передать ему напрямую:

```
mentor <вопрос>   → Agent(persona-mentor)
coach <вопрос>    → Agent(persona-coach)
friend <вопрос>   → Agent(persona-friend)
stoic <вопрос>    → Agent(persona-stoic)
critic <вопрос>   → Agent(persona-critic)
```

| Персонаж | Когда использовать |
|----------|-------------------|
| **mentor** | Нужен прямой совет от опытного человека |
| **coach** | Хочу найти ответ сам, нужны вопросы |
| **friend** | Хочу поговорить по-человечески, без структуры |
| **stoic** | Нужна суть без воды, коротко |
| **critic** | Хочу чтобы мои идеи/отмазки проверили |

Персонаж читает L3 Persona перед ответом — знает контекст Димитри.

---

## Phase 1: Роутинг задачи

### Матрица решений

```
Запрос содержит              → Команда
─────────────────────────────────────────
Obsidian / Zotero / hh.ru    → Personal OS
Telegram                     → Personal OS
психология / самоанализ      → Psych Team
продукт / рынок / GTM        → Startup Lab
контент / посты / SMM        → Startup Lab
HR / найм / резюме           → Startup Lab (hr-specialist)
разработка / код / API       → Dev Lab
дизайн / UI / UX / wireframe → Dev Lab
дизайн-система               → Dev Lab (ui-designer-web/mobile)
редизайн / «нормальный ui»   → Dev Lab (design-lead — делает и дизайн, и код, и проверку)
критика экрана / дизайн-ревью → Dev Lab (design-reviewer, база design-core)
документация / ТЗ            → Dev Lab (tech-writer)
ревью кода                   → Dev Lab (code-reviewer)
рисёрч / NotebookLM          → Research Lab
научная статья / реферат ГОСТ → Research Lab (academic-writer)
анализ источников / книги    → Research Lab (deep-researcher)
скрытые связи / паттерны     → Research Lab (pattern-finder)
генерация идей / гипотезы    → Research Lab (idea-generator)
объясни простыми словами     → Research Lab (simplifier)
документ / отчёт / договор   → Office Bureau
графики / диаграммы          → Office Bureau (data-visualizer)
презентация / оформление     → Office Bureau (presentation-designer)
делопроизводство / ГОСТ-док  → Office Bureau (document-writer)
обучение / научи / выучить   → Tutor Lab
программа курса / план учёбы → Tutor Lab (curriculum-architect)
лекции / конспект по теме    → Tutor Lab (lecturer)
задания / упражнения учебные → Tutor Lab (exercise-designer)
тест / проверка знаний / экз → Tutor Lab (assessment-designer)
```

> Различение: «исследуй тему» (понять/собрать знание для себя) → Research Lab; «научи теме / составь курс» (освоить через структурированную программу с заданиями и проверкой) → Tutor Lab. Tutor Lab может запросить Research Lab для глубины лекций и Office Bureau для оформления курса.

> Различение: «статья по ГОСТ» (научный текст) → Research Lab (academic-writer); «оформить документ/отчёт по ГОСТ» (деловой документ, .docx/.pptx) → Office Bureau. Научный текст из Research Lab можно передать в Office Bureau для финального оформления в .docx/PDF/презентацию.

### Кросс-командные запросы

Если задача требует нескольких команд:

```
PRD → разработка:    Startup Lab (product-manager) → Dev Lab
UX + анализ пользователей:  Psych Team → Startup Lab → Dev Lab
Контент + психология стиля:  Psych Team → Startup Lab (smm)
HR + бизнес-психология:      Psych Team → Startup Lab (hr-specialist)
```

Протокол: `.claude/skills/agent-context/references/cross-team.md`

---

## Phase 2: Запуск Agent Team

Каждая команда работает как **Agent Team** — группа агентов которые координируются через TaskCreate + SendMessage + общие файлы.

### Паттерн запуска

```
1. Создать TaskList для команды (TaskCreate для каждой задачи с зависимостями)
2. Запустить агентов с run_in_background: true для параллельных задач
3. Передать каждому агенту:
   - Путь к его .md файлу определения
   - Контекст предыдущей сессии (если загружен)
   - Профиль Димитри из Obsidian (если релевантно)
   - Задачу и зависимости
4. Агенты синхронизируются через 10 — Claude/Рабочее пространство/ файлы
5. Оркестратор синтезирует результаты
```

### Шаблон prompt для агента

```
Прочитай своё определение: .claude/agents/{name}.md

[Если есть контекст:] Контекст предыдущей сессии:
{содержимое из Obsidian 10 — Claude/Контекст и Сессии/{team}/{name}}

[Если нужен профиль:] Профиль Димитри из Obsidian:
{содержимое релевантных заметок}

Задача: {описание}
Входные файлы: {пути}
Выходные файлы: {пути}
Зависимости: {от кого ждать}
Координация: {с кем общаться через файлы}
```

---

## Системные команды — память L0→L4

Обслуживают 5-уровневую L-память. **L4 (семантический граф)** — отдельный ассоциативный слой памяти: `brainstorm` обновляет только L4, `update-memory` прогоняет всю модель `L0→L1→L2→L3→L4` (memory-extractor пишет L1→L4). Не запускают командных агентов, работают напрямую с vsearch и vault/memory-extractor. Путь инструмента:
`~/Desktop/personal os/tools/obsidian/tools/obsidian_vsearch.py`
Лог L4: индекс `Semantic Graph Log.md` + файлы-дни `L4 Semantic Graph/Log/Graph Log {дата}.md` (писать в файл-день, индекс не читать целиком)

> Обе вызываются и **отдельными командами**: `/brainstorm` и `/update-memory` (самостоятельные скиллы с полным протоколом). Ниже — краткая версия для оркестратора.

### 🔗 brainstorm — L4-память (семантический граф)

**Триггеры:** `brainstorm`, `найди связи`, `скрытые связи`, `обнови граф связей`, `что пересекается с {тема}`

Цель: найти неочевидные пересечения между разными разделами базы и обновить L4-память (семантический граф).

```
Шаг 1 — Свежесть индекса (быстро):
  python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py stats
  find "$VAULT" -name "*.md" | wc -l   # сравнить indexed_files с реальным
  # если разница > 0 → python3 …/obsidian_vsearch.py index

Шаг 2 — Поиск скрытых связей:
  # по теме (если задана):
  python3 …/obsidian_vsearch.py brainstorm "{тема}" 20 0.70
  # по всей базе (если темы нет):
  python3 …/obsidian_vsearch.py brainstorm "" 25 0.72
  # сигнатура: brainstorm "query" limit threshold

Шаг 3 — Перестройка графа:
  python3 …/obsidian_vsearch.py build_graph 0.55 8
  # сигнатура: build_graph threshold top_k

Шаг 4 — Сохранить находки в лог L4:
  # связи со score > 0.75 между РАЗНЫМИ разделами → в ФАЙЛ-ДЕНЬ лога L4 (создать если нет)
  node ~/.claude/skills/obsidian/driver.mjs obsidian_append \
    '{"path":"10 — Claude/Memory/L4 Semantic Graph/Log/Graph Log {дата}.md","content":"## {дата} (brainstorm)\n- [[note1]] ↔ [[note2]] (раздел1 ↔ раздел2, score)\n..."}'
  # + ссылка "- [[Graph Log {дата}]]" в индекс Semantic Graph Log.md (если ещё нет)
```

**Отчёт:** `brainstorm: {K} новых связей · топ-мосты: {раздел↔раздел} · L4-граф обновлён · {N} записано в L4 Semantic Graph Log`

Можно делегировать `Agent(vault-indexer)` — он выполняет Шаги 1–4 автономно (модель haiku, быстро).

### 🧠 update-memory — полная L-память L0→L4

**Триггеры:** `update-memory`, `обнови память`, `обнови L-память`, `проиндексируй заметки`, `актуализируй персону`, `обнови L4`

Цель: прогнать полный цикл 5-уровневой памяти `L0→L1→L2→L3→L4` (линейные слои + L4 семантический граф).

```
Шаг 0 — Экспорт свежих сессий в L0 (ОБЯЗАТЕЛЬНО с cd в vault — скрипт пишет относительно CWD):
  (cd "$VAULT" && python3 ~/.claude/skills/sync-claude-sessions/scripts/claude-sessions export --today)

Шаг 0.5 — Telegram: если пользователь упоминает конкретный чат/диалог — найти его сначала в базе
  фермы (db_chats), затем полностью экспортировать историю вместе с медиа одной командой export_chat:
  node ~/.claude/skills/telegram/driver.cjs export_chat
    '{"chat_id":"<id>","out_dir":"$VAULT/11 — Архив/Телеграм чаты/{чат}"}'
  → {YYYY-MM-DD} export.md + media/ (фото, голосовые, кружки, видео, стикеры)
  Экспорт не просто складывается в архив: memory-extractor разбирает его как источник [SOCIAL] —
  как Димитри общается с этим человеком (регистр, дистанция, просьбы/отказы/извинения, подстройка
  под собеседника) → рубрика «Как общается с людьми» в файле-дне идиолекта, с цитатами.

Шаг 1 — Переиндексация заметок для графа:
  python3 ~/Desktop/personal\ os/tools/obsidian/tools/obsidian_vsearch.py index

Шаг 2 — L-память L1→L4 (агент memory-extractor — сам пишет и L4):
  Agent(memory-extractor): обработай новые сессии из L0 →
    извлеки атомарные факты L1 (10 — Claude/Memory/L1 Atomic Memory/{YYYY-MM-DD}.md),
    обнови сцены L2 (файл-день L2 Scene Blocks/{сцена}/{сцена} {дата}.md + ссылка в ядро L2 {сцена}.md),
    актуализируй персону L3 (10 — Claude/Memory/L3 Persona/persona.md),
    пополни Журнал архитектурных решений из dev-сессий
      (06 — Проекты/Журнал архитектурных решений/{YYYY-MM-DD} — {суть}.md,
       type: adr, теги, тело строго сплошным текстом — правила в MOC журнала и в скилле update-memory),
    перестрой граф и лог L4 (файл-день L4 Semantic Graph/Log/Graph Log {дата}.md + ссылка в индекс)

Шаг 3 (резерв, если memory-extractor пропущен) — перестройка графа L4:
  python3 …/obsidian_vsearch.py build_graph 0.55 8

Шаг 4 (резерв) — новые скрытые связи на свежем индексе:
  python3 …/obsidian_vsearch.py brainstorm "" 20 0.72
  # значимые (score > 0.75) → в файл-день 10 — Claude/Memory/L4 Semantic Graph/Log/Graph Log {дата}.md
```

**Отчёт:** `update-memory: L0 +{S} сессий · tg-экспорт {T} чатов · индекс +{delta} · memory-extractor: {N} сессий → {K} фактов → {M} сцен → L3 → ADR +{A} → L4 · граф обновлён · {J} новых связей`

**Порядок при «обнови всё»:** сначала `update-memory` (наполняет память и индекс), затем `brainstorm` уже отработает Шагами 3–4 внутри — отдельно повторять не нужно.

---

## Decision Council — Совет принятия решений

**Триггеры:** "посоветуй", "как решить", "что делать с...", "помоги принять решение"

```
Agent(decision-council): {вопрос + контекст решения}
```

**6 персонажей параллельно:**

| Персонаж | Агент | Линза |
|----------|-------|-------|
| Аналитик | char-analyst | Данные, факты, логика |
| Критик | char-critic | Риски, слабые места, пре-мортем |
| Стратег | char-strategist | Долгосрок, система, позиция |
| Интуит | char-intuitor | Эмоции, ощущения, ценности |
| Прагматик | char-pragmatist | Исполнение, MVP, следующий шаг |
| Мечтатель | char-visionary | Возможности, лучший сценарий |

**Быстрый режим** (3 персонажа по типу решения):
- Личное/жизненное → Интуит + Стратег + Мечтатель
- Стартап/продукт → Аналитик + Критик + Прагматик
- Техническое → Аналитик + Критик + Прагматик
- Жизненный выбор → Интуит + Критик + Стратег

**Результат:** синтез с рекомендацией, уверенностью, следующим шагом — сохраняется в Obsidian.

---

## Workflows по командам

### 🏠 Personal OS — база знаний и действия

**Режим:** Sub-agents (Sequential/Parallel)

```
Тип задачи              → Агенты
─────────────────────────────────
Чтение/поиск Obsidian   → knowledge-agent
Запись в Obsidian       → knowledge-agent
Telegram (чтение)       → action-agent
Telegram + Obsidian     → оба параллельно
hh.ru поиск/отклик      → action-agent
Утренний брифинг        → оба параллельно → синтез
```

### 🧠 Psych Team — психологический анализ

**Режим:** Fan-out (сбор данных) → Fan-out (параллельный анализ) → Sequential (синтез)

```
Phase 1: [ПАРАЛЛЕЛЬНО] knowledge-agent (Obsidian) + action-agent (Telegram)
Phase 2: [ПАРАЛЛЕЛЬНО] cognitive + emotional + relational analysts
Phase 3: [SEQUENTIAL]  lead-psychologist синтезирует финальный отчёт
```

Оркестратор psych-analysis: `.claude/skills/psych-analysis/SKILL.md`

### 🚀 Startup Lab — стартап и продукт

**Режим:** Agent Team (Pipeline + Fan-out/Fan-in)

```
Phase 1: market-researcher (research + NotebookLM)
Phase 2: product-manager (PRD) + growth-marketer (ICP/GTM) [параллельно если нет зависимости]
Phase 3: smm-specialist (контент) / hr-specialist (найм) / business-psychologist (трекинг)
Phase 4: knowledge-agent сохраняет в Obsidian
```

Оркестратор startup-lab: `.claude/skills/startup-lab/SKILL.md`

### 💻 Dev Lab — разработка и дизайн

**Режим:** Agent Team (Pipeline → Fan-out → Fan-out → Fan-in)

```
Phase 1: ux-researcher + ux-architect [Sequential]
Phase 2: tech-lead архитектура
Phase 3: ui-designer-web + ui-designer-mobile + frontend + backend [Fan-out параллельно]
Phase 4: code-reviewer + design-reviewer [Fan-out параллельно]
Phase 5: tech-lead синтез + tech-writer документация
```

Оркестратор dev-lab: `.claude/skills/dev-lab/SKILL.md`
Дизайн-база команды: `.claude/skills/design-core/` — режим поверхности, три диала,
законы, анти-слоп, критика по 7 линзам, спецификации, движение, стилевые карточки.
Одиночная дизайн-задача (без спринта) идёт напрямую в агента `design-lead`.

---

## Phase 3: Кросс-командная координация

Когда задача требует нескольких команд:

```
1. Определить главную команду (владелец результата)
2. Определить поддерживающие команды (дают данные/артефакты)
3. Запустить поддерживающие команды первыми
4. Передать их результаты главной команде
5. Главная команда завершает работу
```

**Пример: Полный продуктовый цикл**
```
1. Psych Team → психологический профиль пользователей (если нужен)
2. Startup Lab → PRD + позиционирование + GTM
3. Dev Lab → UX/UI + разработка
4. Personal OS → сохранить всё в Obsidian
```

**Пример: HR + Психология**
```
1. Psych Team → портрет Димитри как предпринимателя
2. Startup Lab (business-psychologist + hr-specialist) → карьерная стратегия
```

---

## Phase 4: Самооптимизация системы

Если в ходе работы агент предлагает улучшение:

1. Оркестратор сообщает пользователю предложение агента
2. Ждёт явного ответа (да/нет)
3. При одобрении: агент обновляет свой `.claude/agents/{name}.md` через Edit tool
4. Оркестратор записывает изменение в CLAUDE.md (лог изменений)

Протокол: `.claude/skills/agent-context/references/self-optimization.md`

---

## Запуск агентов

```
subagent_type: "general-purpose"
model: "opus"
run_in_background: true  ← для параллельных задач
```

---

## Обработка ошибок

- Команда недоступна → работай с доступными, отметь что именно пропущено
- Obsidian недоступен → работай без контекста, предупреди пользователя
- Агент не завершил → 1 повтор, затем продолжить без него
- Кросс-командный запрос завис → таймаут 5 мин, продолжить без данных из другой команды

---

## Тестовые сценарии

**Полный цикл:** «Создай стартап-продукт для студентов» → Startup Lab (research → PRD → GTM → посты) → Dev Lab (UX → дизайн-система → фронтенд) → Personal OS (Obsidian)

**Кросс-командный:** «Проанализируй меня как предпринимателя и скажи кого нанять» → Psych Team → Startup Lab (business-psychologist + hr-specialist)

**Простой:** «Найди мои заметки о продуктивности» → Personal OS (knowledge-agent)

## Бюджет контекста

Скилл — диспетчер: раздаёт задания агентам и принимает отчёты. Сам артефакты
агентов не читает и не «проверяет глазами» — проверка это ещё один агент.
Потолки: сессия 55% окна мягкий / 88% жёсткий, субагент 40k / 60k токенов.
Промежуточное — файлом в рабочее пространство команды, в диалог путь и суть.
Фича доведена до конца — чекпоинт и дальше с чистой сессии:

```bash
python3 .claude/hooks/checkpoint.py --note "<что сделано>" --next "<следующий шаг>"
```

Подробности — скилл `context-budget`.
