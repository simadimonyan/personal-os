#!/usr/bin/env bash
# Ежедневный синк базы чатов фермы: диалоги всех аккаунтов → chats.db.
# Запускается кроном (задача tg-chats-sync). Руками — тоже можно.
#
# По воскресеньям идёт глубокий проход (limit 5000): обычный забирает только
# свежие диалоги, а полный нужен, чтобы заметить покинутые и удалённые чаты.
#
# В stdout — одна строка сводки (её кладёт в лог крон), логи gramjs глушим:
# они идут в stderr и утопили бы отчёт.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE="${NODE_BIN:-$(command -v node || echo /opt/homebrew/bin/node)}"

LIMIT="${1:-500}"
if [ -z "${1:-}" ] && [ "$(date +%u)" = "7" ]; then
  LIMIT=5000   # воскресенье — полный проход
fi

OUT="$("$NODE" "$HERE/driver.cjs" db_sync "{\"limit\":$LIMIT}" 2>/dev/null)"
CODE=$?

if [ $CODE -ne 0 ] || [ -z "$OUT" ]; then
  echo "синк базы чатов упал (код $CODE)"
  [ -n "$OUT" ] && echo "$OUT"
  exit 1
fi

# Сводка одной строкой + перечисление аккаунтов; ошибка любого аккаунта роняет
# задачу, чтобы она попала в «крон с ошибкой» на старте сессии.
"$NODE" -e '
let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
  const r = JSON.parse(s);
  const per = (r.synced || []).map(x => x.error ? `${x.account}: ОШИБКА ${x.error}` : `${x.account}: ${x.chats}`);
  console.log(`база чатов: ${r.chats} чатов, ${r.messages} сообщений в кэше · ${per.join(" · ")}`);
  const q = (r.queue || []).find(x => x.status === "pending");
  if (q) console.log(`в очереди ждёт заходов: ${q.n} (queue_run)`);
  process.exit((r.synced || []).some(x => x.error) ? 1 : 0);
});' <<< "$OUT"
