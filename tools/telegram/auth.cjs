#!/usr/bin/env node
'use strict';
/**
 * Interactive multi-account auth for the Telegram skill.
 *
 * Usage:
 *   node auth.cjs add [label]        Log into a new account (phone → code → 2FA)
 *   node auth.cjs list               Show configured accounts
 *   node auth.cjs default <label>    Set the active default account
 *   node auth.cjs remove <label>     Delete an account from the store
 *   node auth.cjs import-legacy [label]   Import the existing .env session as an account
 *   node auth.cjs set <label> <field> <value>   role | note | tags | status
 *   node auth.cjs check [label]      Проверить живость сессий (все или одну)
 *
 * The login code arrives in YOUR Telegram app — you must type it here.
 * API_ID / API_HASH are shared and read from .env.
 */

const readline = require('readline');
const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions');
const store = require('./accounts.cjs');

const env = store.loadEnv();
const API_ID = parseInt(env.TELEGRAM_API_ID);
const API_HASH = env.TELEGRAM_API_HASH;

function ask(question, { hidden = false } = {}) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => {
    if (hidden) {
      const stdout = process.stdout;
      rl._writeToOutput = function (str) {
        if (str.includes(question)) stdout.write(str);
        else stdout.write('*');
      };
    }
    rl.question(question, answer => { rl.close(); resolve(answer.trim()); });
  });
}

async function addAccount(label) {
  if (!API_ID || !API_HASH) {
    console.error('TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env'); process.exit(1);
  }
  if (!label) label = await ask('Метка аккаунта (например work, personal, second): ');
  if (!label) { console.error('Метка обязательна.'); process.exit(1); }

  const existing = store.readStore().accounts[label];
  if (existing) {
    const ok = await ask(`Аккаунт "${label}" уже есть (@${existing.username}). Перезаписать? [y/N]: `);
    if (ok.toLowerCase() !== 'y') { console.log('Отменено.'); process.exit(0); }
  }

  const client = new TelegramClient(new StringSession(''), API_ID, API_HASH, { connectionRetries: 3 });

  await client.start({
    phoneNumber: async () => await ask('Номер телефона (с +, напр. +79991234567): '),
    password: async () => await ask('Пароль 2FA (если включён, иначе Enter): ', { hidden: true }),
    phoneCode: async () => await ask('Код из Telegram: '),
    onError: (err) => console.error('Ошибка авторизации:', err.message),
  });

  const me = await client.getMe();
  const session = client.session.save();
  store.upsertAccount(label, {
    phone: me.phone ? '+' + me.phone : undefined,
    session,
    username: me.username,
    id: me.id?.toString(),
    firstName: me.firstName,
    addedAt: new Date().toISOString(),
  });

  console.log(`\n✅ Авторизован: ${label} → @${me.username || me.firstName} (id ${me.id})`);
  console.log(`Активный по умолчанию: ${store.readStore().default}`);
  await client.disconnect().catch(() => {});
  process.exit(0);
}

function listAccounts() {
  const s = store.readStore();
  const labels = Object.keys(s.accounts);
  if (!labels.length) {
    const env2 = store.loadEnv();
    if (env2.TELEGRAM_SESSION) {
      console.log('Аккаунтов в accounts.json нет, но есть legacy-сессия в .env.');
      console.log('Импортируй её: node auth.cjs import-legacy main');
    } else {
      console.log('Аккаунтов нет. Добавь: node auth.cjs add <label>');
    }
    return;
  }
  console.log('Ферма аккаунтов:');
  for (const label of labels) {
    const a = s.accounts[label];
    const star = s.default === label ? ' ★(default)' : '';
    const role = a.role ? ` · роль: ${a.role}` : '';
    const status = ` · ${a.status || 'unknown'}`;
    const note = a.note ? `\n      ${a.note}` : '';
    console.log(`  ${label}${star} — @${a.username || a.firstName || '?'} ${a.phone || ''} (id ${a.id || '?'})${role}${status}${note}`);
  }
}

function setMeta(label, field, value) {
  if (!label || !field || value === undefined) {
    console.error(`Использование: node auth.cjs set <label> <${store.META_FIELDS.join('|')}> <значение>`);
    process.exit(1);
  }
  const acc = store.setMeta(label, field, value);
  console.log(`${label}: ${field} = ${JSON.stringify(acc[field])}`);
}

async function checkAccounts(label) {
  // gramjs сыплет INFO-логи через console.log — уводим их в stderr,
  // чтобы отчёт проверки читался без шума
  console.log = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
  const say = (s) => process.stdout.write(s + '\n');

  const { checkAccounts: check } = require('./farm.cjs');
  const results = await check(label ? [label] : null);
  for (const r of results) {
    if (r.ok) say(`  ✅ ${r.label} — @${r.username} (${r.status})`);
    else say(`  ❌ ${r.label} — ${r.error}`);
  }
  const dead = results.filter(r => !r.ok);
  if (dead.length) {
    say(`\nМёртвые сессии (${dead.length}) переавторизовать: node auth.cjs add <label>`);
  }
  process.exit(0);
}

async function importLegacy(label) {
  const env2 = store.loadEnv();
  if (!env2.TELEGRAM_SESSION) { console.error('В .env нет TELEGRAM_SESSION.'); process.exit(1); }
  if (!label) label = 'main';
  // Verify the session and pull account info
  const client = new TelegramClient(new StringSession(env2.TELEGRAM_SESSION), API_ID, API_HASH, { connectionRetries: 3 });
  await client.connect();
  const me = await client.getMe();
  store.upsertAccount(label, {
    phone: me.phone ? '+' + me.phone : undefined,
    session: env2.TELEGRAM_SESSION,
    username: me.username,
    id: me.id?.toString(),
    firstName: me.firstName,
    addedAt: new Date().toISOString(),
  });
  console.log(`✅ Импортирован legacy → ${label}: @${me.username || me.firstName} (id ${me.id})`);
  await client.disconnect().catch(() => {});
  process.exit(0);
}

async function main() {
  const [,, cmd, arg, arg2, ...rest] = process.argv;
  switch (cmd) {
    case 'set': return setMeta(arg, arg2, rest.join(' '));
    case 'check': return checkAccounts(arg);
    case 'add': return addAccount(arg);
    case 'list': return listAccounts();
    case 'default':
      if (!arg) { console.error('Укажи метку: node auth.cjs default <label>'); process.exit(1); }
      store.setDefault(arg); console.log(`Активный аккаунт: ${arg}`); return;
    case 'remove':
      if (!arg) { console.error('Укажи метку: node auth.cjs remove <label>'); process.exit(1); }
      store.removeAccount(arg); console.log(`Удалён: ${arg}`); listAccounts(); return;
    case 'import-legacy': return importLegacy(arg);
    default:
      console.log('Команды: add [label] | list | check [label] | set <label> <поле> <значение> | default <label> | remove <label> | import-legacy [label]');
      console.log(`Поля для set: ${store.META_FIELDS.join(', ')} (status: ${store.STATUSES.join(' | ')})`);
  }
}

main().catch(err => { console.error('Ошибка:', err.message); process.exit(1); });
