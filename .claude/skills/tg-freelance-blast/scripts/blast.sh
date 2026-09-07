#!/bin/bash
# tg-freelance-blast — рассылка одного сообщения по списку Telegram-чатов с паузами.
#
# Использование:
#   bash blast.sh <msg_file> <chats_file> [min_delay] [max_delay]
#
#   msg_file   — путь к txt с текстом сообщения (UTF-8, может содержать эмодзи/переносы)
#   chats_file — путь к txt со списком чатов, по одному в строке в формате:  <chat_id>|<имя>
#                строки, начинающиеся с # — пропускаются (комментарии)
#   min_delay  — минимальная пауза между отправками, сек (по умолчанию 40)
#   max_delay  — максимальная пауза между отправками, сек (по умолчанию 60)
#
# Лог пишется в /tmp/tg_blast.log. Корректно отличает успех ("success": true) от ошибки.
set -u

DRIVER="$HOME/.claude/skills/telegram/driver.cjs"
MSG_FILE="${1:?нужен путь к файлу сообщения}"
CHATS_FILE="${2:?нужен путь к файлу списка чатов}"
MIN_DELAY="${3:-40}"
MAX_DELAY="${4:-60}"
LOG=/tmp/tg_blast.log
: > "$LOG"

if ! command -v jq >/dev/null; then
  echo "ERROR: требуется jq (brew install jq)" | tee -a "$LOG"; exit 1
fi
MSG=$(cat "$MSG_FILE")

# собрать чаты в массив, пропуская комментарии и пустые строки
CHATS=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  CHATS+=("$line")
done < "$CHATS_FILE"

total=${#CHATS[@]}
[ "$total" -eq 0 ] && { echo "ERROR: список чатов пуст" | tee -a "$LOG"; exit 1; }

range=$((MAX_DELAY - MIN_DELAY + 1)); [ "$range" -lt 1 ] && range=1
ok=0; fail=0; i=0
for entry in "${CHATS[@]}"; do
  i=$((i+1))
  id="${entry%%|*}"
  name="${entry#*|}"
  payload=$(jq -nc --arg cid "$id" --arg t "$MSG" '{chat_id:$cid,text:$t}')
  out=$(node "$DRIVER" send_message "$payload" 2>>"$LOG")
  if echo "$out" | grep -Eq '"success":[[:space:]]*true'; then
    mid=$(echo "$out" | grep -Eo '"messageId":[[:space:]]*[0-9]+' | grep -Eo '[0-9]+')
    ok=$((ok+1))
    echo "[$i/$total] OK   $name (msgId=$mid)" | tee -a "$LOG"
  else
    fail=$((fail+1))
    err=$(echo "$out" | tr '\n' ' ' | grep -Eo '"error":"[^"]*"' | head -1)
    echo "[$i/$total] FAIL $name :: ${err:-$out}" | tee -a "$LOG"
  fi
  if [ "$i" -lt "$total" ]; then
    sleep $((MIN_DELAY + RANDOM % range))
  fi
done
echo "=== DONE: отправлено $ok/$total, ошибок $fail ===" | tee -a "$LOG"
