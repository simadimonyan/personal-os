---
name: obsidian
description: "Работа с личной базой знаний Obsidian (Органон — 889 заметок). Используй когда просят найти заметку, прочитать, создать новую, написать в дневник, найти по тегу, показать последние заметки или статистику базы."
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

## Готчи

- Не переименовывай заметки — ломаются wiki-ссылки `[[Note Name]]`.
- `09 — Архив` — только чтение. Заметки не удаляй.
- Теги чувствительны к регистру в файлах, но поиск по тегу работает case-insensitive.
- `obsidian_search` ищет через LIKE — по ключевым словам, не по смыслу.
- При создании заметки — `obsidian_create` НЕ перезаписывает существующие, выбросит ошибку.
