---
name: telegram
description: "Работа с Telegram: база всех чатов фермы аккаунтов (поиск чатов без выхода в Telegram), отправка сообщений, чтение диалогов и истории, очередь постепенных заходов в новые чаты, управление участниками групп. Используй когда просят найти чат, написать в Telegram, прочитать чат, получить историю сообщений, собрать список чатов по теме, управлять группой."
---

# Telegram — инструкция для агента

Инструменты запускаются через driver напрямую — подключается к реальным аккаунтам через GramJS/MTProto. `API_ID`/`API_HASH` (общие для всех аккаунтов) читаются из `.env`; сессии аккаунтов хранятся в `accounts.json`.

**Driver:** `~/.claude/skills/telegram/driver.cjs`

```bash
node ~/.claude/skills/telegram/driver.cjs <tool_name> '<json_args>'
```

**ВАЖНО:** Все действия выполняются от реального аккаунта. `send_message` реально отправит сообщение — всегда уточни у пользователя перед отправкой.

---

## Сначала база, потом Telegram

Все чаты всех аккаунтов фермы лежат в SQLite `chats.db` рядом с драйвером.
**За списком чатов ходи в базу, а не в Telegram:** `db_chats` — 0,1 с и без следа,
`get_dialogs` по ферме — 15 с, флуд-вейт и лишняя активность на аккаунте.

```bash
node ~/.claude/skills/telegram/driver.cjs db_chats '{"q":"фриланс","limit":20}'
node ~/.claude/skills/telegram/driver.cjs db_chat  '{"username":"frilanserov_chat"}'
```

В Telegram выходят только три инструмента базы: `db_sync` (раз в сутки),
`db_cache` (точечно) и `queue_run` (очередь заходов).

📖 Полное описание базы: `~/.claude/skills/telegram/references/chats-db.md`

---

## Ферма аккаунтов

К скиллу подключается **сколько угодно аккаунтов**: `API_ID`/`API_HASH` общие (`.env`),
сессии и метаданные каждого аккаунта — в `accounts.json` (chmod 600). Один помечен как `default`.
Смысл фермы — развести нагрузку и риск: личный аккаунт для переписки и памяти, рабочие —
для рассылок, вступления в чаты и сбора публичных данных.

📖 Полное описание фермы (роли, лимиты, статусы, правила против блокировок, грабли):
`~/.claude/skills/telegram/references/account-farm.md`
Реестр аккаунтов Димитри: `10 — Claude/Рабочее пространство/personal-os/Ферма Telegram-аккаунтов.md`

**Выбор аккаунта** для любого инструмента — поле `account` в JSON (или env `TELEGRAM_ACCOUNT`). Без него используется `default`, а если стора нет — legacy-сессия из `.env`.
```bash
node ~/.claude/skills/telegram/driver.cjs get_me '{"account":"work"}'
node ~/.claude/skills/telegram/driver.cjs list_accounts          # ферма: метки, роли, статусы, default
node ~/.claude/skills/telegram/driver.cjs check_accounts '{"accounts":["blast"]}'   # живы ли сессии
TELEGRAM_ACCOUNT=scout python3 script.py                         # для скриптов, не знающих про ферму
```

**Авторизация нового аккаунта (интерактивно — код приходит в Telegram пользователя):**
```bash
# Запускать ИНТЕРАКТИВНО (пользователь вводит номер, код, 2FA-пароль):
node ~/.claude/skills/telegram/auth.cjs add <label>
node ~/.claude/skills/telegram/auth.cjs list
node ~/.claude/skills/telegram/auth.cjs check [label]              # проверка живости сессий
node ~/.claude/skills/telegram/auth.cjs set <label> role рассылки  # role | note | tags | status
node ~/.claude/skills/telegram/auth.cjs default <label>   # сменить активный
node ~/.claude/skills/telegram/auth.cjs remove <label>
node ~/.claude/skills/telegram/auth.cjs import-legacy <label>  # импорт сессии из .env
```
Агент НЕ может ввести код подтверждения сам — он приходит в приложение Telegram пользователя.
Пользователь запускает `auth.cjs add` сам (в Claude Code — через префикс `!`).

**Авторизация руками агента** (`auth-relay.cjs`) — когда терминал не интерактивный: код и
2FA-пароль присылает пользователь в чат, шаги разнесены по вызовам, состояние между ними
лежит на диске (`.auth-pending.json`), так что недоведённый вход переживает конец сессии.
```bash
node ~/.claude/skills/telegram/auth-relay.cjs start acc11 +79991234567
node ~/.claude/skills/telegram/auth-relay.cjs code acc11 12345
node ~/.claude/skills/telegram/auth-relay.cjs password acc11 '<облачный пароль>'
node ~/.claude/skills/telegram/auth-relay.cjs status     # кто на каком шаге стоит
node ~/.claude/skills/telegram/auth-relay.cjs cancel acc11
```

**Подключить пачку аккаунтов** (по одному, с паузой 90 с между логинами, отчёт в конце):
```bash
! bash ~/.claude/skills/telegram/scripts/add-farm.sh acc4 acc5 acc6
! bash ~/.claude/skills/telegram/scripts/add-farm.sh     # метки спросит по ходу
```

**Статусы аккаунта** (`status`): `active` · `cooldown` (отдыхает) · `limited` (спам-блок, только чтение) · `banned` · `unknown`.
Ручные пометки проверка не стирает — снимаются командой `set <label> status active`.

---

## База чатов

Одна SQLite-база на всю ферму: чаты, кто из аккаунтов их видит, за что отвечает каждый
аккаунт, очередь заходов и кэш сообщений. Подробности, схема и грабли — `references/chats-db.md`.

### Читать из базы (Telegram не трогается)
```bash
D=~/.claude/skills/telegram/driver.cjs
node $D db_chats '{"q":"фриланс","limit":20}'        # поиск: название, @, описание
node $D db_chats '{"kind":"group","order":"size"}'   # kind: user|bot|group|channel; order: recent|size|title|seen
node $D db_chats '{"account":"acc2"}'                # что видит конкретный аккаунт
node $D db_chats '{"topic":"фриланс","tag":"активный"}'
node $D db_chat  '{"username":"frilanserov_chat"}'   # карточка чата целиком
node $D db_messages '{"chat_id":"@durov","limit":20}'
node $D db_messages '{"q":"вакансия"}'               # поиск по всему кэшу
node $D db_stats
node $D db_tag '{"chat_id":"@chat","topic":"фриланс","tags":["blast-кандидат"],"note":"живой"}'
```

### Наполнять базу
```bash
node $D db_sync                                   # вся ферма: аккаунты + диалоги, ~15 с
node $D db_sync '{"account":"main","limit":5000}' # один аккаунт глубже
node $D db_cache '{"chat_id":"@durov","limit":100}'  # обновить кэш сообщений точечно
```
**Синк идёт сам:** крон-задача `tg-chats-sync` каждый день в 9:20 (по воскресеньям —
полный проход), скрипт `scripts/daily-sync.sh`. Руками гонять `db_sync` нужно только
после подключения нового аккаунта или если база явно отстала: чаще раза в сутки не
стоит — на 2000 диалогов Telegram уже вставляет флуд-вейт.

### Кто за что отвечает
```bash
node $D db_accounts     # роль, за что отвечает, зоны, лимиты, израсходовано за сегодня
node $D db_account_set '{"label":"acc2","role":"чтение","duty":"радар и очередь",
                         "scope":["radar","queue"],"join_limit_day":8,"read_limit_day":60,"msg_limit_day":0}'
```
Сессии и статусы — в `accounts.json`, ответственность и суточные лимиты — в базе.
`db_sync` синхронизирует первое во второе, не затирая `duty`/`scope`/лимиты.

### Очередь чатов
Список интересных чатов составляется сразу, а заходы идут постепенно — по нескольку в
день, с паузами и суточным лимитом на аккаунт. Так база нарабатывается, не теряя аккаунты.
```bash
node $D queue_add '{"items":[
  {"target":"@chat1","action":"info","reason":"кандидат в blast-список","priority":2},
  {"target":"@chat2","action":"read"},
  {"target":"https://t.me/+abc","action":"join","account":"acc2"}]}'
node $D queue_list '{"status":"pending"}'
node $D queue_run  '{"account":"acc2","limit":5,"dry":true}'  # план + остаток бюджета
node $D queue_run  '{"account":"acc2","limit":5}'             # заход по-настоящему
node $D queue_set  '{"id":12,"status":"skipped"}'
```
`action`: `info` (карточка без вступления) · `read` (+ кэш сообщений) · `join` (вступить).
На `FLOOD_WAIT`/`PEER_FLOOD` проход целиком останавливается, запись откладывается,
аккаунт уходит в `cooldown` — переключаться на следующий аккаунт с тем же темпом нельзя.

Очередь `acc7` разбирается сама: крон-задача `tg-queue-run` дважды в день (13:10 и 21:10)
зовёт `scripts/queue-run.sh acc7 2`. Ставить в очередь — можно сразу и пачкой, темп держат
`join_limit_day` аккаунта и расписание.

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

### export_chat
Полный экспорт диалога в markdown + скачивание медиа (фото, голосовые, кружки, видео, стикеры, файлы).
```bash
node ~/.claude/skills/telegram/driver.cjs export_chat '{"chat_id":"@username","out_dir":"/путь/к/папке"}'
node ~/.claude/skills/telegram/driver.cjs export_chat '{"chat_id":"@username","out_dir":"…","limit":500,"media":false}'
# → {"file":"…/2026-07-21 export.md","messages":7054,"period":"…","media":{"saved":312,"skipped":1,"failed":0,"mb":840.2}}
```
Параметры: `chat_id`, `out_dir` (создаётся сам) — обязательные; `limit` (0/нет = вся история),
`media` (по умолчанию `true`), `max_media_mb` (по умолчанию 100 — файлы крупнее помечаются в тексте, но не качаются),
`name` (переопределить имя чата в заголовке).

Формат: `{дата} export.md` в стиле остальных выгрузок в `11 — Архив/Телеграм чаты/` (front matter + группировка по дням,
строки `- ЧЧ:ММ — **Имя:** текст`), медиа — в подпапку `media/` именами `{id}_{тип}.{ext}`.
Фото/голосовые/кружки/видео/стикеры вставляются как `![](media/…)` — Obsidian показывает их встроенно
(у голосовых и кружков появляется плеер), документы идут ссылкой.
Сообщения выгружаются от старых к новым; уже скачанные файлы повторно не тянутся — экспорт можно перезапустить.
Долгие выгрузки печатают прогресс в stderr (`… 200 сообщений`).

### get_entity
Информация о пользователе, чате или канале.
```bash
node ~/.claude/skills/telegram/driver.cjs get_entity '{"entity":"@username"}'
node ~/.claude/skills/telegram/driver.cjs get_entity '{"entity":"+79991234567"}'
# → {id, username, firstName, lastName, title, type}
```

### search_public
Глобальный поиск публичных каналов, групп и пользователей по названию/юзернейму (`contacts.Search`).
```bash
node ~/.claude/skills/telegram/driver.cjs search_public '{"query":"английский по фильмам","limit":40}'
# → [{id, title, username, type, isChannel, isGroup, participants, verified, scam}]
```
Отдаёт ~10 результатов на запрос — для широкого охвата гоняй пачку синонимов и склеивай по `username`
(пауза ~1 с между запросами против флуд-лимита). Результаты подкрашены языком/регионом аккаунта.

### chat_info
Подробности публичного канала/группы: описание, число участников, онлайн.
```bash
node ~/.claude/skills/telegram/driver.cjs chat_info '{"chat_id":"@englishgalaxy_school"}'
# → {id, title, username, isChannel, isGroup, participants, online, about}
```
Живость проверяется отдельно — `get_chat_history` по публичному чату читается без вступления
(дата последнего сообщения + сколько их за неделю).

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

### group_members
Кто в группе. Аккаунты фермы подписываются меткой — сразу видно, кто из своих внутри.
```bash
node ~/.claude/skills/telegram/driver.cjs group_members '{"chat_id":"-1002263449848"}'
# → {chat_id, title, participants, members:[{id, username, name, bot, account}]}
```
`account: "acc2"` у участника = это аккаунт фермы; `null` — сторонний человек.
Работает и с обычными группами, и с супергруппами; `limit` (по умолчанию 200) — пагинация.

### add_member
Добавить участника в группу. `user` — метка фермы (`acc2`), `@username` или id.
```bash
node ~/.claude/skills/telegram/driver.cjs add_member '{"chat_id":"-1002263449848","user":"acc2"}'
# → {chat_id, title, user, method, inside, hint}
```
Приглашает аккаунт из `account` (по умолчанию `default`), ему нужны права на добавление.
Идемпотентно: если участник уже внутри — `method: "уже внутри"`, ничего не делается.

Для аккаунтов фермы есть обход: админ часто не может получить input-entity своего же
аккаунта (тот не в его кэше сущностей) — тогда админ выпускает ссылку-приглашение, а
приглашаемый входит по ней своей сессией (`method: "вход по ссылке-приглашению"`).
Для сторонних пользователей обхода нет — только приглашение, и приватность может его
запретить (`USER_PRIVACY_RESTRICTED`).

Состав в `chats.db` обновится следующим `db_sync` по этому аккаунту.

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

- **За чатами — в базу (`db_chats`), а не в `get_dialogs`.** `get_dialogs` нужен, только
  когда важна секундная свежесть (только что созданный чат) или база ещё не синкалась.
- Пустой ответ `db_chats` про заведомо существующий чат = «его нет у нас», а не «его нет»:
  прогони `db_sync` или поставь в очередь.
- `chats.db` наружу не отдаётся (личные названия и кэш переписки) — как и `accounts.json`.
- Числовой `chat_id` для групп/каналов обычно отрицательный: `-1001234567890`.
- `get_chat_history` возвращает сообщения от новых к старым.
- `restrict_user` работает только если аккаунт — администратор группы.
- Логи GramJS (INFO/WARN) пишутся в stderr — это нормально, они не мешают JSON-выводу в stdout.
- Если сессия устарела — `AUTH_KEY_UNREGISTERED`. Переавторизоваться: `node ~/.claude/skills/telegram/auth.cjs add <label>` (интерактивно, код приходит в Telegram).
- Ферма аккаунтов: см. раздел «Ферма аккаунтов» выше. Выбор — поле `account` в аргументах, подробности — `references/account-farm.md`.
- Рассылки — только с рабочего аккаунта, паузы 30–90 с, потолок в десятках сообщений в день. На `FLOOD_WAIT`/`PEER_FLOOD` — стоп и `status cooldown`, а не переключение на следующий аккаунт.
- `remove` убирает сессию из стора, но не разлогинивает аккаунт в Telegram — сессию надо снять в приложении (Настройки → Устройства).
