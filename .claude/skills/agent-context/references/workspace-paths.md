# Глобальные пути — Workspace и Sessions

## Obsidian Vault

```
VAULT = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон
```

## Рабочее пространство агентов (Workspace)

Всё рабочее пространство команд находится **в Obsidian**:

```
WORKSPACE_ROOT = {VAULT}/10 — Claude/Рабочее пространство/

Psych Team:    {VAULT}/10 — Claude/Рабочее пространство/psych/
Startup Lab:   {VAULT}/10 — Claude/Рабочее пространство/startup/
Dev Lab:       {VAULT}/10 — Claude/Рабочее пространство/dev/
Research Lab:  {VAULT}/10 — Claude/Рабочее пространство/research/
Office Bureau: {VAULT}/10 — Claude/Рабочее пространство/office/
Tutor Lab:     {VAULT}/10 — Claude/Рабочее пространство/tutor/
Personal OS:   {VAULT}/10 — Claude/Рабочее пространство/personal-os/
```

**Полные абсолютные пути:**

```
PSYCH_WS    = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/psych
STARTUP_WS  = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/startup
DEV_WS      = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/dev
RESEARCH_WS = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/research
OFFICE_WS   = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/office
TUTOR_WS    = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/tutor
POS_WS      = /Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон/10 — Claude/Рабочее пространство/personal-os
```

## Сессии (sync-claude-sessions → Obsidian)

Все сессии сохраняются через `sync-claude-sessions` в `10 — Claude/Контекст и Сессии/`:

```
SESSIONS_ROOT = {VAULT}/10 — Claude/Контекст и Сессии/

Psych Team:    {VAULT}/10 — Claude/Контекст и Сессии/Psych Team/
Startup Lab:   {VAULT}/10 — Claude/Контекст и Сессии/Startup Lab/
Dev Lab:       {VAULT}/10 — Claude/Контекст и Сессии/Dev Lab/
Personal OS:   {VAULT}/10 — Claude/Контекст и Сессии/Personal OS/
```

## Протокол работы с workspace

### До начала работы (каждый агент)

1. Прочитай последнюю сессию своей команды:
```bash
VAULT="/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
SESSIONS="$VAULT/10 — Claude/Контекст и Сессии/{команда}"
ls "$SESSIONS/" 2>/dev/null | sort | tail -1
# прочитать найденный файл
```

2. Прочитай свой контекст (открытые вопросы, следующие шаги):
```bash
node ~/.claude/skills/obsidian/driver.mjs obsidian_read '{"name":"{agent-name}"}'
```

3. Проверь рабочее пространство:
```bash
ls "{WORKSPACE}/{команда}/" # что уже есть
```

### После завершения работы (оркестратор)

Сохрани сессию через sync-claude-sessions:

```bash
VAULT="/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
VAULT_DIR="$VAULT" \
CLAUDE_SESSIONS_OUTPUT="$VAULT/10 — Claude/Контекст и Сессии/{Команда}" \
  python3 ~/.claude/skills/sync-claude-sessions/scripts/claude-sessions export \
  "$(ls -t ~/.claude/projects/-Users-dimitrisimonyan/*.jsonl | head -1)"
```

Файл создастся автоматически с метаданными: дата, навыки, артефакты, сообщения.
