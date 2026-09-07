# context-store — визуальные референсы Personal OS

Движок хранилища «бордов» (доски референсов как в Pinterest) с двумя полярностями:
**Нравится** (do's) и **Не нравится** (don'ts). Код — здесь, данные — в Obsidian:
`08 — Шаблоны и ресурсы/Визуальные референсы/`.

Зависимостей нет (только stdlib) — работает под супервизором pos.

## CLI

```bash
store.py manifest                                      # пересобрать _store.json
store.py board-create <slug> --name "…" --category "…" [--desc "…"] [--tags a,b]
store.py add-url   <slug> <like|avoid> <url> [url …]   # скачать по ссылкам
store.py add-file  <slug> <like|avoid> <path> [path …] # скопировать файлы
store.py add-b64   <slug> <like|avoid> <name> <base64> # загрузка из UI
store.py ingest-pinterest <slug> <like|avoid> <url> [--limit N]  # борд/пин пачкой
store.py list | get <query>
store.py delete-image <slug> <like|avoid> <file> | delete-board <slug>
```

Корень переопределяется env `CONTEXT_STORE_ROOT`.

## Интеграции

- **mission-control** — раздел «Хранилище контекстов → Изображения»: галерея бордов,
  панель добавления (ссылки / Pinterest / загрузка), кнопка «визуальное ДНК».
  Роуты: `GET /api/refs`, `GET /refs/img?path=`, `POST /api/refs/{board-create,add-url,add-pinterest,add-file,delete}`.
- **ui-designer-web / ui-designer-mobile** — читают `_store.json` перед выбором стиля.

## Грабли

- **Владелец файлов.** mission-control работает под пользователем; если создать данные
  из root-шелла — сервер не сможет писать (EPERM). Данные всегда создаются сервером
  (как user) либо `chown -R dimitrisimonyan:staff` после ручного вмешательства.
- **Pinterest.** Публичные борды парсятся best-effort из HTML (`i.pinimg.com` → originals).
  JS-only/приватные вернут 0 — тогда вставляй прямые URL картинок или загружай файлы.
- **TLS под VPN.** `_fetch` пробует проверенный контекст (truststore→certifi), при
  провале верификации — качает без проверки (публичные картинки, не секреты).
