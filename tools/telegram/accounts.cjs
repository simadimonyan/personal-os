'use strict';
/**
 * Account store for the Telegram skill — ферма аккаунтов.
 * Sessions live in accounts.json next to this file:
 *   {
 *     "default": "<label>",
 *     "accounts": {
 *       "<label>": {
 *         "phone": "+7...", "session": "1Ap...", "username": "...", "id": "...",
 *         "addedAt": "ISO",
 *         // метаданные фермы (необязательные, правятся через auth.cjs set):
 *         "role": "личный | рассылки | чтение | резерв | ...",
 *         "note": "для чего этот аккаунт",
 *         "tags": ["blast", "ru"],
 *         "status": "active | cooldown | limited | banned | unknown",
 *         "lastCheck": "ISO", "lastError": "текст последней ошибки проверки"
 *       }
 *     }
 *   }
 * API_ID / API_HASH are shared (app-level) and read from .env.
 */

const path = require('path');
const fs = require('fs');

const STORE_PATH = path.join(__dirname, 'accounts.json');
const ENV_PATH = path.join(__dirname, '.env');

function loadEnv() {
  const out = {};
  if (!fs.existsSync(ENV_PATH)) return out;
  const content = fs.readFileSync(ENV_PATH, 'utf8');
  content.split('\n').forEach(line => {
    const [key, ...rest] = line.split('=');
    if (key && rest.length) out[key.trim()] = rest.join('=').trim();
  });
  return out;
}

function readStore() {
  if (!fs.existsSync(STORE_PATH)) return { default: null, accounts: {} };
  try {
    const parsed = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    if (!parsed.accounts) parsed.accounts = {};
    if (!('default' in parsed)) parsed.default = null;
    return parsed;
  } catch (e) {
    return { default: null, accounts: {} };
  }
}

function writeStore(store) {
  fs.writeFileSync(STORE_PATH, JSON.stringify(store, null, 2));
  try { fs.chmodSync(STORE_PATH, 0o600); } catch (e) { /* best effort */ }
}

function upsertAccount(label, data) {
  const store = readStore();
  store.accounts[label] = { ...(store.accounts[label] || {}), ...data };
  if (!store.default) store.default = label;
  writeStore(store);
  return store;
}

function removeAccount(label) {
  const store = readStore();
  delete store.accounts[label];
  if (store.default === label) {
    const keys = Object.keys(store.accounts);
    store.default = keys.length ? keys[0] : null;
  }
  writeStore(store);
  return store;
}

function setDefault(label) {
  const store = readStore();
  if (!store.accounts[label]) throw new Error(`No such account: ${label}`);
  store.default = label;
  writeStore(store);
  return store;
}

// Поля метаданных фермы, которые можно править командой `auth.cjs set`.
// Всё остальное (session, id, phone) пишется только авторизацией — руками не трогаем.
const META_FIELDS = ['role', 'note', 'tags', 'status'];
const STATUSES = ['active', 'cooldown', 'limited', 'banned', 'unknown'];

function setMeta(label, field, value) {
  const store = readStore();
  if (!store.accounts[label]) throw new Error(`No such account: ${label}`);
  if (!META_FIELDS.includes(field)) {
    throw new Error(`Unknown field "${field}". Available: ${META_FIELDS.join(', ')}`);
  }
  if (field === 'status' && !STATUSES.includes(value)) {
    throw new Error(`Unknown status "${value}". Available: ${STATUSES.join(', ')}`);
  }
  // теги задаются через запятую: "blast, ru" → ["blast","ru"]
  const parsed = field === 'tags'
    ? String(value).split(',').map(s => s.trim()).filter(Boolean)
    : value;
  store.accounts[label][field] = parsed;
  writeStore(store);
  return store.accounts[label];
}

/**
 * Публичный срез аккаунта — без session-строки.
 * Используется и драйвером (list_accounts), и auth.cjs (list).
 */
function publicAccount(label, a, defaultLabel) {
  return {
    label,
    username: a.username,
    id: a.id,
    phone: a.phone,
    role: a.role || null,
    note: a.note || null,
    tags: a.tags || [],
    status: a.status || 'unknown',
    lastCheck: a.lastCheck || null,
    lastError: a.lastError || null,
    addedAt: a.addedAt || null,
    isDefault: defaultLabel === label,
  };
}

/**
 * Resolve which session string to use.
 * Priority: explicit label arg → TELEGRAM_ACCOUNT env → store default →
 *           legacy TELEGRAM_SESSION from .env.
 * Returns { label, session, source }.
 */
function resolveSession(explicitLabel) {
  const env = loadEnv();
  const store = readStore();
  const label = explicitLabel || process.env.TELEGRAM_ACCOUNT || store.default;

  if (label && store.accounts[label]) {
    return { label, session: store.accounts[label].session, source: 'store' };
  }
  if (label && !store.accounts[label]) {
    throw new Error(`Unknown account "${label}". Available: ${Object.keys(store.accounts).join(', ') || '(none)'}`);
  }
  // Fallback: legacy single-account session in .env
  if (env.TELEGRAM_SESSION) {
    return { label: 'legacy', session: env.TELEGRAM_SESSION, source: 'env' };
  }
  throw new Error('No Telegram account configured. Run: node auth.cjs add <label>');
}

module.exports = {
  STORE_PATH,
  META_FIELDS,
  STATUSES,
  loadEnv,
  readStore,
  writeStore,
  upsertAccount,
  removeAccount,
  setDefault,
  setMeta,
  publicAccount,
  resolveSession,
};
