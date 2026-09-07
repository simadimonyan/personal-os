# Кросс-командное взаимодействие

Команды Personal OS могут запрашивать помощь друг у друга. Это позволяет команде разработки использовать UX-исследования из startup-lab, стартап-команде — психологические инсайты из psych-team, и т.д.

---

## Матрица взаимодействий

| Кто запрашивает | Что запрашивает | У кого |
|----------------|-----------------|--------|
| startup-lab | Психологический портрет основателя | psych-team |
| startup-lab | Данные из Obsidian / Zotero | personal-os |
| startup-lab | UI/UX для продукта | dev-lab |
| dev-lab | PRD / roadmap | startup-lab → product-manager |
| dev-lab | Исследование рынка | startup-lab → market-researcher |
| dev-lab | Данные из Obsidian | personal-os |
| psych-team | Бизнес-контекст | startup-lab → business-psychologist |
| psych-team | Данные из Telegram | personal-os → action-agent |
| любая команда | Поиск / запись в Obsidian | personal-os → knowledge-agent |
| любая команда | Действия в Telegram / hh.ru | personal-os → action-agent |

---

## Протокол запроса

### Из оркестратора команды

Когда команда нуждается в данных другой команды:

```
1. Оркестратор сигнализирует мастер-оркестратору (personal-os):
   "Команда [team] запрашивает у команды [other-team]: {описание запроса}"

2. Мастер-оркестратор:
   a. Создаёт задачу для нужной команды
   b. Ждёт результат (или запускает параллельно если не блокирует)
   c. Передаёт результат запросившей команде

3. Результат сохраняется в 10 — Claude/Рабочее пространство/{requesting-team}/cross-team/{source-team}_{artifact}.md
```

### Прямой запрос к personal-os агентам

Для Obsidian и Telegram — команды могут использовать их напрямую если оркестратор дал разрешение:

```bash
# Любая команда может читать Obsidian через driver
node ~/.claude/skills/obsidian/driver.mjs obsidian_search '{"query":"...", "limit":10}'

# Для действий в Telegram / hh.ru — только через action-agent (personal-os)
# Запрашивай через мастер-оркестратор
```

---

## Примеры кросс-командных сценариев

### Dev Lab → Startup Lab (PRD для разработки)
```
dev-lab запрашивает PRD:
→ мастер-оркестратор создаёт задачу для product-manager (startup-lab)
→ product-manager создаёт PRD в 10 — Claude/Рабочее пространство/dev/brief.md
→ dev-lab начинает работу
```

### Startup Lab → Psych Team (анализ для продукта)
```
growth-marketer нужен психологический профиль аудитории:
→ мастер-оркестратор активирует psych-team в режиме "анализ типичного пользователя"
→ psych-team создаёт профиль в 10 — Claude/Рабочее пространство/startup/research/user_psych.md
→ growth-marketer использует для ICP
```

### Любая команда → Personal OS (Obsidian)
```
Любой агент может читать Obsidian напрямую через driver.
Запись — рекомендуется через knowledge-agent чтобы не дублировать.
```

---

## Файловая конвенция кросс-командных артефактов

```
10 — Claude/Рабочее пространство/{команда}/cross-team/{источник}_{тип}_{дата}.md
```

Примеры:
- `10 — Claude/Рабочее пространство/dev/cross-team/startup-lab_prd_2026-05-31.md`
- `10 — Claude/Рабочее пространство/startup/cross-team/psych-team_user-profile_2026-05-31.md`
