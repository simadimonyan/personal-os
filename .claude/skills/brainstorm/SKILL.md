---
name: brainstorm
description: "Обновление L4-памяти (семантического графа) базы знаний Obsidian (Органон). Находит неочевидные пересечения между разными разделами базы, перестраивает граф и записывает находки в лог L4 Semantic Graph. Триггеры: 'brainstorm', 'найди связи', 'скрытые связи', 'обнови граф', 'обнови граф связей', 'обнови L4', 'что пересекается с', 'неожиданные пересечения', 'мозговой штурм по базе'."
---

# brainstorm — L4-память (семантический граф)

Самостоятельная системная команда Personal OS. Обслуживает **L4-память** (семантический граф) — ассоциативный слой 5-уровневой модели памяти (`L0→L1→L2→L3→L4`). Не запускает командных агентов — работает напрямую с семантическим поиском vsearch по базе знаний «Органон».

**Цель:** найти неочевидные пересечения между РАЗНЫМИ разделами базы, перестроить семантический граф, зафиксировать находки в логе L4.

**Инструмент:** `~/Desktop/personal os/tools/obsidian/tools/obsidian_vsearch.py`
**Vault:** `/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон`
**L4-память:** `10 — Claude/Memory/L4 Semantic Graph/` (лог связей + граф-артефакт)

---

## Протокол

```bash
VSEARCH="$HOME/Desktop/personal os/tools/obsidian/tools/obsidian_vsearch.py"
VAULT="/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
```

### Шаг 1 — Свежесть индекса

```bash
python3 "$VSEARCH" stats                          # indexed_files
find "$VAULT" -name "*.md" | wc -l                # реальное число
# если разница > 0 → переиндексировать:
python3 "$VSEARCH" index
```

### Шаг 2 — Поиск скрытых связей

Сигнатура: `brainstorm "query" limit threshold`

```bash
# по теме (если пользователь задал тему):
python3 "$VSEARCH" brainstorm "{тема}" 20 0.70
# по всей базе (если темы нет):
python3 "$VSEARCH" brainstorm "" 25 0.72
```

Связи между одинаковыми разделами отбрасываются автоматически — остаются только мосты между разными разделами.

### Шаг 3 — Перестройка графа

Сигнатура: `build_graph threshold top_k`

```bash
python3 "$VSEARCH" build_graph 0.55 8
# граф: ~/Desktop/personal os/tools/obsidian/tools/graph/graph.html
```

### Шаг 4 — Записать находки в лог L4

Связи со `score > 0.75` между разными разделами → в **файл-день** лога L4 (создать если нет; в общий индекс содержимое не писать и целиком его не читать):

```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_append \
  '{"path":"10 — Claude/Memory/L4 Semantic Graph/Log/Graph Log {YYYY-MM-DD}.md","content":"## {YYYY-MM-DD} (brainstorm)\n- [[note1]] ↔ [[note2]] (раздел1 ↔ раздел2, score)\n..."}'
# + добавить "- [[Graph Log {YYYY-MM-DD}]]" в индекс Semantic Graph Log.md, если ссылки ещё нет
```

---

## Выходной отчёт

```
brainstorm: {K} новых связей · топ-мосты: {раздел↔раздел} · L4-граф обновлён · {N} записано в L4 Semantic Graph Log
```

Можно делегировать `Agent(vault-indexer)` — он выполняет Шаги 1–4 автономно (haiku, быстро).

## Связь с другими командами

- Полное обновление памяти + индекс + граф → `/update-memory` (вызывает brainstorm Шагами 3–4 внутри).
- Часть мастер-оркестратора `/personal-os` (раздел «Системные команды»).

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
