---
name: telegram
description: "Работа с Telegram-аккаунтом: отправка сообщений, чтение диалогов и истории, управление участниками групп. Используй когда просят написать в Telegram, прочитать чат, получить историю сообщений, управлять группой."
---

# Telegram — инструкция для агента

Инструменты запускаются через driver напрямую — подключается к реальному аккаунту `@dimitrisimonyan` через GramJS/MTProto. Credentials читаются автоматически из `telegram-mcp-server/.env`.

**Driver:** `~/.claude/skills/telegram/driver.cjs`

```bash
node ~/.claude/skills/telegram/driver.cjs <tool_name> '<json_args>'
```

**ВАЖНО:** Все действия выполняются от реального аккаунта. `send_message` реально отправит сообщение — всегда уточни у пользователя перед отправкой.

---

## Инструменты

### get_me
Информация о текущем аккаунте.
```bash
node ~/.claude/skills/telegram/driver.cjs get_me
# → {"id":"934458920","username":"dimitrisimonyan","firstName":"Димитри",...}
```

### get_dialogs
Список последних диалогов: группы, каналы, личные чаты.
```bash
node ~/.claude/skills/telegram/driver.cjs get_dialogs '{"limit":20}'
# → [{id, name, username, unreadCount, isChannel, isGroup, isUser}]
```
`id` из результата используй как `chat_id` в других инструментах.

### get_chat_history
История сообщений чата (новые первые).
```bash
node ~/.claude/skills/telegram/driver.cjs get_chat_history '{"chat_id":"-1001981718410","limit":20}'
node ~/.claude/skills/telegram/driver.cjs get_chat_history '{"chat_id":"@username","limit":10}'
# → [{id, text, date, fromId, out}]
```

### get_entity
Информация о пользователе, чате или канале.
```bash
node ~/.claude/skills/telegram/driver.cjs get_entity '{"entity":"@username"}'
node ~/.claude/skills/telegram/driver.cjs get_entity '{"entity":"+79991234567"}'
# → {id, username, firstName, lastName, title, type}
```

### send_message
Отправить сообщение. Поддерживает HTML и Markdown разметку.
```bash
node ~/.claude/skills/telegram/driver.cjs send_message '{"chat_id":"@username","text":"Привет!"}'
node ~/.claude/skills/telegram/driver.cjs send_message '{"chat_id":"-1001234567890","text":"<b>Важно:</b> текст","parse_mode":"html"}'
# → {"messageId": 123, "success": true}
```

### edit_message
Редактировать своё отправленное сообщение.
```bash
node ~/.claude/skills/telegram/driver.cjs edit_message '{"chat_id":"@username","message_id":123,"text":"Исправленный текст"}'
```

### delete_message
Удалить сообщение (revoke=true удаляет у всех).
```bash
node ~/.claude/skills/telegram/driver.cjs delete_message '{"chat_id":"-1001234567890","message_id":456}'
```

### restrict_user
Ограничить права пользователя в группе (нужны права администратора).
```bash
node ~/.claude/skills/telegram/driver.cjs restrict_user '{"chat_id":"-1001234567890","user_id":123456,"restrict_type":"mute","duration_seconds":3600}'
```
`restrict_type`: `mute` | `no_send_messages` | `no_send_media` | `full_restrict`

---

## Типичные workflow

**Найти чат и прочитать сообщения:**
```bash
# 1. Найти нужный чат
node ~/.claude/skills/telegram/driver.cjs get_dialogs '{"limit":50}'

# 2. Прочитать последние сообщения (использовать id из предыдущего шага)
node ~/.claude/skills/telegram/driver.cjs get_chat_history '{"chat_id":"-1001981718410","limit":20}'
```

**Отправить сообщение пользователю:**
```bash
# 1. Проверить что пользователь существует
node ~/.claude/skills/telegram/driver.cjs get_entity '{"entity":"@username"}'

# 2. Уточнить у пользователя текст, потом отправить
node ~/.claude/skills/telegram/driver.cjs send_message '{"chat_id":"@username","text":"Текст сообщения"}'
```

---

## Готчи

- Числовой `chat_id` для групп/каналов обычно отрицательный: `-1001234567890`.
- `get_chat_history` возвращает сообщения от новых к старым.
- `restrict_user` работает только если аккаунт — администратор группы.
- Логи GramJS (INFO/WARN) пишутся в stderr — это нормально, они не мешают JSON-выводу в stdout.
- Если сессия устарела — `AUTH_KEY_UNREGISTERED`. Нужно переавторизоваться: `node auth.js` в папке `telegram-mcp-server/`.
