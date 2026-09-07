# yandex-forms — сборка опросов на Яндекс Формах из JSON

Публичного API на **создание** форм у Яндекса нет. Но конструктор общается со своим
бэкендом обычным JSON, и мы говорим с ним напрямую: открываем страницу залогиненным
браузером, берём csrf-токен из `window.__DATA__` и шлём те же запросы через `fetch`
внутри страницы. Это на порядок надёжнее кликов по DOM (проверено: на 39 вопросах
кликер разваливался — вопросы уезжали не на ту страницу, заголовки не применялись,
сервер отвечал `duplicate value`).

## Быстрый старт

```bash
node yaforms.mjs login              # один раз: войти в Яндекс руками (откроется окно)
node yaapi.mjs build students --publish
node yaapi.mjs build authors --publish
```

Всё остальное работает без окна: `YAF_HEADLESS` по умолчанию включён в `yaapi.mjs`,
а `yaforms.mjs` принимает `YAF_HEADLESS=1`.

## Файлы

| Файл | Что делает |
|---|---|
| `forms.json` | спека опросов: разделы, вопросы, типы, варианты, обязательность |
| `yaapi.mjs` | **рабочий инструмент** — клиент внутреннего API, собирает форму из спеки |
| `yaforms.mjs` | вход в аккаунт и разведка (recon / probe / sniff) — нужен при изменениях UI |
| `shots/` | скриншоты и дампы разведки |
| `_shot.mjs` | снять скриншот произвольной страницы залогиненным браузером |

Сессия живёт в `~/.yandex-forms-profile` (отдельный профиль Chrome; рабочий профиль
не трогаем — он залочен работающим браузером).

## Команды `yaapi.mjs`

```bash
node yaapi.mjs list                        # список форм аккаунта
node yaapi.mjs info <surveyId>             # структура формы (дамп в shots/info.json)
node yaapi.mjs check <спека>               # проверить спеку, ничего не создавая и не открывая браузер
node yaapi.mjs build <спека> [--publish]   # собрать форму
node yaapi.mjs insert <surveyId> <спека> [--section N --take N --page N --position N]
                                           # дописать вопросы в живую форму, ссылка та же
node yaapi.mjs publish <surveyId>          # опубликовать
node yaapi.mjs delete <surveyId>           # удалить
```

### `insert` — правка уже разосланной формы

Пересборка даёт новый `surveyId`, то есть новую ссылку: разосланная умирает, собранные ответы
остаются в старой форме. Поэтому вопросы в живую форму **дописываются**, а не пересобираются:

```bash
# первые 2 вопроса раздела 1 спеки students → в начало страницы 1 живой формы
node yaapi.mjs insert 6a7b3631… students --section 1 --take 2
```

`--section` — из какого раздела спеки брать вопросы (по умолчанию 1), `--take` — сколько первых
(без флага — весь раздел), `--page`/`--position` — куда класть в форме (по умолчанию в самое
начало). Сервер сам сдвигает остальные вопросы вниз. В ответе — `headAfter`: заголовки начала
страницы после вставки (`*` = обязательный), это и есть сверка порядка.

**Правило:** сначала та же правка в `forms.json`, потом `insert` в живую форму — иначе спека
и форма разъедутся, и следующая пересборка потеряет вопросы.

`<спека>` — три формы записи:

```bash
node yaapi.mjs build students                    # ключ из общего forms.json
node yaapi.mjs build /путь/опрос.json            # свой файл с одной спекой
node yaapi.mjs build /путь/опросы.json#icp       # файл-словарь, нужный ключ через #
```

Агентам — второе: своя спека в рабочем пространстве команды, без правки общего
`forms.json`. Стартап-команда ходит сюда через
`.claude/skills/startup-lab/references/surveys.md` (там же правила хорошего опроса).

`check` валидирует спеку до похода в сеть и называет все проблемы разом: вопрос без
`title`, `radio`/`checkbox`/`select` без `options[]`, раздел без вопросов.

## Формат спеки

```jsonc
{
  "ключ": {
    "title": "Название формы",
    "description": "Подзаголовок",
    "sections": [                      // раздел = отдельная страница формы
      {
        "title": "Название раздела",
        "questions": [
          { "type": "radio", "required": true,
            "title": "Вопрос?", "options": ["А", "Б"] }
        ]
      }
    ]
  }
}
```

## Типы вопросов

| Ключ в спеке | В конструкторе | Что уходит в API |
|---|---|---|
| `text` | Короткий текст | `type=text view=textinput` |
| `textarea` | Длинный текст | `type=text view=textarea` |
| `number` | Число | `type=text view=textinput validator=decimal` |
| `email` / `phone` / `link` | Почта / Телефон / Ссылка | `validator=email\|phone\|url` |
| `radio` | Один вариант | `type=choices view=radio` |
| `checkbox` | Несколько вариантов | `type=choices view=checks` |
| `select` | Выпадающий список | `type=choices view=select` |
| `boolean` | Да/Нет | `type=boolean` |
| `date` | Дата | `type=date` |
| `scale` | Оценка по шкале | `type=matrix` + `rows[]`, `columns[]` |

## Внутренний API (что удалось снять)

Все вызовы — `POST https://forms.yandex.ru/admin/gateway/root/form/<операция>`
с заголовками `x-csrf-token`, `x-sdk: 1`, `x-use-collab: 1`, `content-type: application/json`.

| Операция | Тело | Ответ |
|---|---|---|
| `createFormFromTemplate` | `{templateId:"empty_form_v2"}` | `{id}` — surveyId |
| `updateSurveyInfo` | `{surveyId, surveyInfo:{name, description}}` | — |
| `getSurveyInfo` | `{surveyId}` | вся форма, включая `questions.pages[]` |
| `addPage` | `{surveyId}` | `{id, page, items}` |
| `addSurveyQuestion` | `{surveyId, page, position, question:{…}}` | вопрос с серверными id |
| `updateSurveyQuestion` | `{surveyId, question:{…полный объект…, required:true}}` | обновлённый вопрос |
| `updateEnumItems` | `{surveyId, questionId, items:[{id, slug, label}]}` | варианты ответа |
| `publishSurvey` | `{surveyId}` | пусто, форма становится публичной |
| `deleteSurvey` | `{surveyId}` | пусто |
| `getForms` | `{page, pageSize}` | список форм |

Ссылка на заполнение после публикации: `https://forms.yandex.ru/u/<surveyId>/`,
редактирование: `https://forms.yandex.ru/admin/<surveyId>/edit`.

### Грабли

- **id вопросов и вариантов генерирует клиент** (большие числа), сервер возвращает свои.
  Ключи обязаны быть уникальными в пределах формы — иначе `duplicate value`.
- **Пустая форма приходит с одной готовой страницей.** Её берём под первый раздел,
  остальные добавляем `addPage`, иначе первая останется пустой.
- **`required` ставится вторым запросом**: сначала `addSurveyQuestion`, потом
  `updateSurveyQuestion` с полным объектом из ответа плюс `required:true`.
- **csrf-токен** живёт в HTML страницы (`window.__DATA__`), один на загрузку;
  в рамках одной сессии переиспользуется без проблем.
- Заголовки страниц (`Страница 1`) через API не задавались — в UI это не отдельная
  операция; если понадобится, снимать заново через `yaforms.mjs sniffall`.

## Если Яндекс поменяет API

```bash
YAF_HEADLESS=1 node yaforms.mjs sniffall      # прокликать все возможности и снять вызовы
YAF_HEADLESS=1 node yaforms.mjs probe '{"url":"…/edit","type":"Один вариант"}'
```
Дампы лягут в `shots/sniff-all.json` и `shots/probe.json` — по ним правится `yaapi.mjs`.

## Сделанные формы (проект «Дубль»)

| Опрос | Заполнение | Редактирование |
|---|---|---|
| Ученики, 39 вопросов, 9 страниц | https://forms.yandex.ru/u/6a7a54be6d2d73df2bffb75a/ | https://forms.yandex.ru/admin/6a7a54be6d2d73df2bffb75a/edit |
| Авторы, 37 вопросов, 7 страниц | https://forms.yandex.ru/u/6a7a54f995add5b3cf0b401d/ | https://forms.yandex.ru/admin/6a7a54f995add5b3cf0b401d/edit |
