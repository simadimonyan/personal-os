#!/usr/bin/env bash
# Подключение пачки Telegram-аккаунтов к ферме — по одному, с паузами.
#
#   bash ~/.claude/skills/telegram/scripts/add-farm.sh acc4 acc5 acc6
#   bash ~/.claude/skills/telegram/scripts/add-farm.sh          # метки спросит по ходу
#
# Запускать ИНТЕРАКТИВНО (в Claude Code — через префикс `!`): на каждый аккаунт
# спрашивается номер, код из Telegram и 2FA-пароль. Ввести их может только владелец.
#
# Пауза между аккаунтами — против проверки Telegram на серию логинов с одного IP.

set -u
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAUSE="${FARM_PAUSE:-90}"   # секунд между аккаунтами, FARM_PAUSE=0 чтобы отключить

labels=("$@")

connect_one() {
  local label="$1"
  echo
  echo "──────────────────────────────────────────────"
  echo "  Аккаунт: $label"
  echo "──────────────────────────────────────────────"
  node "$SKILL_DIR/auth.cjs" add "$label"
  return $?
}

pause_between() {
  [ "$PAUSE" -eq 0 ] && return 0
  echo
  echo "Пауза $PAUSE с перед следующим аккаунтом (Ctrl+C — остановиться)…"
  for ((i=PAUSE; i>0; i--)); do
    printf "\r  осталось %3d с " "$i"
    sleep 1
  done
  printf "\r                    \r"
}

if [ ${#labels[@]} -gt 0 ]; then
  # Метки заданы аргументами — идём по списку
  total=${#labels[@]}
  for i in "${!labels[@]}"; do
    connect_one "${labels[$i]}" || echo "⚠️  ${labels[$i]}: не подключён, идём дальше"
    [ $((i + 1)) -lt "$total" ] && pause_between
  done
else
  # Меток нет — спрашиваем по одной, пока не введут пустую строку
  while true; do
    echo
    read -r -p "Метка следующего аккаунта (Enter — закончить): " label
    [ -z "$label" ] && break
    connect_one "$label" || echo "⚠️  $label: не подключён, идём дальше"
    pause_between
  done
fi

echo
echo "═══ Ферма после подключения ═══"
node "$SKILL_DIR/auth.cjs" list
echo
echo "Проверить живость всех сессий:  node $SKILL_DIR/auth.cjs check"
echo "Назначить роль:                 node $SKILL_DIR/auth.cjs set <label> role рассылки"
