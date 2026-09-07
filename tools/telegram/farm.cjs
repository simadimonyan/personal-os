'use strict';
/**
 * Проверка живости аккаунтов фермы.
 * Общий код для auth.cjs (`check`) и driver.cjs (`check_accounts`).
 *
 * Проверка = подключиться сессией и спросить getMe. Это дешёвый запрос,
 * он не считается активностью-подозрением, но всё же гонять его пачкой
 * чаще раза в сутки смысла нет.
 */

const store = require('./accounts.cjs');

// Сессия жива, но аккаунт мог быть выключен пользователем вручную
// (cooldown/limited/banned) — успешная проверка такие пометки не стирает.
const MANUAL_STATUSES = ['cooldown', 'limited', 'banned'];

async function checkAccounts(labels) {
  const { TelegramClient } = require('telegram');
  const { StringSession } = require('telegram/sessions');

  const env = store.loadEnv();
  const apiId = parseInt(env.TELEGRAM_API_ID);
  const apiHash = env.TELEGRAM_API_HASH;

  const s = store.readStore();
  const targets = (labels && labels.length ? labels : Object.keys(s.accounts));
  const results = [];

  for (const label of targets) {
    const acc = s.accounts[label];
    if (!acc) {
      results.push({ label, ok: false, error: 'нет такого аккаунта' });
      continue;
    }
    const client = new TelegramClient(new StringSession(acc.session), apiId, apiHash, { connectionRetries: 2 });
    let entry;
    try {
      await client.connect();
      const me = await client.getMe();
      const status = MANUAL_STATUSES.includes(acc.status) ? acc.status : 'active';
      store.upsertAccount(label, {
        username: me.username,
        id: me.id?.toString(),
        firstName: me.firstName,
        status,
        lastCheck: new Date().toISOString(),
        lastError: null,
      });
      entry = { label, ok: true, username: me.username, id: me.id?.toString(), status };
    } catch (e) {
      store.upsertAccount(label, {
        status: 'unknown',
        lastCheck: new Date().toISOString(),
        lastError: e.message,
      });
      entry = { label, ok: false, error: e.message, status: 'unknown' };
    }
    await client.disconnect().catch(() => {});
    results.push(entry);
  }

  return results;
}

module.exports = { checkAccounts };
