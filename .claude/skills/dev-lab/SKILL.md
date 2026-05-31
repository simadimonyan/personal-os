---
name: dev-lab
description: "Команда разработки и дизайна для создания продуктов. Запускай когда нужно: спроектировать UX/UI, написать код (фронтенд/бэкенд), провести ревью кода или дизайна, создать документацию или ТЗ, разработать дизайн-систему. Триггеры: 'разработай продукт', 'создай UI', 'напиши код', 'сделай дизайн', 'создай дизайн-систему', 'напиши ТЗ', 'сделай документацию', 'проведи ревью кода', 'проведи ревью дизайна', 'UX исследование', 'wireframes', 'архитектура', 'фронтенд', 'бэкенд', 'dev-lab', 'команда разработки', 'полный спринт', 'обнови', 'доделай', 'продолжи', 'исправь по ревью'."
---

# Dev Lab — Agent Team разработки и дизайна

Координирую полный цикл создания продукта как связная **Agent Team**: исследование → UX → дизайн-система + UI → разработка → ревью → документация.

**Кросс-командное взаимодействие:**
- Принимает: PRD от startup-lab, психологический профиль от psych-team
- Передаёт: UI-спецификации и документацию обратно в startup-lab (если нужно)
- Протокол: `.claude/skills/agent-context/references/cross-team.md`

**Agent Team:**
- `tech-lead` — архитектура, технические решения, финальный tech review
- `frontend-developer` — React/Next.js/TypeScript, реализация интерфейсов
- `backend-developer` — Node.js/Python APIs, БД, серверная логика
- `ui-designer-web` — Web UI + **дизайн-система (web)**
- `ui-designer-mobile` — Mobile UI + **дизайн-система (mobile)**
- `ux-researcher` — пользовательские исследования, персоны, JTBD
- `ux-architect` — IA, user flows, wireframes
- `code-reviewer` — качество кода, безопасность, производительность
- `design-reviewer` — консистентность дизайна, accessibility, WCAG
- `tech-writer` — документация, ТЗ, API docs, user stories, README

**Вспомогательные агенты (из основного харнеса):**
- `knowledge-agent` — сохранение артефактов в Obsidian
- `market-researcher` — рыночные данные для UX-контекста (если нужно)
- `product-manager` — PRD для старта разработки (если нужно)

**Режим выполнения:** Hybrid
- Phase 1 (UX): Sequential (research → architecture)
- Phase 2 (Дизайн + Разработка): Fan-out (параллельно)
- Phase 3 (Ревью): Fan-out (code + design параллельно)
- Phase 4 (Синтез): Sequential (tech-lead + документация)

---

## Phase 0: Проверка контекста

### 0a. Контекст рабочей папки
При запуске определить режим:
- `_workspace/dev/` существует + запрос на доработку → **частичный перезапуск** нужного агента
- `_workspace/dev/` существует + новый проект → переименовать в `_workspace/dev_prev/`, начать заново
- `_workspace/dev/` нет → **первый запуск**, создать структуру

Создать структуру если нет:
```
_workspace/dev/
├── brief.md
├── architecture.md
├── tech-brief.md
├── ux/
│   ├── research/
│   ├── flows/
│   └── wireframes/
├── ui/
│   ├── web/
│   └── mobile/
├── code/
│   ├── frontend/
│   └── backend/
├── docs/
└── reviews/
```

### 0b. Загрузка контекста агентов из Obsidian

Спроси пользователя: **«Загрузить контекст предыдущей сессии Dev Lab? (да/нет)»**

Если **да** — читай контекст задействованных агентов:
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"tech-lead"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"frontend-developer"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"backend-developer"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"ui-designer-web"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"ui-designer-mobile"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"ux-researcher"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"ux-architect"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"code-reviewer"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"design-reviewer"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"tech-writer"}'
```
Читай только агентов, которые будут задействованы в этой сессии. Найденный контекст включай в prompt агента (секция "Контекст предыдущей сессии").

Полный протокол: `.claude/skills/agent-context/SKILL.md`

---

## Phase 1: Роутинг по типу задачи

| Тип задачи | Агенты | Режим |
|-----------|--------|-------|
| Полный спринт (идея → продукт) | все 10 последовательно-параллельно | Pipeline+Fan-out |
| UX спринт (только исследование + flows) | ux-researcher → ux-architect | Sequential |
| Дизайн спринт (UX готов) | ui-designer-web + ui-designer-mobile параллельно | Fan-out |
| Разработка по макетам | tech-lead → frontend + backend параллельно | Fan-out |
| Только документация/ТЗ | tech-writer (± knowledge-agent) | Sub-agent |
| Ревью кода | code-reviewer | Sub-agent |
| Ревью дизайна | design-reviewer | Sub-agent |
| Полное ревью | code-reviewer + design-reviewer параллельно → tech-lead | Fan-out+Fan-in |

---

## Workflow-шаблоны

### 🚀 Полный продуктовый спринт (Agent Team)

**Режим: Agent Team — Pipeline → Fan-out → Fan-out → Fan-in**

```
1. Зафиксировать brief в _workspace/dev/brief.md

   Проверить кросс-командные артефакты:
   - _workspace/dev/cross-team/ (PRD от startup-lab, психопрофиль от psych-team)
   - Если нет PRD → запросить у мастер-оркестратора startup-lab → product-manager

2. Загрузить из Obsidian:
   - Профиль Димитри: obsidian_search({"query":"профиль проект", "section":"06"})
   - Контексты агентов (раздел 10) для задействованных агентов

3. [TaskCreate] Задачи Agent Team:
   T1: ux-researcher — исследование пользователей (независима)
   T2: ux-architect — flows + wireframes (зависит от T1)
   T3: tech-lead — архитектура (зависит от T2)
   T4: ui-designer-web — дизайн-система + UI (зависит от T2, параллельно T3)
   T5: ui-designer-mobile — mobile дизайн-система (зависит от T2, параллельно T4)
   T6: backend-developer — API + БД (зависит от T3)
   T7: frontend-developer — компоненты + страницы (зависит от T3, T4)
   T8: code-reviewer — ревью кода (зависит от T6, T7)
   T9: design-reviewer — ревью дизайна (зависит от T4, T5)
   T10: tech-lead — финальный ревью (зависит от T8, T9)
   T11: tech-writer — документация (зависит от T10)

4. [Agent Team выполняет задачи]
   Каждый агент: читает Obsidian → выполняет задачу → пишет артефакты → синхронизирует контекст

5. knowledge-agent: сохранить в Obsidian раздел 06 Проекты

6. Вывести пользователю сводку артефактов
```

### 🎨 Дизайн спринт (от wireframes до готового UI)

**Режим: Fan-out**

```
1. Проверить наличие wireframes в _workspace/dev/ux/wireframes/

2. [ПАРАЛЛЕЛЬНО]
   a. [ui-designer-web]: design-system.md → components.md → screens/
   b. [ui-designer-mobile]: mobile-design-system.md → mobile-components.md → screens/

3. [design-reviewer]: проверить оба результата

4. Вывести отчёт ревью и список правок
```

### 🔬 UX Спринт (исследование + архитектура)

**Режим: Sequential**

```
1. [ux-researcher]: персоны + JTBD + pain map (NotebookLM: 10–50 источников)
2. [ux-architect]: IA + flows + wireframes
3. Вывести пользователю результаты, уточнить что нужно передать дальше
```

### 💻 Разработка по готовым макетам

**Режим: Fan-out**

```
1. [tech-lead]: архитектура + tech-brief (если ещё нет)

2. [ПАРАЛЛЕЛЬНО]
   a. [backend-developer]: API эндпоинты, БД
   b. [frontend-developer]: компоненты, страницы (с mock если бэкенд не готов)

3. [code-reviewer]: ревью всего кода

4. [tech-lead]: финальный tech review

5. [tech-writer]: обновить API docs и README
```

### 📝 Документирование

**Режим: Sub-agent**

```
1. [tech-writer]:
   - Прочитать имеющиеся артефакты (_workspace/dev/)
   - Создать запрошенные документы (README / ТЗ / API docs / user stories / changelog)
   - Уточнить у команды (через оркестратора) неясные места

2. knowledge-agent: сохранить документы в Obsidian
```

### 🔍 Ревью (код + дизайн)

**Режим: Fan-out + Fan-in**

```
1. [ПАРАЛЛЕЛЬНО]
   a. [code-reviewer]: code_review.md
   b. [design-reviewer]: design_review.md

2. [tech-lead]: синтез обоих ревью → приоритизированный список правок

3. Вывести пользователю сводный отчёт
```

---

## NotebookLM в Dev Lab

**Все агенты умеют исследовать через NotebookLM.** При постановке задачи явно укажи агенту: «Используй NotebookLM для research, загрузи 10–50 источников по теме».

Особенно важно для:
- `ux-researcher`: аудиторные исследования, behavioral patterns — загружай максимум источников
- `tech-lead`: архитектурные паттерны, сравнение технологий
- `code-reviewer`: OWASP, security guidelines
- `tech-writer`: стандарты документации, examples

---

## Запуск агентов

Используй `Agent` tool:
- `subagent_type: "general-purpose"` для всех
- `model: "opus"` обязательно
- Для параллельного выполнения: `run_in_background: true`

В prompt включай:
1. Путь к файлу агента: `.claude/agents/{name}.md` — попроси прочитать
2. Конкретные пути к входным/выходным файлам
3. Ожидаемый формат результата
4. Контекст предыдущей сессии (если загружен)

---

## Обработка ошибок

- Нет brief → попроси пользователя: что строим, для кого, веб или мобайл, какая главная задача
- NotebookLM недоступен → ux-researcher и tech-lead работают без него, явно отмечают ограничение
- Дизайн не готов к разработке → фронтенд начинает с mock-данными и заглушками по wireframes
- Ревью блокирует → критические блокеры исправляются первыми, разработка не ждёт minor замечаний
- Агент не завершил → 1 повтор, затем продолжить без него, отметить в итоге

---

## Тестовые сценарии

**Нормальный:** «Создай SaaS-продукт для управления задачами» → brief → UX спринт → дизайн-системы → разработка → ревью → документация

**Ошибочный:** Нет данных об аудитории → ux-researcher создаёт research-based персоны с явными гипотезами → остальной пайплайн работает в обычном режиме
