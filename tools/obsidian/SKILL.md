---
name: obsidian
description: "Работа с личной базой знаний Obsidian (Органон — 904 заметки). Используй когда просят найти заметку, прочитать, создать новую, написать в дневник, найти по тегу, показать последние заметки, выявить скрытые связи между заметками или построить семантический граф базы знаний."
---

# Obsidian — Органон (личная база знаний)

Хранилище: `/Yandex.Disk/Self-Education/Knowledge base/Obsidian/Органон`
Driver: `~/.claude/skills/obsidian/driver.mjs`

```bash
node ~/.claude/skills/obsidian/driver.mjs <tool> '<json_args>'
```

889 заметок. Структура PARA + LYT. Читай и пиши напрямую через файловую систему — Obsidian не нужен.

---

## Разделы базы

| # | Раздел | Содержимое |
|---|--------|-----------|
| 01 | Личность | Ценности, самооценка, самоанализ, границы, комплексы |
| 02 | Внутренний мир | Дневник, чувства, КПТ, поведение, отношения |
| 03 | Идеи и мысли | Идеи, мысли, закономерности, карточки дня |
| 04 | Цели и задачи | Цели, планирование, стратегия, саморазвитие |
| 05 | Знания и навыки | Computer Science, образование, аналитика |
| 06 | Проекты | Активные проекты |
| 07 | Жизнь | Данные, эпизоды, отношения, мероприятия |
| 08 | Шаблоны | Шаблоны заметок |
| 09 | Архив | История (535 заметок, только чтение) |
| 10 | Claude | Обсуждения с Claude |

---

## Инструменты

### obsidian_stats
Статистика всей базы.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_stats
```

### obsidian_recent
Последние изменённые заметки.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_recent '{"limit":10}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_recent '{"limit":5,"section":"Проекты"}'
```

### obsidian_search
Полнотекстовый поиск по всей базе. Возвращает путь, название и выдержку.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"агентная система","limit":10}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"цель","section":"04","limit":20}'
```

### obsidian_read
Прочитать заметку по названию (или точному пути).
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"Видение"}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"path":"04 — Цели и задачи/Стратегия/Видение.md"}'
```
Поиск по названию: сначала точное совпадение, потом частичное (case-insensitive).

### obsidian_list
Список заметок в разделе.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_list '{"section":"06","limit":20}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_list '{"section":"02 — Внутренний мир","recursive":false}'
```

### obsidian_find_by_tag
Найти все заметки с тегом. Ищет и в frontmatter, и в тексте.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_find_by_tag '{"tag":"идея","limit":30}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_find_by_tag '{"tag":"Самоанализ"}'
```
`tag` с `#` или без — оба варианта работают.

### obsidian_create
Создать новую заметку с frontmatter.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_create '{
  "title": "Новая идея о проекте",
  "section": "03",
  "subfolder": "Идеи",
  "tags": ["идея", "проект"],
  "content": "# Новая идея о проекте\n\nОписание..."
}'
```
`section` — номер (`"03"`) или часть названия (`"Идеи и мысли"`).

### obsidian_append
Дописать в конец существующей заметки.
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_append '{
  "name": "Видение",
  "content": "\n## Обновление 2026-05-30\n\nНовая мысль..."
}'
```

### obsidian_diary
Создать или дополнить дневниковую запись сегодняшнего дня в `02 — Внутренний мир/Дневник/`.
```bash
# Создаёт новую запись если её нет, иначе дописывает
node ~/.claude/skills/obsidian/driver.mjs obsidian_diary '{
  "content": "## Размышление\n\nСегодня думал о...",
  "mood": "reflective",
  "tags": ["дневник", "психология"]
}'
```

---

## Типичные workflow

**Найти все заметки о цели X:**
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"цель","limit":15}'
```

**Прочитать + дополнить заметку:**
```bash
# 1. Найти и прочитать
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"Система самоорганизации"}'
# 2. Дописать новые мысли
node ~/.claude/skills/obsidian/driver.mjs obsidian_append '{"name":"Система самоорганизации","content":"\n## 2026-05-30\n..."}'
```

**Создать заметку по итогам разговора:**
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_create '{
  "title": "Анализ разговора с X",
  "section": "02",
  "subfolder": "Дневник",
  "tags": ["дневник", "анализ"],
  "content": "# Анализ разговора с X\n\n..."
}'
```

**Написать в дневник:**
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_diary '{"content":"## Что произошло\n\n...","mood":"tired"}'
```

---

---

## Семантический поиск + Граф связей

Инструмент: `~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py`
База векторов: `tools/obsidian_vectors.db` (904 файла, 41k чанков)
Граф: `tools/graph/graph.json` + `tools/graph/graph.html`

### Когда использовать вместо obsidian_search

| Задача | Инструмент |
|--------|-----------|
| Найти заметку по точному слову | `obsidian_search` (driver.mjs) |
| Найти по смыслу / концепции | `hybrid` или `search` |
| Найти неожиданные связи между разделами | `brainstorm` |
| Получить контекст всей базы знаний | `graph.json` или `GRAPH_REPORT.md` |

---

### obsidian_vsearch_search
Семантический поиск — находит по смыслу.
```bash
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py search "паттерн избегания близости"
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py search "страх провала" 15
# аргументы: query [limit=10] [threshold=0.25]
```

### obsidian_vsearch_hybrid
Семантика + текст одновременно. **Использовать по умолчанию** для большинства запросов.
```bash
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py hybrid "тревога перед встречами"
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py hybrid "цели на год" 20
```

### obsidian_brainstorm
Выявляет скрытые связи между заметками из **разных разделов** — то что нейросеть видит, но ты явно не связывал.
Возвращает: список пар заметок с высоким семантическим сходством, мосты между разделами.
```bash
# Скрытые связи вокруг темы
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py brainstorm "прокрастинация"

# Все скрытые связи по всей базе (медленнее)
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py brainstorm "" 20 0.70

# аргументы: query [limit=15] [threshold=0.65]
```
Особенно полезно для Psych Team — находит как психологические паттерны перекликаются со стартап-решениями или дневниковыми записями.

### obsidian_build_graph
Строит полный семантический граф всей базы знаний.
```bash
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py build_graph
# аргументы: [threshold=0.55] [top_k=8]
# Выходные файлы: tools/graph/graph.json + tools/graph/graph.html
```
- `graph.html` — интерактивная визуализация в браузере (цвета по разделам, zoom, клик по узлу)
- `graph.json` — для агентов: узлы с разделом/сниппетом + рёбра с весом связи

### Чтение графа агентами
```bash
# Прочитать граф для контекста (топ связей конкретной заметки)
python3 -c "
import json
g = json.load(open('$HOME/Desktop/personal os/obsidian/tools/graph/graph.json'))
path = '02 — Внутренний мир/...'
edges = [e for e in g['edges'] if path in (e['source'], e['target'])]
edges.sort(key=lambda x: -x['weight'])
print(json.dumps(edges[:10], ensure_ascii=False, indent=2))
"
```

---

## Стандартный контекст-лоадинг (Phase 0)

Перед работой с любой темой — три слоя контекста:
```bash
# 1. Текстовый поиск (быстрый)
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}","limit":5}'

# 2. Семантический поиск (по смыслу)
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py hybrid "{тема}" 10

# 3. Скрытые связи (неожиданные пересечения)
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py brainstorm "{тема}" 10
```

---

## Готчи

- Не переименовывай заметки — ломаются wiki-ссылки `[[Note Name]]`.
- `11 — Архив` — только чтение. Заметки не удаляй.
- Теги чувствительны к регистру в файлах, но поиск по тегу работает case-insensitive.
- `obsidian_search` ищет через LIKE — по ключевым словам, не по смыслу.
- При создании заметки — `obsidian_create` НЕ перезаписывает существующие, выбросит ошибку.
