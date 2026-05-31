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

---

## Phase 0: Контекст и загрузка знаний

### 0a. База знаний Obsidian

Перед запуском любой команды прочитай релевантный контекст:

```bash
# Контекст агентов которые будут задействованы
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"{agent-name}"}'

# Профиль Димитри если задача личная
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"профиль", "limit":5}'

# Тема задачи — не повторяй уже сделанное
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}", "limit":10}'
```

### 0b. Контекст рабочей папки

```
_workspace/{команда}/ существует + запрос на доработку → частичный перезапуск
_workspace/{команда}/ существует + новая задача → архивировать в _prev/, начать заново
_workspace/{команда}/ нет → первый запуск
```

### 0c. Загрузка контекста агентов

Спроси пользователя: **«Загрузить контекст предыдущей сессии? (да/нет)»**

Если да — читай контекст задействованных агентов из Obsidian (`10 — Claude/Контекст и Сессии/{команда}/{агент}`).

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
документация / ТЗ            → Dev Lab (tech-writer)
ревью кода                   → Dev Lab (code-reviewer)
```

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
4. Агенты синхронизируются через _workspace/ файлы
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
