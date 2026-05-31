---
name: zotero
description: "Работа с библиотекой Zotero: поиск источников, метаданные статей и книг, коллекции, теги, добавление источников. Используй когда просят найти источник, показать библиографию, добавить статью в Zotero или работать с научной литературой."
---

# Zotero — инструкция для агента

Инструменты запускаются через driver напрямую. Чтение идёт из SQLite (`~/Zotero`) — Zotero не обязан быть запущен. Запись требует открытого Zotero.

**Driver:** `~/.claude/skills/zotero/driver.mjs`

```bash
node ~/.claude/skills/zotero/driver.mjs <tool_name> '<json_args>'
```

---

## Инструменты

### zotero_stats
Статистика библиотеки: всего элементов и количество по типам.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_stats
# → {"totalItems": 42, "byType": [{"itemType":"journalArticle","count":30},...]}
```

### zotero_search
Поиск по библиотеке. Ищет в title, abstract, авторах, тегах, публикации.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_search '{"query":"machine learning","limit":20}'
node ~/.claude/skills/zotero/driver.mjs zotero_search '{"creator":"Иванов"}'
node ~/.claude/skills/zotero/driver.mjs zotero_search '{"tag":"AI","itemType":"journalArticle","limit":50}'
node ~/.claude/skills/zotero/driver.mjs zotero_search '{"collection":"Диссертация"}'
```
Параметры: `query`, `creator`, `tag`, `collection`, `itemType`, `limit` (макс 200, по умолч 25), `includeTrashed`.

### zotero_get_item
Полные метаданные одного элемента: поля, теги, коллекции, аннотация, заметки, вложения.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_get_item '{"idOrKey":"ABCD1234"}'
```
`idOrKey` — 8-символьный ключ из URL `zotero://select/library/items/ABCD1234` или числовой itemID.

### zotero_get_attachments
Список файлов (PDF, снапшоты) элемента с локальными путями.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_get_attachments '{"idOrKey":"ABCD1234"}'
```

### zotero_list_collections
Все коллекции с количеством элементов.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_list_collections
```

### zotero_list_tags
Теги, отсортированные по частоте.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_list_tags '{"limit":50}'
```

### zotero_recent
Последние добавленные элементы.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_recent '{"limit":10}'
```

### zotero_check_api
Проверить, запущен ли Zotero с HTTP API (нужно для записи).
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_check_api
# → {"ok": true} или {"ok": false, "error": "..."}
```

### zotero_item_template
Получить JSON-шаблон для типа элемента (перед добавлением).
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_item_template '{"itemType":"journalArticle"}'
```

### zotero_add_item
Добавить элемент(ы) в библиотеку. Требует открытый Zotero.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_add_item '{
  "items": [{
    "itemType": "journalArticle",
    "title": "Название статьи",
    "creators": [{"creatorType":"author","firstName":"Иван","lastName":"Иванов"}],
    "publicationTitle": "Название журнала",
    "date": "2024"
  }]
}'
```

### zotero_save_from_url
Сохранить источник по URL через Zotero-транслятор (как браузерное расширение). Требует открытый Zotero.
```bash
node ~/.claude/skills/zotero/driver.mjs zotero_save_from_url '{"url":"https://arxiv.org/abs/2301.00001"}'
```

---

## Типичные workflow

**Собрать библиографию по теме:**
```bash
# 1. Найти источники
node ~/.claude/skills/zotero/driver.mjs zotero_search '{"query":"нейронные сети","limit":50}'

# 2. Получить детали нужных (по ключу из результатов поиска)
node ~/.claude/skills/zotero/driver.mjs zotero_get_item '{"idOrKey":"ABCD1234"}'

# 3. Отформатировать в нужный стиль (APA, ГОСТ и т.д.)
```

**Добавить статью с arXiv:**
```bash
# Сначала проверь что Zotero запущен
node ~/.claude/skills/zotero/driver.mjs zotero_check_api

# Сохрани по URL
node ~/.claude/skills/zotero/driver.mjs zotero_save_from_url '{"url":"https://arxiv.org/abs/2301.00001"}'
```

---

## Готчи

- Поиск через `LIKE %query%` — не семантический. Ищи ключевые слова, не смысл.
- `zotero_search` без параметров вернёт первые 25 элементов — добавь `"limit":200` для полного списка.
- Если `~/Zotero` в нестандартном месте, задай `ZOTERO_DATA_DIR` в окружении.
