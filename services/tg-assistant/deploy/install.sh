#!/usr/bin/env bash
# Установка assistant как launchd LaunchAgent (§7 ARCHITECTURE).
# Запускать ПОД GUI-сессией пользователя (нужен доступ к Яндекс.Диску и сети).
set -euo pipefail

PROJECT_DIR="/Users/dimitrisimonyan/Desktop/personal os/services/tg-assistant"
PLIST_NAME="com.dimitri.assistant.plist"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_SRC="${PROJECT_DIR}/deploy/${PLIST_NAME}"
PLIST_DST="${AGENTS_DIR}/${PLIST_NAME}"

echo "==> Проверка окружения"
if [[ ! -d "${PROJECT_DIR}/.venv" ]]; then
  echo "ОШИБКА: ${PROJECT_DIR}/.venv не найден."
  echo "Сначала создай venv и установи зависимости:"
  echo "  python3.11 -m venv ${PROJECT_DIR}/.venv"
  echo "  ${PROJECT_DIR}/.venv/bin/pip install -r ${PROJECT_DIR}/requirements.txt"
  exit 1
fi
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "ОШИБКА: ${PROJECT_DIR}/.env не найден. Скопируй .env.example в .env и заполни BOT_TOKEN/OWNER_ID."
  exit 1
fi

echo "==> Установка LaunchAgent"
mkdir -p "${AGENTS_DIR}"

# выгрузить старую версию, если была
launchctl unload "${PLIST_DST}" 2>/dev/null || true

cp "${PLIST_SRC}" "${PLIST_DST}"
launchctl load "${PLIST_DST}"

echo "==> Статус"
launchctl list | grep assistant || echo "(пока не появился в списке — проверь logs/launchd.err.log)"

echo "Готово. Бот запущен и будет авто-стартовать при логине."
echo "Логи: ${PROJECT_DIR}/logs/bot.log"
