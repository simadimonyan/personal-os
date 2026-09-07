# Reddit — публикации и участие

Работает под **личным аккаунтом через живую сессию Chrome**. Никаких
приложений, ключей и паролей в файлах — как у hh.ru.

- `reddit.py` — логика: гарды, вердикты, спеки, проверка снятия (Python, stdlib)
- `driver.mjs` + `browser.mjs` — транспорт: системный Chrome через Playwright
- База `reddit.db` · сессия `~/.reddit-session/` · `config.json` — только гарды

## Чем Reddit отличается от Telegram

В телеграме сообщение либо ушло, либо нет. На Reddit есть третье состояние:
**пост ушёл, виден автору и не виден никому больше**. AutoModerator снимает его
по возрасту аккаунта, карме, домену ссылки или отсутствию флейра — молча, без
уведомления. Поэтому здесь после каждой публикации обязательна команда `verify`.

Второе отличие — писаное правило самопиара **9:1**: на одно сообщение про своё
должно приходиться примерно девять, где ты просто участвуешь. Инструмент считает
это соотношение и предупреждает при перекосе.

Третье — там репортят не за факт рекламы, а за бесполезность. Развёрнутый ответ
в чужом треде не снимают никогда; пост «я сделал штуку» в сабреддите, где таких
постов нет, снимают всегда.

## Настройка (один раз)

```bash
cd tools/reddit
npm install                 # playwright, браузер не качается — берём системный Chrome
python3 reddit.py login     # откроется Chrome, входишь руками, сессия сохранится
python3 reddit.py me        # карма, возраст, готовность к постингу
```

Вход нужен только для действий (посты, комментарии, входящие). Разведка —
`find`, `sub info`, `sub rules`, `threads` — работает и без него.

## Порядок работы

```bash
cd tools/reddit

# 0. готов ли аккаунт вообще
python3 reddit.py me

# 1. разведка: где сидит аудитория
python3 reddit.py find "learn english"
python3 reddit.py sub info languagelearning --flairs
python3 reddit.py sub rules languagelearning

# 2. преполёт по конкретной площадке (пишет вердикт в базу)
python3 reddit.py sub check languagelearning
python3 reddit.py sub add languagelearning --note "основная аудитория"

# 3. участие — главный канал, риска ноль
python3 reddit.py threads "can't understand by ear" --days 7
python3 reddit.py comment t3_abc123 --file ответ.md --dry
python3 reddit.py comment t3_abc123 --file ответ.md

# 4. публикация — из файла-спеки, после гардов
python3 reddit.py post мой-пост.json --dry
python3 reddit.py post мой-пост.json

# 5. ОБЯЗАТЕЛЬНО через 10–15 минут
python3 reddit.py verify --all

# состояние
python3 reddit.py stats      # соотношение 9:1
python3 reddit.py plan       # что гарды разрешают сегодня
python3 reddit.py inbox --unread
```

## Спека поста

`post.example.json` + `post.example.md` — рабочий образец в жанре, который
на Reddit проходит (личный опыт + вопрос сообществу, без ссылок).

```json
{
  "subreddit": "languagelearning",
  "kind": "self",             // self — текст, link — ссылка
  "title": "…",
  "text_file": "post.md",     // либо "text": "…"
  "flair": "Discussion",      // по названию, id подберётся сам
  "send_replies": true
}
```

## Гарды

Настраиваются в `config.json → guards`, перебиваются флагом `--force`:

| Гард | По умолчанию | Зачем |
|---|---|---|
| `min_hours_between_posts` | 12 | серия постов подряд — подпись бота |
| `max_posts_per_day` | 3 | суточный потолок |
| `days_between_same_sub` | 30 | повтор в тот же сабреддит = спам |
| `comment_to_post_ratio` | 5 | правило 9:1, считается по своей базе |
| `min_account_age_days` | 30 | младше — почти везде авто-снос |
| `min_comment_karma` | 50 | то же самое |

Возраст и карма не обходятся ничем, кроме нескольких дней осмысленных
комментариев. Это не ограничение инструмента, это устройство площадки.

## Грабли (проверено на этой машине 13.08.2026)

- **403 Blocked на любой запрос снаружи браузера.** `curl`, `urllib` и даже
  `context.request` Playwright с живыми кукисами и браузерным User-Agent
  получают 403 — Reddit фильтрует по отпечатку соединения, а не по UA. Поэтому
  запросы делаются **изнутри вкладки** (`page.evaluate(fetch)`), там те же
  ручки отдают 200. Не «чинить» это заменой User-Agent — не поможет.
- **old.reddit.com, не www.** Простые формы и стабильные `.json`-ручки,
  переживающие редизайны нового интерфейса.
- **`modhash`** — старый Reddit требует токен формы на любое действие. Берётся
  из `/api/me.json` и уходит полем `uh` + заголовком `X-Modhash`. Пустой
  modhash = сессия выкинута, нужен повторный `login`.
- **`RATELIMIT` в ответе на submit** — у аккаунта мало кармы, Reddit сам режет
  частоту постов. Ждать, не долбить.
- **Пост «есть», но его никто не видит** — `verify` покажет `СНЯТ`. Лечится
  письмом модераторам, а не перезаливом (перезалив читается как обход).
- **Правило про самопиар почти везде.** В r/languagelearning (3.4M) свой
  продукт нельзя постить без разрешения модераторов, в r/EnglishLearning
  (715k) — «you will be banned if you promote before seeking permission».
  `sub check` вытаскивает такие пункты из правил до отправки.
