#!/usr/bin/env bash
# Установка cron-сервиса Personal OS как launchd LaunchAgent.
# Сервис крутится в фоне и при логине пользователя — работает, даже когда
# Claude Code закрыт (Mac должен быть включён).
set -euo pipefail

PROJECT_DIR="/Users/dimitrisimonyan/Desktop/personal os/services/cron"
PLIST_NAME="com.dimitri.poscron.plist"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_SRC="${PROJECT_DIR}/deploy/${PLIST_NAME}"
PLIST_DST="${AGENTS_DIR}/${PLIST_NAME}"

echo "==> Подготовка"
mkdir -p "${PROJECT_DIR}/logs" "${AGENTS_DIR}"

echo "==> Установка LaunchAgent"
launchctl unload "${PLIST_DST}" 2>/dev/null || true
cp "${PLIST_SRC}" "${PLIST_DST}"
launchctl load "${PLIST_DST}"

echo "==> Статус"
launchctl list | grep poscron || echo "(пока не в списке — проверь logs/launchd.err.log)"

echo "Готово. Планировщик запущен и будет авто-стартовать при логине."
echo "Логи:    ${PROJECT_DIR}/logs/cron.log"
echo "Задачи:  ${PROJECT_DIR}/cronctl.py list"
echo
echo "Совет: для задач-промптов нужен поднятый claude-local-api"
echo "       (services/claude-local-api-main). Проверка: ./cronctl.py status"
