'use strict';
/**
 * База чатов фермы — единое хранилище всего, что мы знаем про Telegram,
 * чтобы не ходить в Telegram за каждым списком.
 *
 * Файл: chats.db рядом с драйвером (SQLite, node:sqlite).
 *
 *   accounts       за что отвечает каждый аккаунт + суточные лимиты
 *   chats          чат как таковой (один на весь Telegram, не на аккаунт)
 *   chat_accounts  что про этот чат знает конкретный аккаунт (состоит ли, непрочитанное)
 *   queue          куда ещё предстоит зайти — по одному, с паузами
 *   actions        журнал заходов: по нему считаются суточные лимиты
 *   messages       кэш сообщений, чтобы читать историю из базы
 *
 * node:sqlite до Node 23.4 живёт под флагом --experimental-sqlite; driver.cjs
 * перезапускает себя с флагом сам, здесь только проверка (see `available`).
 */

const path = require('path');

const DB_PATH = process.env.TELEGRAM_CHATS_DB || path.join(__dirname, 'chats.db');

function available() {
  try { require('node:sqlite'); return true; } catch (e) { return false; }
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS accounts (
  label           TEXT PRIMARY KEY,
  tg_id           TEXT,
  username        TEXT,
  phone           TEXT,
  role            TEXT,               -- личный | рассылки | чтение | резерв
  duty            TEXT,               -- за что отвечает, словами
  scope           TEXT,               -- json-массив зон: ["radar","blast","memory"]
  status          TEXT DEFAULT 'unknown',
  is_default      INTEGER DEFAULT 0,
  join_limit_day  INTEGER DEFAULT 5,  -- сколько чатов в сутки можно занять очередью
  read_limit_day  INTEGER DEFAULT 40, -- сколько чатов в сутки читать/опрашивать
  msg_limit_day   INTEGER DEFAULT 20, -- потолок исходящих (соблюдают рассылки)
  pause_min_sec   INTEGER DEFAULT 45, -- пауза между действиями очереди
  pause_max_sec   INTEGER DEFAULT 120,
  note            TEXT,
  last_check      TEXT,
  last_error      TEXT,
  synced_at       TEXT,
  updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS chats (
  id              TEXT PRIMARY KEY,   -- telegram id
  kind            TEXT,               -- user | bot | group | channel
  title           TEXT,
  username        TEXT,
  about           TEXT,
  participants    INTEGER,
  verified        INTEGER DEFAULT 0,
  scam            INTEGER DEFAULT 0,
  topic           TEXT,               -- к чему относится: фриланс, java, английский…
  tags            TEXT,               -- json-массив своих меток
  note            TEXT,
  last_message_at TEXT,
  msg_cached      INTEGER DEFAULT 0,
  source          TEXT,               -- dialogs | search | queue | manual
  first_seen      TEXT,
  updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS chats_username ON chats(username);
CREATE INDEX IF NOT EXISTS chats_kind     ON chats(kind);
CREATE INDEX IF NOT EXISTS chats_topic    ON chats(topic);

CREATE TABLE IF NOT EXISTS chat_accounts (
  chat_id    TEXT NOT NULL,
  account    TEXT NOT NULL,
  joined     INTEGER DEFAULT 1,       -- чат есть в диалогах этого аккаунта
  unread     INTEGER DEFAULT 0,
  is_admin   INTEGER DEFAULT 0,
  last_seen  TEXT,
  PRIMARY KEY (chat_id, account)
);
CREATE INDEX IF NOT EXISTS chat_accounts_account ON chat_accounts(account);

CREATE TABLE IF NOT EXISTS queue (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  target       TEXT NOT NULL,          -- @username, id или ссылка-приглашение
  chat_id      TEXT,                   -- проставляется после захода
  title        TEXT,
  action       TEXT DEFAULT 'info',    -- info | read | join
  account      TEXT,                   -- кому поручено (пусто = решаем при запуске)
  priority     INTEGER DEFAULT 5,      -- 1 — вперёд всех
  status       TEXT DEFAULT 'pending', -- pending | done | failed | skipped | hold
  reason       TEXT,                   -- зачем этот чат нужен
  attempts     INTEGER DEFAULT 0,
  not_before   TEXT,                   -- не раньше этого момента (ISO)
  last_attempt TEXT,
  last_error   TEXT,
  added_at     TEXT,
  done_at      TEXT,
  UNIQUE (target, action)
);
CREATE INDEX IF NOT EXISTS queue_status ON queue(status, priority);

CREATE TABLE IF NOT EXISTS actions (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT,
  action  TEXT,
  chat_id TEXT,
  target  TEXT,
  ok      INTEGER,
  error   TEXT,
  at      TEXT
);
CREATE INDEX IF NOT EXISTS actions_at ON actions(account, at);

CREATE TABLE IF NOT EXISTS messages (
  chat_id   TEXT NOT NULL,
  msg_id    INTEGER NOT NULL,
  account   TEXT,
  date      TEXT,
  from_id   TEXT,
  from_name TEXT,
  text      TEXT,
  media     TEXT,
  out       INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS messages_date ON messages(chat_id, date);
`;

// node:sqlite не принимает undefined и boolean — приводим к своим типам.
const v = (x) => {
  if (x === undefined || x === null) return null;
  if (typeof x === 'boolean') return x ? 1 : 0;
  if (typeof x === 'bigint') return x.toString();
  return x;
};
const now = () => new Date().toISOString();
const jsonOrNull = (x) => (x == null ? null : Array.isArray(x) ? JSON.stringify(x) : String(x));
const parseTags = (row) => {
  if (!row) return row;
  if (typeof row.tags === 'string') { try { row.tags = JSON.parse(row.tags); } catch (e) { row.tags = [row.tags]; } }
  if (typeof row.scope === 'string') { try { row.scope = JSON.parse(row.scope); } catch (e) { row.scope = [row.scope]; } }
  return row;
};

function open() {
  const { DatabaseSync } = require('node:sqlite');
  const db = new DatabaseSync(DB_PATH);
  // WAL: дашборд/другой процесс может читать базу, пока идёт синк
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA busy_timeout = 5000');
  db.exec(SCHEMA);
  return db;
}

// ── аккаунты: кто за что отвечает ─────────────────────────────────────────

const ACCOUNT_FIELDS = [
  'role', 'duty', 'scope', 'status', 'note',
  'join_limit_day', 'read_limit_day', 'msg_limit_day', 'pause_min_sec', 'pause_max_sec',
];

/** Подтянуть ферму из accounts.json: сессии — там, ответственность — здесь. */
function syncAccounts(db, store) {
  const s = store.readStore();
  const ins = db.prepare(`
    INSERT INTO accounts (label, tg_id, username, phone, role, status, is_default, last_check, last_error, synced_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(label) DO UPDATE SET
      tg_id      = excluded.tg_id,
      username   = excluded.username,
      phone      = excluded.phone,
      status     = excluded.status,
      is_default = excluded.is_default,
      last_check = excluded.last_check,
      last_error = excluded.last_error,
      synced_at  = excluded.synced_at,
      -- role заводится из фермы только пока его не задали здесь: duty/лимиты живут в базе
      role       = COALESCE(accounts.role, excluded.role)`);
  const t = now();
  let n = 0;
  for (const [label, a] of Object.entries(s.accounts)) {
    ins.run(label, v(a.id), v(a.username), v(a.phone), v(a.role), v(a.status || 'unknown'),
      s.default === label ? 1 : 0, v(a.lastCheck), v(a.lastError), t, t);
    n++;
  }
  return { accounts: n, default: s.default };
}

function listAccounts(db) {
  const rows = db.prepare('SELECT * FROM accounts ORDER BY is_default DESC, label').all();
  const today = now().slice(0, 10);
  const used = db.prepare(
    `SELECT account, action, COUNT(*) AS n FROM actions
     WHERE ok = 1 AND at >= ? GROUP BY account, action`
  ).all(today);
  return rows.map((r) => {
    const row = parseTags(r);
    row.today = {};
    for (const u of used) if (u.account === row.label) row.today[u.action] = u.n;
    row.chats = db.prepare('SELECT COUNT(*) AS n FROM chat_accounts WHERE account = ?').get(row.label).n;
    return row;
  });
}

function setAccount(db, label, patch) {
  const exists = db.prepare('SELECT label FROM accounts WHERE label = ?').get(label);
  if (!exists) throw new Error(`нет аккаунта "${label}" в базе — сначала db_sync (подтянет ферму из accounts.json)`);
  const sets = [], vals = [];
  for (const [k, val] of Object.entries(patch)) {
    if (!ACCOUNT_FIELDS.includes(k)) throw new Error(`поле "${k}" не правится. Доступны: ${ACCOUNT_FIELDS.join(', ')}`);
    sets.push(`${k} = ?`);
    vals.push(k === 'scope' ? jsonOrNull(val) : v(val));
  }
  if (!sets.length) throw new Error('нечего менять');
  sets.push('updated_at = ?'); vals.push(now());
  vals.push(label);
  db.prepare(`UPDATE accounts SET ${sets.join(', ')} WHERE label = ?`).run(...vals);
  return parseTags(db.prepare('SELECT * FROM accounts WHERE label = ?').get(label));
}

/** Лимиты аккаунта на сегодня: сколько уже потрачено и сколько осталось. */
function budget(db, label) {
  const acc = db.prepare('SELECT * FROM accounts WHERE label = ?').get(label);
  if (!acc) return null;
  const today = now().slice(0, 10);
  const rows = db.prepare(
    `SELECT action, COUNT(*) AS n FROM actions WHERE account = ? AND ok = 1 AND at >= ? GROUP BY action`
  ).all(label, today);
  const used = { join: 0, read: 0, info: 0 };
  for (const r of rows) used[r.action] = r.n;
  const readUsed = used.read + used.info;
  return {
    account: label,
    status: acc.status,
    join: { used: used.join, limit: acc.join_limit_day, left: Math.max(0, acc.join_limit_day - used.join) },
    read: { used: readUsed, limit: acc.read_limit_day, left: Math.max(0, acc.read_limit_day - readUsed) },
    pause: [acc.pause_min_sec, acc.pause_max_sec],
  };
}

// ── чаты ──────────────────────────────────────────────────────────────────

function upsertChat(db, c) {
  const t = now();
  db.prepare(`
    INSERT INTO chats (id, kind, title, username, about, participants, verified, scam, last_message_at, source, first_seen, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET
      kind            = COALESCE(excluded.kind, chats.kind),
      title           = COALESCE(excluded.title, chats.title),
      username        = COALESCE(excluded.username, chats.username),
      about           = COALESCE(excluded.about, chats.about),
      participants    = COALESCE(excluded.participants, chats.participants),
      verified        = COALESCE(excluded.verified, chats.verified),
      scam            = COALESCE(excluded.scam, chats.scam),
      last_message_at = COALESCE(excluded.last_message_at, chats.last_message_at),
      updated_at      = excluded.updated_at`
  ).run(String(c.id), v(c.kind), v(c.title), v(c.username), v(c.about), v(c.participants),
    v(c.verified), v(c.scam), v(c.last_message_at), v(c.source || 'dialogs'), t, t);
  return String(c.id);
}

function upsertChatAccount(db, chatId, account, data = {}) {
  db.prepare(`
    INSERT INTO chat_accounts (chat_id, account, joined, unread, is_admin, last_seen)
    VALUES (?,?,?,?,?,?)
    ON CONFLICT(chat_id, account) DO UPDATE SET
      joined    = excluded.joined,
      unread    = excluded.unread,
      is_admin  = COALESCE(excluded.is_admin, chat_accounts.is_admin),
      last_seen = excluded.last_seen`
  ).run(String(chatId), account, v(data.joined ?? 1), v(data.unread ?? 0), v(data.is_admin), now());
}

/** Пометить чат своими метками — это и делает базу источником, а не свалкой. */
function tagChat(db, chatId, patch) {
  const row = db.prepare('SELECT id FROM chats WHERE id = ? OR username = ?').get(String(chatId), String(chatId).replace(/^@/, ''));
  if (!row) throw new Error(`чата "${chatId}" нет в базе`);
  const sets = [], vals = [];
  for (const k of ['topic', 'note', 'tags']) {
    if (patch[k] !== undefined) { sets.push(`${k} = ?`); vals.push(k === 'tags' ? jsonOrNull(patch[k]) : v(patch[k])); }
  }
  if (!sets.length) throw new Error('нечего менять: topic | tags | note');
  sets.push('updated_at = ?'); vals.push(now());
  vals.push(row.id);
  db.prepare(`UPDATE chats SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
  return parseTags(db.prepare('SELECT * FROM chats WHERE id = ?').get(row.id));
}

function findChats(db, f = {}) {
  const where = [], vals = [];
  if (f.q) {
    where.push('(chats.title LIKE ? COLLATE NOCASE OR chats.username LIKE ? COLLATE NOCASE OR chats.about LIKE ? COLLATE NOCASE)');
    const like = `%${f.q}%`; vals.push(like, like, like);
  }
  if (f.kind) { where.push('chats.kind = ?'); vals.push(f.kind); }
  if (f.topic) { where.push('chats.topic = ?'); vals.push(f.topic); }
  if (f.tag) { where.push('chats.tags LIKE ?'); vals.push(`%"${f.tag}"%`); }
  if (f.username) { where.push('chats.username = ?'); vals.push(String(f.username).replace(/^@/, '')); }
  if (f.account) {
    where.push('EXISTS (SELECT 1 FROM chat_accounts ca WHERE ca.chat_id = chats.id AND ca.account = ? AND ca.joined = 1)');
    vals.push(f.account);
  }
  if (f.unread) where.push('EXISTS (SELECT 1 FROM chat_accounts ca WHERE ca.chat_id = chats.id AND ca.unread > 0)');

  const order = {
    recent: 'chats.last_message_at DESC',
    size: 'chats.participants DESC',
    title: 'chats.title COLLATE NOCASE',
    seen: 'chats.first_seen DESC',
  }[f.order || 'recent'] || 'chats.last_message_at DESC';

  const rows = db.prepare(`
    SELECT chats.*,
           (SELECT GROUP_CONCAT(ca.account) FROM chat_accounts ca WHERE ca.chat_id = chats.id AND ca.joined = 1) AS accounts,
           (SELECT SUM(ca.unread)  FROM chat_accounts ca WHERE ca.chat_id = chats.id) AS unread
    FROM chats
    ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
    ORDER BY ${order} NULLS LAST
    LIMIT ?`).all(...vals, f.limit ?? 50);
  return rows.map(parseTags).map((r) => ({ ...r, accounts: r.accounts ? r.accounts.split(',') : [] }));
}

function getChat(db, key) {
  const k = String(key).replace(/^@/, '');
  const chat = db.prepare('SELECT * FROM chats WHERE id = ? OR username = ? COLLATE NOCASE').get(String(key), k);
  if (!chat) return null;
  return {
    ...parseTags(chat),
    accounts: db.prepare('SELECT * FROM chat_accounts WHERE chat_id = ?').all(chat.id),
    queue: db.prepare('SELECT id, action, status, account, reason, not_before, last_error FROM queue WHERE chat_id = ? OR target = ?')
      .all(chat.id, chat.username ? '@' + chat.username : chat.id),
    messages: db.prepare('SELECT COUNT(*) AS n, MIN(date) AS from_date, MAX(date) AS to_date FROM messages WHERE chat_id = ?').get(chat.id),
  };
}

// ── кэш сообщений ─────────────────────────────────────────────────────────

function saveMessages(db, chatId, account, msgs) {
  const ins = db.prepare(`
    INSERT INTO messages (chat_id, msg_id, account, date, from_id, from_name, text, media, out)
    VALUES (?,?,?,?,?,?,?,?,?)
    ON CONFLICT(chat_id, msg_id) DO UPDATE SET text = excluded.text, media = excluded.media`);
  let n = 0;
  for (const m of msgs) {
    ins.run(String(chatId), m.msg_id, v(account), v(m.date), v(m.from_id), v(m.from_name), v(m.text), v(m.media), v(m.out));
    n++;
  }
  const total = db.prepare('SELECT COUNT(*) AS n, MAX(date) AS last FROM messages WHERE chat_id = ?').get(String(chatId));
  db.prepare('UPDATE chats SET msg_cached = ?, last_message_at = COALESCE(?, last_message_at), updated_at = ? WHERE id = ?')
    .run(total.n, v(total.last), now(), String(chatId));
  return { saved: n, cached: total.n };
}

function readMessages(db, f = {}) {
  const where = [], vals = [];
  if (f.chat_id) {
    const k = String(f.chat_id).replace(/^@/, '');
    const chat = db.prepare('SELECT id FROM chats WHERE id = ? OR username = ? COLLATE NOCASE').get(String(f.chat_id), k);
    if (!chat) throw new Error(`чата "${f.chat_id}" нет в базе`);
    where.push('chat_id = ?'); vals.push(chat.id);
  }
  if (f.q) { where.push('text LIKE ?'); vals.push(`%${f.q}%`); }
  if (f.since) { where.push('date >= ?'); vals.push(f.since); }
  return db.prepare(`
    SELECT chat_id, msg_id, date, from_name, text, media, out FROM messages
    ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
    ORDER BY date DESC LIMIT ?`).all(...vals, f.limit ?? 50);
}

// ── очередь ───────────────────────────────────────────────────────────────

const ACTIONS = ['info', 'read', 'join'];
const QUEUE_STATUSES = ['pending', 'done', 'failed', 'skipped', 'hold'];

function queueAdd(db, items) {
  const ins = db.prepare(`
    INSERT INTO queue (target, title, action, account, priority, reason, not_before, status, added_at)
    VALUES (?,?,?,?,?,?,?,'pending',?)
    ON CONFLICT(target, action) DO UPDATE SET
      reason     = COALESCE(excluded.reason, queue.reason),
      priority   = excluded.priority,
      account    = COALESCE(excluded.account, queue.account),
      not_before = COALESCE(excluded.not_before, queue.not_before)`);
  const t = now();
  const added = [];
  for (const raw of items) {
    const it = typeof raw === 'string' ? { target: raw } : raw;
    if (!it.target) throw new Error('в очередь нужен target: @username, id или ссылка');
    const action = it.action || 'info';
    if (!ACTIONS.includes(action)) throw new Error(`action "${action}" неизвестен. Доступны: ${ACTIONS.join(', ')}`);
    ins.run(String(it.target), v(it.title), action, v(it.account), v(it.priority ?? 5), v(it.reason), v(it.not_before), t);
    added.push({ target: it.target, action });
  }
  return { added: added.length, items: added, pending: db.prepare("SELECT COUNT(*) AS n FROM queue WHERE status='pending'").get().n };
}

function queueList(db, f = {}) {
  const where = [], vals = [];
  if (f.status) { where.push('status = ?'); vals.push(f.status); }
  if (f.account) { where.push('(account = ? OR account IS NULL)'); vals.push(f.account); }
  if (f.action) { where.push('action = ?'); vals.push(f.action); }
  return db.prepare(`
    SELECT * FROM queue ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
    ORDER BY (status='pending') DESC, priority, added_at LIMIT ?`).all(...vals, f.limit ?? 50);
}

/** Что можно взять прямо сейчас: с учётом not_before и суточных лимитов аккаунта. */
function queueNext(db, account, limit = 10, action = null) {
  const t = now();
  const where = ["status = 'pending'", '(not_before IS NULL OR not_before <= ?)'];
  const vals = [t];
  if (account) { where.push('(account = ? OR account IS NULL)'); vals.push(account); }
  if (action) { where.push('action = ?'); vals.push(action); }
  const rows = db.prepare(`SELECT * FROM queue WHERE ${where.join(' AND ')} ORDER BY priority, added_at LIMIT ?`)
    .all(...vals, limit * 3);

  if (!account) return rows.slice(0, limit);
  const b = budget(db, account);
  const left = { join: b ? b.join.left : 0, read: b ? b.read.left : 0, info: b ? b.read.left : 0 };
  const out = [];
  for (const r of rows) {
    const key = r.action === 'join' ? 'join' : 'read';
    if (left[key] <= 0) continue;
    left[key]--; if (key === 'read') left.info = left.read;
    out.push(r);
    if (out.length >= limit) break;
  }
  return out;
}

function queueSet(db, id, patch) {
  const row = db.prepare('SELECT * FROM queue WHERE id = ?').get(id);
  if (!row) throw new Error(`в очереди нет записи #${id}`);
  const sets = [], vals = [];
  for (const k of ['status', 'account', 'priority', 'reason', 'not_before', 'action']) {
    if (patch[k] !== undefined) {
      if (k === 'status' && !QUEUE_STATUSES.includes(patch[k])) throw new Error(`статус "${patch[k]}" неизвестен. Доступны: ${QUEUE_STATUSES.join(', ')}`);
      sets.push(`${k} = ?`); vals.push(v(patch[k]));
    }
  }
  if (!sets.length) throw new Error('нечего менять: status | account | priority | reason | not_before | action');
  vals.push(id);
  db.prepare(`UPDATE queue SET ${sets.join(', ')} WHERE id = ?`).run(...vals);
  return db.prepare('SELECT * FROM queue WHERE id = ?').get(id);
}

/** Итог захода: и в очередь, и в журнал — журнал держит суточные лимиты. */
function logAction(db, { account, action, chat_id, target, ok, error, queue_id }) {
  const t = now();
  db.prepare('INSERT INTO actions (account, action, chat_id, target, ok, error, at) VALUES (?,?,?,?,?,?,?)')
    .run(v(account), v(action), v(chat_id), v(target), ok ? 1 : 0, v(error), t);
  if (queue_id) {
    db.prepare(`UPDATE queue SET status = ?, chat_id = COALESCE(?, chat_id), attempts = attempts + 1,
                last_attempt = ?, last_error = ?, done_at = ?, account = COALESCE(account, ?) WHERE id = ?`)
      .run(ok ? 'done' : 'failed', v(chat_id), t, v(error), ok ? t : null, v(account), queue_id);
  }
}

/** FLOOD_WAIT: не ретраим, а отодвигаем запись и оставляем её в очереди. */
function deferQueue(db, id, seconds, error) {
  const until = new Date(Date.now() + seconds * 1000).toISOString();
  db.prepare(`UPDATE queue SET status = 'pending', not_before = ?, attempts = attempts + 1,
              last_attempt = ?, last_error = ? WHERE id = ?`).run(until, now(), v(error), id);
  return until;
}

function stats(db) {
  const one = (sql, ...a) => db.prepare(sql).get(...a);
  const all = (sql, ...a) => db.prepare(sql).all(...a);
  return {
    db: DB_PATH,
    chats: one('SELECT COUNT(*) AS n FROM chats').n,
    by_kind: all('SELECT kind, COUNT(*) AS n FROM chats GROUP BY kind ORDER BY n DESC'),
    by_account: all(`SELECT account, COUNT(*) AS n FROM chat_accounts WHERE joined = 1 GROUP BY account ORDER BY n DESC`),
    by_topic: all(`SELECT topic, COUNT(*) AS n FROM chats WHERE topic IS NOT NULL GROUP BY topic ORDER BY n DESC LIMIT 20`),
    messages: one('SELECT COUNT(*) AS n FROM messages').n,
    messages_chats: one('SELECT COUNT(DISTINCT chat_id) AS n FROM messages').n,
    queue: all('SELECT status, COUNT(*) AS n FROM queue GROUP BY status'),
    actions_today: all(`SELECT account, action, COUNT(*) AS n FROM actions WHERE at >= ? GROUP BY account, action`, now().slice(0, 10)),
    last_sync: one('SELECT MAX(synced_at) AS at FROM accounts').at,
  };
}

module.exports = {
  DB_PATH, ACTIONS, ACCOUNT_FIELDS, QUEUE_STATUSES,
  available, open, now,
  syncAccounts, listAccounts, setAccount, budget,
  upsertChat, upsertChatAccount, tagChat, findChats, getChat,
  saveMessages, readMessages,
  queueAdd, queueList, queueNext, queueSet, logAction, deferQueue,
  stats,
};
