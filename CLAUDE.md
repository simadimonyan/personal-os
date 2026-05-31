# Personal OS — Система агентов Димитри

## Как использовать

**Один скилл для всего:** используй скилл `personal-os` для любой задачи.

Мастер-оркестратор определит команду, загрузит контекст из Obsidian и запустит агентов.

```
Примеры:
"проанализируй меня психологически"       → Psych Team
"придумай продукт для студентов"           → Startup Lab
"создай UI для мобильного приложения"      → Dev Lab
"найди мои заметки о продуктивности"       → Personal OS
"создай продукт и разработай его"          → Startup Lab + Dev Lab (кросс-командно)
"кого мне нанять первым?"                  → Startup Lab (hr-specialist)
"напиши сопроводительное письмо"           → Startup Lab (hr-specialist + hh.ru)
```

---

## Архитектура

```
personal-os (мастер-оркестратор)
│
├── Personal OS Team
│   ├── knowledge-agent  ─ Obsidian (Органон) + Zotero
│   └── action-agent     ─ Telegram + hh.ru
│
├── Psych Team
│   ├── cognitive-analyst    ─ КПТ, убеждения, мышление
│   ├── emotional-analyst    ─ эмоции, триггеры, саморегуляция
│   ├── relational-analyst   ─ отношения, стиль привязанности
│   ├── social-psychologist  ─ социальная идентичность, групповая динамика, страх изгнания
│   ├── blind-spot-analyst   ─ самообман, белые пятна (batch + интерактивный диалог)
│   ├── sexologist           ─ сексуальные паттерны, динамика власти, связь с психологией
│   ├── prognosis-analyst    ─ карта сценариев, маршруты трансформации, NotebookLM
│   └── lead-psychologist    ─ синтез, финальный портрет
│
├── Startup Lab
│   ├── market-researcher    ─ рынок, конкуренты, NotebookLM (10–50 источников)
│   ├── product-manager      ─ PRD, roadmap, user stories
│   ├── growth-marketer      ─ ICP, позиционирование, GTM
│   ├── smm-specialist       ─ контент, посты в стиле Димитри
│   ├── business-psychologist─ перфоманс предпринимателя, блоки
│   └── hr-specialist        ─ найм, культура + резюме, сопроводительные, hh.ru
│
└── Dev Lab
    ├── tech-lead            ─ архитектура, tech decisions, финальное ревью
    ├── frontend-developer   ─ React/Next.js/TypeScript
    ├── backend-developer    ─ Node.js/Python APIs, БД
    ├── ui-designer-web      ─ Web UI + дизайн-система
    ├── ui-designer-mobile   ─ Mobile UI + mobile дизайн-система (тёмная тема)
    ├── ux-researcher        ─ пользовательские исследования, персоны, JTBD
    ├── ux-architect         ─ IA, user flows, wireframes
    ├── code-reviewer        ─ качество кода, OWASP, безопасность
    ├── design-reviewer      ─ WCAG accessibility, консистентность
    └── tech-writer          ─ README, ТЗ, API docs, user stories
```

**Итого: 26 агентов в 4 командах**

---

## Рабочее пространство агентов — в Obsidian

**ВСЁ рабочее пространство команд находится в Obsidian** (`10 — Claude/`):

```
Vault = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон

Рабочее пространство:
  10 — Claude/Рабочее пространство/psych/       ← Psych Team
  10 — Claude/Рабочее пространство/startup/     ← Startup Lab
  10 — Claude/Рабочее пространство/dev/         ← Dev Lab
  10 — Claude/Рабочее пространство/personal-os/ ← Personal OS

Сессии (вербатим под копирку):
  10 — Claude/Пользовательские сессии/Psych Team/
  10 — Claude/Пользовательские сессии/Startup Lab/
  10 — Claude/Пользовательские сессии/Dev Lab/
  10 — Claude/Пользовательские сессии/Personal OS/

Контексты агентов (между сессиями):
  10 — Claude/Контекст и Сессии/{команда}/{агент}
```

Глобальные пути: `.claude/skills/agent-context/references/workspace-paths.md`

**Протокол сессий:**
- До начала: читать последнюю сессию из `Пользовательские сессии/{команда}/`
- После завершения: сохранить полный вербатим диалог туда же (не summary!)

---

## Obsidian — единая база знаний

Все агенты читают Obsidian перед работой и обновляют его по итогам.

| Раздел | Что там | Кто читает |
|--------|---------|-----------|
| 01 — Личность | Ценности, самооценка | Psych Team |
| 02 — Внутренний мир | Дневник, чувства | Psych Team |
| 03 — Идеи и мысли | Идеи, закономерности | Startup Lab |
| 04 — Цели и задачи | Цели, стратегия | HR, Startup Lab |
| 05 — Знания и CS | Технические знания | Dev Lab |
| 06 — Проекты | Активные проекты | все команды |
| 10 — Claude | Рабочее пространство, сессии, контексты | все агенты |

**Контексты сессий** (`10 — Claude/Контекст и Сессии/`):
- `Personal OS/` — knowledge-agent, action-agent
- `Psych Team/` — 4 психологических агента
- `Startup Lab/` — 6 стартап-агентов
- `Dev Lab/` — 10 dev-агентов

Протокол доступа: `.claude/skills/agent-context/references/obsidian-kb.md`

---

## Режим Agent Teams

Все команды работают как **Agent Teams**:
- TaskCreate с зависимостями для координации
- `run_in_background: true` для параллельных задач
- Общие файлы в `_workspace/` как канал общения
- Аналитики обмениваются гипотезами перед синтезом (Psych Team)

---

## Кросс-командное взаимодействие

| Запрос | От | К |
|--------|----|----|
| Психопрофиль аудитории | Startup Lab | Psych Team |
| PRD для разработки | Dev Lab | Startup Lab |
| Obsidian / Telegram | любая команда | Personal OS |
| Полный цикл идея→продукт | — | Startup Lab + Dev Lab |

Протокол: `.claude/skills/agent-context/references/cross-team.md`

---

## Самооптимизация агентов

Агенты предлагают улучшения самим себе:

```
🔧 Предложение по улучшению [agent-name]:
Обнаружил: {пробел}  Предлагаю: {изменение}  Разрешаете?
```

При одобрении — агент обновляет свой `.claude/agents/{name}.md`.
Протокол: `.claude/skills/agent-context/references/self-optimization.md`

---

## Скиллы

| Скилл | Назначение |
|-------|-----------|
| **`personal-os`** | **Единая точка входа — использовать для всего** |
| `startup-lab` | Прямой доступ к стартап-команде |
| `psych-analysis` | Прямой доступ к команде психологов |
| `dev-lab` | Прямой доступ к команде разработки |
| `agent-context` | Контексты, Obsidian KB, самооптимизация, кросс-команды |
| `sync-claude-sessions` | Экспорт сессий в Obsidian |
| `harness` | Изменение и расширение системы |

---

## Глобальные drivers

- `~/.claude/skills/obsidian/driver.mjs` — Obsidian
- `~/.claude/skills/zotero/driver.mjs` — Zotero
- `~/.claude/skills/hh/driver.mjs` — hh.ru
- `~/.claude/skills/telegram/driver.cjs` — Telegram
- `~/.claude/skills/notebooklm/SKILL.md` — NotebookLM

---

## История изменений

| Дата | Изменение | Объект | Причина |
|------|-----------|--------|---------|
| 2026-05-31 | Начальная сборка | Весь харнес | Первичная настройка |
| 2026-05-31 | Psych Team | agents/cognitive,emotional,relational-analyst, lead-psychologist, skills/psych-analysis/ | Психологический анализ |
| 2026-05-31 | Startup Lab | agents/market-researcher, product-manager, growth-marketer, smm-specialist, business-psychologist, skills/startup-lab/ | Продукты, маркетинг, трекинг |
| 2026-05-31 | NotebookLM везде | все agents/*.md, skills/*/SKILL.md | 10–50 источников, ✅/⚠️/❓ |
| 2026-05-31 | Система контекстов | skills/sync-claude-sessions/, skills/agent-context/ | Контексты агентов в Obsidian |
| 2026-05-31 | hr-specialist | agents/hr-specialist.md | Личный HR + HR стартапа + hh.ru |
| 2026-05-31 | Dev Lab (10 агентов) | agents/tech-lead,frontend,backend,ui-web,ui-mobile,ux-researcher,ux-architect,code-reviewer,design-reviewer,tech-writer, skills/dev-lab/ | Полный цикл разработки |
| 2026-05-31 | Agent Teams + самооптимизация + мастер-оркестратор | все файлы | Agent Teams, Obsidian как общая KB, самооптимизация, кросс-команды, единый personal-os |
| 2026-05-31 | Psych Team расширение: social-psychologist, blind-spot-analyst (batch+interactive), sexologist, prognosis-analyst | agents/social-psychologist,blind-spot-analyst,sexologist,prognosis-analyst.md, skills/psych-analysis/SKILL.md | Социальная динамика, самообман (диалог), сексология, карта трансформации |
| 2026-05-31 | Протокол сессий: вербатим под копирку | skills/psych-analysis/SKILL.md, blind-spot-analyst | Сохранение полных диалогов без summary |
| 2026-05-31 | Workspace → Obsidian | все SKILL.md, CLAUDE.md, workspace-paths.md | Рабочее пространство всех команд теперь в Obsidian 10 — Claude/Рабочее пространство/ |
| 2026-05-31 | Авто-загрузка контекста | psych/startup/dev-lab SKILL.md | Убран вопрос "загрузить контекст?" — теперь автоматически |
| 2026-05-31 | lead-psychologist оптимизация | agents/lead-psychologist.md | Читает гипотезы-файлы первыми для экономии контекста |
