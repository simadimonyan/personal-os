#!/usr/bin/env bash
# Проход очереди заходов: берёт из queue столько чатов, сколько влезает в суточный
# бюджет аккаунта, и заходит в них с паузами. Запускается кроном (задача tg-queue-run)
# дважды в день; руками — тоже можно.
#
#   bash queue-run.sh [аккаунт] [сколько за проход]
#
# В stdout — одна строка сводки (её кладёт в лог крон), логи gramjs глушим:
# они идут в stderr и утопили бы отчёт.
#
# Аккаунт в статусе cooldown/limited/banned очередь не трогает — это не ошибка,
# а штатная пауза после флуд-вейта; задача при этом не падает.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE="${NODE_BIN:-$(command -v node || echo /opt/homebrew/bin/node)}"

ACCOUNT="${1:-acc7}"
LIMIT="${2:-2}"

OUT="$("$NODE" "$HERE/driver.cjs" queue_run "{\"account\":\"$ACCOUNT\",\"limit\":$LIMIT}" 2>/dev/null)"
CODE=$?

if [ $CODE -ne 0 ] || [ -z "$OUT" ]; then
  echo "проход очереди ($ACCOUNT) упал (код $CODE)"
  [ -n "$OUT" ] && echo "$OUT"
  exit 1
fi

# Сводка: что сделано, что осталось. Флуд-вейт печатается предупреждением —
# аккаунт уже уведён в cooldown самим драйвером, ронять задачу незачем.
"$NODE" -e '
let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
  const r = JSON.parse(s);
  if (r.note) { console.log(`очередь ${r.account}: ${r.note}`); return process.exit(0); }
  const done = r.done || [];
  const ok = done.filter(x => x.ok), bad = done.filter(x => !x.ok);
  const names = ok.map(x => `${x.target}${x.title ? ` (${x.title})` : ""}`).join(", ");
  const b = r.budget && r.budget.join;
  console.log(`очередь ${r.account}: ${ok.length} зашёл${names ? " — " + names : ""}` +
    (bad.length ? ` · ошибок ${bad.length}: ${bad.map(x => `${x.target} — ${x.error}`).join("; ")}` : "") +
    (b ? ` · бюджет заходов ${b.used}/${b.limit}` : "") +
    ` · в очереди осталось ${r.pending}`);
  (r.warnings || []).forEach(w => console.log(`⚠️ ${w}`));
  process.exit(0);
});' <<< "$OUT"
