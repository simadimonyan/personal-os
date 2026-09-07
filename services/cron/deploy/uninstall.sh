#!/usr/bin/env bash
# Удаление cron-сервиса Personal OS из launchd.
set -euo pipefail

PLIST_NAME="com.dimitri.poscron.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

launchctl unload "${PLIST_DST}" 2>/dev/null || true
rm -f "${PLIST_DST}"
echo "Сервис остановлен и удалён из автозапуска."
echo "Файлы задач (jobs.json) и логи оставлены на месте."
