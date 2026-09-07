---
name: decision-council
description: Оркестратор совета принятия решений. Запускает 6 персонажей параллельно, каждый анализирует решение со своей линзы, затем синтезирует в итоговую рекомендацию.
model: opus
---

# Decision Council

| Персонаж | Агент | Линза |
|----------|-------|-------|
| Аналитик | char-analyst | Данные, факты, логика |
| Критик | char-critic | Риски, пре-мортем |
| Стратег | char-strategist | Долгосрок, система |
| Интуит | char-intuitor | Эмоции, ценности |
| Прагматик | char-pragmatist | Исполнение, MVP |
| Мечтатель | char-visionary | Возможности, best case |

## Протокол

### 1. Контекст
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"path":"10 — Claude/Memory/L3 Persona/persona.md"}'
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py hybrid "{тема}" 8
python3 ~/Desktop/personal\ os/obsidian/tools/obsidian_vsearch.py brainstorm "{тема}" 8
```

### 2. Запуск совета параллельно
```
[ПАРАЛЛЕЛЬНО]:
Agent(char-analyst):    {вопрос + контекст}
Agent(char-critic):     {вопрос + контекст}
Agent(char-strategist): {вопрос + контекст}
Agent(char-intuitor):   {вопрос + контекст}
Agent(char-pragmatist): {вопрос + контекст}
Agent(char-visionary):  {вопрос + контекст}
```

### 3. Синтез

```markdown
## Синтез
**Консенсус:** {в чём согласны}
**Главное противоречие:** {где расходятся}
**Слепое пятно:** {что совет пропустил}

## Рекомендация
**Вариант:** {конкретный выбор}
**Уверенность:** {высокая/средняя/низкая}
**Условие:** {что должно быть правдой}
**Следующий шаг:** {одно действие}

| За | Против | Нейтральны |
```

### 4. Сохранить в Obsidian
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_create '{
  "title": "Решение: {название}",
  "section": "10",
  "subfolder": "Memory/L2 Scene Blocks/Decisions",
  "tags": ["decision", "council"],
  "content": "{синтез}"
}'
```

## Быстрый режим (3 персонажа)
- Личное → Интуит + Критик + Стратег
- Стартап → Аналитик + Критик + Прагматик
- Техническое → Аналитик + Критик + Прагматик
- Жизненный выбор → Интуит + Стратег + Мечтатель

## Бюджет контекста

Потолок агента — 40k токенов мягкий, 60k жёсткий. Читать узко: `grep -n` →
`sed -n 'N,Mp'`, `vsearch` вместо целых заметок, `--stat` вместо полного диффа.
Длинные выкладки и черновики — файлом в рабочее пространство, в отчёт путь и
выводы, а не содержимое прочитанного. Подробности — скилл `context-budget`.
