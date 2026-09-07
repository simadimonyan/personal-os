#!/usr/bin/env bash
# Удаление assistant LaunchAgent. Данные (data/, vault) НЕ трогаются.
set -euo pipefail

PLIST_NAME="com.dimitri.assistant.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

echo "==> Остановка и выгрузка"
launchctl unload "${PLIST_DST}" 2>/dev/null || true

if [[ -f "${PLIST_DST}" ]]; then
  rm "${PLIST_DST}"
  echo "Удалён ${PLIST_DST}"
else
  echo "LaunchAgent не найден — возможно уже удалён."
fi

echo "Готово. SQLite (data/) и записи в Obsidian сохранены."
