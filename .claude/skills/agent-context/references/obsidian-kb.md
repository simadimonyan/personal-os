# Obsidian как общая база знаний

Obsidian — единое хранилище знаний, памяти и контекста для всей системы агентов. Каждый агент читает оттуда то, что релевантно его задаче, перед тем как начать работу.

Driver: `node ~/.claude/skills/obsidian/driver.mjs <tool> '<json>'`

---

## Структура знаний

### Профиль Димитри (раздел 01, 02)
Читай перед любой задачей связанной с личными решениями, карьерой, психологией, контентом.

```bash
# Личный профиль и ценности
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"профиль", "limit":5}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"обо мне", "limit":5}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"ценности", "section":"01", "limit":10}'

# Дневник и внутренний мир (для psych-team)
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"дневник", "section":"02", "limit":20}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_recent '{"limit":10, "section":"02"}'
```

### Профессиональный профиль (раздел 04, 06)
Читай перед карьерными задачами, стартап-работой, HR.

```bash
# Цели и стратегия
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"цели", "section":"04", "limit":10}'
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"резюме", "limit":5}'

# Активные проекты
node ~/.claude/skills/obsidian/driver.mjs obsidian_list '{"section":"06", "limit":20}'
```

### Накопленные знания (раздел 03, 05)
Читай перед исследованием — возможно, тема уже изучалась.

```bash
# Идеи и мысли
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}", "section":"03", "limit":10}'

# CS и знания
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}", "section":"05", "limit":10}'
```

### Контекст сессий (раздел 10)
Контексты работы команд (см. agent-context/SKILL.md).

```bash
# Контекст конкретного агента
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"{agent-name}"}'

# Поиск по контекстам команды
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"{тема}", "section":"10", "limit":10}'
```

---

## Что читать — правила для агентов

| Задача | Что читать |
|--------|-----------|
| Любая личная задача | Профиль (01, 02) + контекст агента (10) |
| Карьера / HR | Резюме + цели (04) + профессиональные проекты (06) |
| Контент / SMM | Личные заметки (01, 02, 03) для голоса и стиля |
| Стартап / продукт | Проекты (06) + идеи (03) + контекст агента (10) |
| Исследование | Знания (03, 05) — не повторяй уже изученное |
| Психологический анализ | Дневник (02) + все личные разделы (01) |
| Разработка | Проекты (06) + технические знания (05) |

---

## Запись в Obsidian

Агент пишет в Obsidian через знание-агента или напрямую через driver.

```bash
# Создать заметку
node ~/.claude/skills/obsidian/driver.mjs obsidian_create '{
  "title": "{название}",
  "section": "{номер раздела}",
  "subfolder": "{подпапка}",
  "tags": ["{теги}"],
  "content": "{содержимое}"
}'

# Дополнить существующую
node ~/.claude/skills/obsidian/driver.mjs obsidian_append '{
  "name": "{название}",
  "content": "\n{новый контент}"
}'
```

---

## Правило: Obsidian — источник истины

Если в Obsidian есть информация по теме → используй её.
Если нет → можешь предложить создать заметку по итогам работы.
Устаревшая информация в Obsidian → обнови её, не работай на основе старых данных.
