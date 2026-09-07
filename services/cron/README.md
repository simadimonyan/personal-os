# Personal OS Cron

Планировщик автоматизированных задач для Personal OS. Крутится фоновым демоном
(launchd), по расписанию выполняет задачи и раскладывает результат в Obsidian,
файл или лог.

Главное отличие от «будильника» внутри сессии Claude Code: **этот сервис работает,
даже когда Claude Code закрыт** — это системный демон macOS. Mac должен быть включён.

Зависимостей нет — только стандартная библиотека Python 3.11+.

---

## Что умеет

Задача (`job`) = **расписание** + **действие** + **куда положить результат**.

**Действия:**
- `prompt` — отправить промпт локальному Claude через
  [`claude-local-api`](../claude-local-api-main) (Unix-сокет `/tmp/claude_api.sock`).
- `shell` — выполнить shell-команду.

**Приёмники результата (`output`):**
- `obsidian` — дозаписать в заметку (через `~/.claude/skills/obsidian/driver.mjs`).
- `file` — дозаписать в файл.
- `telegram` — отправить текст сообщением в чат/канал (через `~/.claude/skills/telegram/driver.cjs`).
- `log` — только в `logs/cron.log` (по умолчанию).

**Расписание:**
| Формат | Пример | Значение |
|--------|--------|----------|
| cron (5 полей) | `0 9 * * *` | каждый день в 9:00 |
| cron c шагом | `*/15 * * * *` | каждые 15 минут |
| cron по будням | `0 10 * * 1-5` | в 10:00 пн–пт |
| интервал | `@every 30m` | каждые 30 минут (`s`/`m`/`h`/`d`) |
| ярлыки | `@hourly` `@daily` `@weekly` `@monthly` | |

---

## Файлы

```
cron/
├── cron.py        — демон (тик каждые 30с, env POS_CRON_TICK)
├── cronctl.py     — CLI управления задачами
├── core.py        — ядро: расписания, выполнение, приёмники
├── client.py      — клиент к claude-local-api (сокет)
├── jobs.json      — определения задач (правится через cronctl или руками)
├── state.json     — состояние срабатываний (создаётся автоматически)
├── logs/          — cron.log, runs.log, launchd.*
└── deploy/        — launchd plist + install.sh / uninstall.sh
```

---

## Быстрый старт

```bash
cd ~/Desktop/personal\ os/services/cron

# (для prompt-задач) поднять локальный Claude-сервер в соседней папке:
#   cd ../claude-local-api-main && python server.py

# добавить задачу: утренний брифинг в Obsidian
./cronctl.py add morning-brief --schedule "0 9 * * *" --model sonnet \
  --prompt "Составь короткий фокус-лист на сегодня: 3 приоритета и напоминание о балансе." \
  --obsidian "10 — Claude/Рабочее пространство/personal-os/Брифинги.md" \
  --desc "Утренний брифинг"

# проверить прямо сейчас (мимо расписания)
./cronctl.py run morning-brief

# поставить как фоновый сервис (авто-старт при логине)
bash deploy/install.sh
```

---

## Устойчивость к офлайну и пропускам (catch-up)

Планировщик понимает, что работает в фоне и связь может пропадать:

- **Догонка пропущенных запусков.** Если время cron-задачи прошло, пока ноутбук спал
  или не было сети, задача выполнится при первой возможности (в пределах окна
  `POS_CRON_CATCHUP_MIN`, по умолчанию 8 суток). Интервальные (`@every`) догоняют сами.
  Отключить для конкретной задачи: `--no-catchup`.
- **Учёт ресурсов.** Если задаче нужен ресурс, а его нет (нет интернета / не поднят
  claude-local-api), она **не «сгорает»**: запуск откладывается, задача остаётся
  просроченной и выполнится **приоритетно**, как только ресурс появится.
  Указывается через `--requires internet` или `--requires internet,claude`.
- **Приоритет.** На каждом тике сначала идут самые просроченные задачи.

```bash
# сетевая задача: при офлайне отложится и выполнится, когда появится интернет
./cronctl.py add news --schedule "@every 1h" --requires internet --shell "..."

# проверить состояние сети/ресурсов и просрочки
./cronctl.py status
```

Окно догонки и хост проверки сети настраиваются через env:
`POS_CRON_CATCHUP_MIN` (минут), `POS_CRON_NET_HOST` (по умолчанию `1.1.1.1`).

---

## Команды cronctl

```bash
./cronctl.py list                 # все задачи
./cronctl.py status               # последние срабатывания + статус сокета
./cronctl.py add <id> --schedule "..." (--prompt "..." [--model] | --shell "...") \
                      [--obsidian PATH] [--file PATH] [--desc "..."]
./cronctl.py run <id>             # запустить сейчас
./cronctl.py enable  <id>
./cronctl.py disable <id>
./cronctl.py rm <id>
./cronctl.py tick                 # один проход планировщика вручную (тест)
```

Примеры действий:
```bash
# shell-задача в файл-лог
./cronctl.py add disk --schedule "@every 6h" --shell "df -h / | tail -1" --file ~/disk.log

# вызвать драйвер Personal OS (Telegram/Obsidian/hh) как shell-команду
./cronctl.py add tg-digest --schedule "0 20 * * *" \
  --shell 'node ~/.claude/skills/telegram/driver.cjs ...' \
  --obsidian "10 — Claude/Рабочее пространство/personal-os/Дайджесты.md"
```

---

## Исходящие задачи: рассылки и посты

> ⚠️ Это **необратимые публичные действия**. Демон шлёт без подтверждения. Проверяй
> сначала через `./cronctl.py run <id>` и держи задачи `disabled`, пока не уверен.

**Рассылка по фриланс-чатам** (shell + готовый `blast.sh`, паузы 40–70с):
```bash
mkdir -p messages && echo "Текст интро..." > messages/intro.txt
./cronctl.py add freelance-blast --schedule "0 11 * * 1,4" \
  --shell "bash '/Users/dimitrisimonyan/Desktop/personal os/.claude/skills/tg-freelance-blast/scripts/blast.sh' \
           '$(pwd)/messages/intro.txt' \
           '/Users/dimitrisimonyan/Desktop/personal os/.claude/skills/tg-freelance-blast/references/known_chats.txt' 40 70" \
  --obsidian "10 — Claude/Рабочее пространство/personal-os/Рассылки.md"
```

**Пост на ТГ-канал, сгенерированный локальным Claude** (prompt → telegram):
```bash
./cronctl.py add channel-post --schedule "0 10 * * *" --model sonnet \
  --prompt "Короткий пост (3-5 предложений) о продуктивности. Живой тон, без хэштегов. Только текст." \
  --telegram "@your_channel"
```

**Пост с фиксированным текстом из файла** (shell → telegram driver напрямую):
```bash
./cronctl.py add channel-fixed --schedule "0 10 * * *" \
  --shell "node ~/.claude/skills/telegram/driver.cjs send_message \
           \"\$(jq -nc --arg t \"\$(cat messages/post.txt)\" '{chat_id:\"@your_channel\",text:\$t}')\""
```

**Чтобы не превратиться в спам / не словить бан:**
- Не слать одинаковый текст в одни и те же чаты часто — рассылку максимум 1–2 раза в неделю,
  по будням, с паузами (blast.sh уже рандомит 40–70с).
- Для постов лучше `prompt` (каждый раз свежий текст), а не один и тот же файл.
- Сначала `run` вручную и посмотри результат в Obsidian/логе, потом `enable`.
- Рассылка ≠ канал: канал — твой (`send_message`), рассылка — чужие чаты (риск жалоб/бана).

---

## Управление сервисом

```bash
bash deploy/install.sh     # установить и запустить
bash deploy/uninstall.sh   # остановить и убрать из автозапуска
tail -f logs/cron.log      # следить за работой
```

После правки `jobs.json` перезапуск **не нужен** — демон перечитывает файл каждый тик.
Перезапуск нужен только если поменял `cron.py`/`core.py`:
```bash
launchctl unload ~/Library/LaunchAgents/com.dimitri.poscron.plist
launchctl load   ~/Library/LaunchAgents/com.dimitri.poscron.plist
```

---

## Структура задачи (jobs.json)

```json
{
  "id": "morning-brief",
  "schedule": "0 9 * * *",
  "enabled": true,
  "description": "Утренний брифинг",
  "action": { "type": "prompt", "model": "sonnet", "prompt": "..." },
  "output": [
    { "to": "obsidian", "path": "10 — Claude/.../Брифинги.md" }
  ]
}
```

`output` можно задать одним объектом или списком — результат уйдёт во все приёмники.
У приёмника можно задать `header` — заголовок блока (по умолчанию `## <id> — <дата>`).
