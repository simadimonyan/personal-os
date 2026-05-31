---
name: hh
description: "Работа с hh.ru: поиск вакансий, отклик на вакансии, управление резюме, просмотр откликов. Используй когда просят найти работу, откликнуться на вакансию, проверить отклики на hh.ru."
---

# HH.ru — инструкция для агента

Инструменты запускаются через driver напрямую — без MCP-сервера. Каждый вызов открывает браузер, выполняет действие, закрывает браузер.

**Driver:** `~/.claude/skills/hh/driver.mjs`

```bash
node ~/.claude/skills/hh/driver.mjs <tool_name> '<json_args>'
```

---

## Инструменты

### hh_check_auth
Проверить статус авторизации на hh.ru.
```bash
node ~/.claude/skills/hh/driver.mjs hh_check_auth
# → true | false
```

### hh_login
Войти на hh.ru. Открывается видимый браузер — пользователь решает капчу вручную, затем вызывает hh_save_session.
```bash
node ~/.claude/skills/hh/driver.mjs hh_login '{"login":"email@mail.ru","password":"pass"}'
```

### hh_save_session
Сохранить сессию после ручной авторизации. Cookies → `~/.hh-mcp/auth-state.json`.
```bash
node ~/.claude/skills/hh/driver.mjs hh_save_session
```

### hh_search_vacancies
Поиск вакансий. Возвращает массив с id, title, company, salary, location, url.
```bash
node ~/.claude/skills/hh/driver.mjs hh_search_vacancies '{"query":"Python разработчик","area":"1","experience":"between1And3","schedule":"remote","perPage":10}'
```
Параметры: `query`\*, `area` (1=Москва, 2=СПб, 113=Россия), `salary`, `experience` (noExperience/between1And3/between3And6/moreThan6), `employment` (full/part/project), `schedule` (fullDay/flexible/remote), `page`, `perPage` (макс 20).

### hh_get_vacancy
Полные детали вакансии: описание, требования, контакты.
```bash
node ~/.claude/skills/hh/driver.mjs hh_get_vacancy '{"vacancy_id":"123456789"}'
```

### hh_get_resumes
Список резюме текущего пользователя.
```bash
node ~/.claude/skills/hh/driver.mjs hh_get_resumes
# → [{id, title, status, updatedAt, url}]
```

### hh_get_resume_text
Полный текст резюме для анализа и написания сопроводительного письма.
```bash
node ~/.claude/skills/hh/driver.mjs hh_get_resume_text '{"resume_id":"abc123def456"}'
```

### hh_apply
Откликнуться на вакансию.
```bash
node ~/.claude/skills/hh/driver.mjs hh_apply '{"vacancy_id":"123456789","resume_id":"abc123","cover_letter":"Здравствуйте, меня заинтересовала ваша вакансия..."}'
```

### hh_get_applications
Список всех откликов и их статус.
```bash
node ~/.claude/skills/hh/driver.mjs hh_get_applications
# → [{title, company, status, date, url}]
```

---

## Типичные workflow

**Поиск и отклик:**
```bash
# 1. Проверить авторизацию
node ~/.claude/skills/hh/driver.mjs hh_check_auth

# 2. Найти вакансии
node ~/.claude/skills/hh/driver.mjs hh_search_vacancies '{"query":"Backend developer","area":"1","experience":"between1And3"}'

# 3. Прочитать детали вакансии
node ~/.claude/skills/hh/driver.mjs hh_get_vacancy '{"vacancy_id":"<id_из_шага_2>"}'

# 4. Получить список резюме
node ~/.claude/skills/hh/driver.mjs hh_get_resumes

# 5. Прочитать резюме для написания письма
node ~/.claude/skills/hh/driver.mjs hh_get_resume_text '{"resume_id":"<resume_id>"}'

# 6. Откликнуться
node ~/.claude/skills/hh/driver.mjs hh_apply '{"vacancy_id":"<id>","resume_id":"<id>","cover_letter":"..."}'
```

---

## Готчи

- Браузер открывается в видимом режиме (headless=false) — на машине без дисплея hh_login не работает.
- Сессия хранится в `~/.hh-mcp/auth-state.json`. Первый запуск требует авторизации.
- Каждый вызов driver запускает новый браузер — медленно, но надёжно. Не вызывай driver в цикле слишком быстро.
- `perPage` максимум 20. Для следующей страницы используй `page: 2`.
