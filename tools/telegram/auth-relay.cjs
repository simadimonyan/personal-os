#!/usr/bin/env node
'use strict';
/**
 * Неинтерактивная авторизация аккаунта фермы — вход в три шага,
 * когда код подтверждения приходит владельцу, а команды выполняет агент.
 *
 *   node auth-relay.cjs start <label> <phone>     запросить код (Telegram пришлёт его в приложение)
 *   node auth-relay.cjs code <label> <code>       ввести код
 *   node auth-relay.cjs password <label> <pass>   ввести пароль 2FA (если попросили)
 *   node auth-relay.cjs status                    какие входы не доведены до конца
 *   node auth-relay.cjs cancel <label>            бросить недоведённый вход
 *
 * Отличие от auth.cjs: тот спрашивает всё через stdin и работает только в живом
 * терминале. Здесь состояние между шагами лежит в .auth-pending.json (chmod 600),
 * поэтому шаги можно разносить по разным вызовам.
 *
 * Вывод — JSON в stdout, одной строкой. Логи gramjs уводятся в stderr:
 * иначе они перемешиваются с результатом (см. references/chats-db.md, та же грабля).
 */

const fs = require('fs');
const path = require('path');
const { TelegramClient, Api } = require('telegram');
const { StringSession } = require('telegram/sessions');
const { computeCheck } = require('telegram/Password');
const store = require('./accounts.cjs');

// gramjs пишет INFO через console.log — в stdout ему нельзя
console.log = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');

const PENDING_PATH = path.join(__dirname, '.auth-pending.json');
const env = store.loadEnv();
const API_ID = parseInt(env.TELEGRAM_API_ID);
const API_HASH = env.TELEGRAM_API_HASH;

function emit(obj) {
  return new Promise(resolve => process.stdout.write(JSON.stringify(obj, null, 2) + '\n', resolve));
}

function readPending() {
  if (!fs.existsSync(PENDING_PATH)) return {};
  try { return JSON.parse(fs.readFileSync(PENDING_PATH, 'utf8')); } catch (e) { return {}; }
}

function writePending(data) {
  fs.writeFileSync(PENDING_PATH, JSON.stringify(data, null, 2));
  try { fs.chmodSync(PENDING_PATH, 0o600); } catch (e) { /* best effort */ }
}

function savePending(label, entry) {
  const all = readPending();
  all[label] = { ...(all[label] || {}), ...entry, updatedAt: new Date().toISOString() };
  writePending(all);
}

function dropPending(label) {
  const all = readPending();
  delete all[label];
  writePending(all);
}

function requireCreds() {
  if (!API_ID || !API_HASH) throw new Error('В .env нет TELEGRAM_API_ID / TELEGRAM_API_HASH');
}

async function connect(session) {
  const client = new TelegramClient(new StringSession(session || ''), API_ID, API_HASH, {
    connectionRetries: 3,
  });
  await client.connect();
  return client;
}

/** Шаг 1: запросить код на номер. */
async function cmdStart(label, phone) {
  requireCreds();
  if (!label || !phone) throw new Error('Использование: auth-relay.cjs start <label> <phone>');
  if (!phone.startsWith('+')) throw new Error('Номер — в международном формате, с + (например +79991234567)');

  const existing = store.readStore().accounts[label];
  const client = await connect('');
  const { phoneCodeHash, isCodeViaApp } = await client.sendCode({ apiId: API_ID, apiHash: API_HASH }, phone);

  // Сессию сохраняем уже после sendCode: в ней ключ авторизации и, если Telegram
  // перекинул номер на другой дата-центр, уже верный DC. Без неё phoneCodeHash
  // на следующем шаге не примут.
  savePending(label, { phone, phoneCodeHash, session: client.session.save(), stage: 'code' });
  await client.disconnect().catch(() => {});

  return {
    ok: true,
    stage: 'code',
    label,
    phone,
    codeSentTo: isCodeViaApp ? 'приложение Telegram' : 'SMS',
    overwrites: existing ? `@${existing.username || existing.id}` : null,
    next: `node auth-relay.cjs code ${label} <код>`,
  };
}

/** Шаг 2: ввести код. */
async function cmdCode(label, code) {
  requireCreds();
  const pending = readPending()[label];
  if (!pending) throw new Error(`Нет начатого входа для "${label}". Сначала: auth-relay.cjs start ${label} <phone>`);
  if (!code) throw new Error('Использование: auth-relay.cjs code <label> <код>');

  const client = await connect(pending.session);
  try {
    await client.invoke(new Api.auth.SignIn({
      phoneNumber: pending.phone,
      phoneCodeHash: pending.phoneCodeHash,
      phoneCode: String(code).replace(/\D/g, ''),
    }));
  } catch (err) {
    if (err.errorMessage === 'SESSION_PASSWORD_NEEDED') {
      savePending(label, { session: client.session.save(), stage: 'password' });
      await client.disconnect().catch(() => {});
      return {
        ok: true,
        stage: 'password',
        label,
        message: 'Код принят, на аккаунте включена двухэтапная проверка — нужен пароль 2FA',
        next: `node auth-relay.cjs password ${label} <пароль>`,
      };
    }
    await client.disconnect().catch(() => {});
    throw err;
  }

  return finish(client, label);
}

/** Шаг 3: пароль двухэтапной проверки. */
async function cmdPassword(label, password) {
  requireCreds();
  const pending = readPending()[label];
  if (!pending) throw new Error(`Нет начатого входа для "${label}".`);
  if (!password) throw new Error('Использование: auth-relay.cjs password <label> <пароль>');

  const client = await connect(pending.session);
  try {
    const pwdInfo = await client.invoke(new Api.account.GetPassword());
    await client.invoke(new Api.auth.CheckPassword({
      password: await computeCheck(pwdInfo, password),
    }));
  } catch (err) {
    await client.disconnect().catch(() => {});
    throw err;
  }
  return finish(client, label);
}

/** Общий хвост успешного входа: записать аккаунт в ферму. */
async function finish(client, label) {
  const me = await client.getMe();
  const session = client.session.save();
  store.upsertAccount(label, {
    phone: me.phone ? '+' + me.phone : readPending()[label]?.phone,
    session,
    username: me.username,
    id: me.id?.toString(),
    firstName: me.firstName,
    status: 'active',
    lastCheck: new Date().toISOString(),
    lastError: null,
    addedAt: new Date().toISOString(),
  });
  dropPending(label);
  await client.disconnect().catch(() => {});
  return {
    ok: true,
    stage: 'done',
    label,
    username: me.username || null,
    firstName: me.firstName || null,
    id: me.id?.toString(),
    phone: me.phone ? '+' + me.phone : null,
    default: store.readStore().default,
  };
}

function cmdStatus() {
  const all = readPending();
  const items = Object.entries(all).map(([label, p]) => ({
    label, phone: p.phone, stage: p.stage, updatedAt: p.updatedAt,
  }));
  return { ok: true, pending: items };
}

function cmdCancel(label) {
  dropPending(label);
  return { ok: true, cancelled: label };
}

async function main() {
  const [, , cmd, a1, a2] = process.argv;
  switch (cmd) {
    case 'start': return cmdStart(a1, a2);
    case 'code': return cmdCode(a1, a2);
    // slice(4), а не (3): argv[3] — это метка аккаунта. Срез — ради паролей с пробелами.
    case 'password': return cmdPassword(a1, process.argv.slice(4).join(' '));
    case 'status': return cmdStatus();
    case 'cancel': return cmdCancel(a1);
    default:
      return {
        ok: false,
        usage: [
          'auth-relay.cjs start <label> <phone>',
          'auth-relay.cjs code <label> <code>',
          'auth-relay.cjs password <label> <pass>',
          'auth-relay.cjs status',
          'auth-relay.cjs cancel <label>',
        ],
      };
  }
}

main()
  .then(async (res) => { await emit(res); process.exit(res && res.ok === false ? 1 : 0); })
  .catch(async (err) => {
    await emit({ ok: false, error: err.errorMessage || err.message, seconds: err.seconds });
    process.exit(1);
  });
