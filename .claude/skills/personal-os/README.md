# Personal OS — Справочник команд

Полный список команд системы агентов Димитри. Каждая команда вызывается отдельно как `/{команда}` либо через мастер-оркестратор `/personal-os`.

**База:** Obsidian «Органон» · **Рабочее пространство всех команд:** `10 — Claude/Рабочее пространство/{команда}/` · **Память:** `10 — Claude/Memory/` (L1→L2→L3) · **Сессии:** `10 — Claude/Контекст и Сессии/{Команда}/`

---

## 🎛 Точка входа

| Команда | Что делает |
|---------|-----------|
| **`/personal-os`** | Мастер-оркестратор. Понимает любой запрос, выбирает команду(ы), координирует кросс-командную работу. Можно не выбирать команду вручную — он маршрутизирует сам. |

---

## 👥 Команды (Agent Teams) — каждая отдельным вызовом

| Команда | Рабочая папка | Агенты | Когда |
|---------|--------------|--------|-------|
| **`/psych-analysis`** | `…/psych/` | cognitive, emotional, relational, social, blind-spot, sexologist, prognosis, lead-psychologist | Психоанализ, портрет, паттерны, самоанализ |
| **`/startup-lab`** | `…/startup/` | market-researcher, product-manager, growth-marketer, smm, business-psychologist, hr-specialist | Продукт, рынок, GTM, контент, HR, бизнес-трекинг |
| **`/dev-lab`** | `…/dev/` | tech-lead, frontend, backend, ui-web, ui-mobile, ux-researcher, ux-architect, code-reviewer, design-reviewer, tech-writer | Разработка, дизайн-система, UX/UI, ТЗ, ревью |
| **`/research-lab`** | `…/research/` | research-scout, deep-researcher, pattern-finder, idea-generator, academic-writer, simplifier | Рисёрч, NotebookLM, статьи по ГОСТ, идеи, скрытые связи, простые объяснения |
| **`/office-bureau`** | `…/office/` | office-scout, document-writer, data-visualizer, presentation-designer | Документы, отчёты, договоры, графики, презентации по ГОСТ |
| **`/tutor-lab`** | `…/tutor/` | learning-needs-analyst, curriculum-architect, lecturer, exercise-designer, assessment-designer, learning-coach | Программа обучения, лекции, задания, тесты, учебный план |

«Личная» команда **Personal OS** (knowledge-agent + action-agent) запускается внутри `/personal-os` для Obsidian / Telegram / hh.ru. Рабочая папка `…/personal-os/`.

---

## ⚙️ Системные команды (граф и память) — отдельным вызовом

| Команда | Что делает |
|---------|-----------|
| **`/brainstorm`** | Обновляет **L4-память** (семантический граф): находит новые скрытые связи между разделами, перестраивает граф, пишет находки в файлы-дни `Memory/L4 Semantic Graph/Log/Graph Log {дата}.md` (индекс — `Semantic Graph Log.md`). Триггеры: «найди связи», «обнови граф», «обнови L4». |
| **`/update-memory`** | Полный цикл L-памяти L0→L1→L2→L3→L4 (memory-extractor) + переиндексация заметок + перестройка графа + brainstorm. Триггеры: «обнови память», «переиндексируй», «актуализируй персону». |

Оба также доступны внутри `/personal-os` (раздел «Системные команды»). Автоматически перед Obsidian-задачами отрабатывает фоновый агент **vault-indexer**.

---

## 🗣 Персонажи — режимы ответа (внутри `/personal-os`)

Вызов: `/personal-os {персонаж} <вопрос>`

| Персонаж | Когда |
|----------|-------|
| **mentor** | Прямой совет от опытного человека |
| **coach** | Хочу найти ответ сам — нужны вопросы |
| **friend** | По-человечески, без структуры |
| **stoic** | Суть без воды, коротко |
| **critic** | Проверить идеи/отмазки на прочность |

## 🧭 Decision Council — совет принятия решений (внутри `/personal-os`)

Триггеры: «посоветуй», «как решить», «что делать с…», «помоги принять решение».
6 линз параллельно: Аналитик, Критик, Стратег, Интуит, Прагматик, Мечтатель → синтез с рекомендацией.

---

## 🧰 Инструментальные скиллы (драйверы)

| Команда | Назначение |
|---------|-----------|
| **`/obsidian`** | База знаний «Органон»: поиск, чтение, создание заметок, дневник |
| **`/telegram`** | Telegram: сообщения, диалоги, история, группы |
| **`/hh`** | hh.ru: вакансии, отклики, резюме |
| **`/zotero`** | Библиотека Zotero: источники, метаданные, коллекции |
| **`/sync-claude-sessions`** | Экспорт сессий Claude Code в Obsidian (вербатим) |
| **`/tg-freelance-blast`** | Рассылка интро по фриланс-чатам Telegram с паузами |
| **`/notebooklm`** | Рисёрч через NotebookLM (10–300 источников) |

## 📐 Справочные скиллы (подключаются агентами автоматически)

| Скилл | Для кого |
|-------|----------|
| **`/gost-standards`** | academic-writer, document-writer — оформление по ГОСТ |
| **`/pedagogy`** | агенты Tutor Lab — инструкционный дизайн |
| **`/doc-design`** | presentation-designer, data-visualizer — визуал, .pptx, графики |
| **`/agent-context`** | контекст агентов между сессиями в Obsidian |
| **`/harness`** | мета-скилл: создание/расширение команд и агентов |

---

## 📂 Где всё лежит (Obsidian `10 — Claude/`)

```
Рабочее пространство/         ← рабочие файлы команд (НЕ _workspace на диске!)
  ├── psych/  startup/  dev/  research/  office/  tutor/  personal-os/
Memory/                       ← L-память агентов
  ├── L1 Atomic Memory/  L2 Scene Blocks/ (ядра + файлы-дни)  L3 Persona/ (ядро + модули)  L4 Semantic Graph/Log/ (файлы-дни)
Контекст и Сессии/{Команда}/  ← контексты агентов + вербатим сессий
```

**Семантический граф / vsearch:** `~/Desktop/personal os/tools/obsidian/tools/obsidian_vsearch.py` (команды: `index`, `stats`, `hybrid`, `brainstorm`, `build_graph`). Граф: `…/tools/obsidian/tools/graph/graph.html`.
