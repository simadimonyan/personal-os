'use strict';
/**
 * Мост Telegram → база чатов: наполнение базы и обработка очереди.
 *
 * Правило, вокруг которого всё построено: в Telegram ходим редко и по чуть-чуть,
 * а читают агенты — из базы. Поэтому синк диалогов делается пачкой раз в сутки,
 * а незнакомые чаты берутся из очереди по нескольку в день с паузами и
 * суточным лимитом на каждый аккаунт (см. таблицу accounts).
 */

const store = require('./accounts.cjs');
const db_ = require('./chatsdb.cjs');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rnd = (a, b) => Math.floor(a + Math.random() * (b - a));

/** Клиент под конкретный ярлык фермы. Закрывается всегда. */
async function withClient(label, fn) {
  const { TelegramClient } = require('telegram');
  const { StringSession } = require('telegram/sessions');
  const env = store.loadEnv();
  const resolved = store.resolveSession(label);
  const client = new TelegramClient(
    new StringSession(resolved.session),
    parseInt(env.TELEGRAM_API_ID), env.TELEGRAM_API_HASH,
    { connectionRetries: 3 }
  );
  await client.connect();
  try { return await fn(client, resolved.label); }
  finally { await client.disconnect().catch(() => {}); }
}

function kindOf(d) {
  if (d.isUser) return d.entity?.bot ? 'bot' : 'user';
  if (d.entity?.megagroup) return 'group';
  if (d.isChannel) return 'channel';
  if (d.isGroup) return 'group';
  return 'chat';
}

const titleOf = (e, fallback) =>
  e?.title || [e?.firstName, e?.lastName].filter(Boolean).join(' ') || e?.username || fallback || null;

const usernameOf = (e) => e?.username || e?.usernames?.[0]?.username || null;

/**
 * Единый id чата на всю базу.
 * getDialogs отдаёт «размеченный» id (канал -100…, группа -…), а getEntity —
 * голый id сущности. Без приведения один и тот же чат попадал бы в базу дважды:
 * из синка диалогов и из очереди. Канон — размеченный, как в get_dialogs.
 */
function peerId(entity) {
  try { return require('telegram').utils.getPeerId(entity).toString(); }
  catch (e) { return entity?.id?.toString() ?? null; }
}

function mediaLabel(m) {
  if (!m.media) return null;
  if (m.photo) return 'фото';
  if (m.voice) return 'голосовое';
  if (m.videoNote) return 'кружок';
  if (m.video) return 'видео';
  if (m.audio) return 'аудио';
  if (m.sticker) return 'стикер';
  if (m.document) return 'файл';
  return 'медиа';
}

function rowsOfMessages(msgs) {
  return msgs.map((m) => ({
    msg_id: m.id,
    date: m.date ? new Date(m.date * 1000).toISOString() : null,
    from_id: m.fromId?.userId?.toString() || m.fromId?.toString() || null,
    from_name: m.sender ? titleOf(m.sender) : null,
    text: m.text || null,
    media: mediaLabel(m),
    out: m.out ? 1 : 0,
  }));
}

// ── синк диалогов ─────────────────────────────────────────────────────────

/**
 * Забрать диалоги аккаунта в базу. Один запрос к Telegram на аккаунт —
 * дешёвая операция, но гонять её чаще раза в сутки смысла нет.
 */
async function syncDialogs(db, label, { limit = 500 } = {}) {
  return withClient(label, async (client, account) => {
    const dialogs = await client.getDialogs({ limit });
    let chats = 0;
    for (const d of dialogs) {
      const id = d.id?.toString();
      if (!id) continue;
      const e = d.entity;
      db_.upsertChat(db, {
        id,
        kind: kindOf(d),
        title: titleOf(e, d.name),
        username: usernameOf(e),
        participants: e?.participantsCount ?? null,
        verified: e?.verified ? 1 : 0,
        scam: (e?.scam || e?.fake) ? 1 : 0,
        last_message_at: d.message?.date ? new Date(d.message.date * 1000).toISOString() : null,
        source: 'dialogs',
      });
      db_.upsertChatAccount(db, id, account, {
        joined: 1,
        unread: d.unreadCount ?? 0,
        is_admin: e?.adminRights ? 1 : 0,
      });
      chats++;
    }
    return { account, chats };
  });
}

/** Синк по всей ферме: аккаунты из accounts.json + диалоги каждого живого. */
async function syncAll(db, { limit = 500, accounts = null, pause = [3, 8] } = {}) {
  const meta = db_.syncAccounts(db, store);
  const s = store.readStore();
  const labels = accounts && accounts.length
    ? accounts
    : Object.entries(s.accounts).filter(([, a]) => a.status !== 'banned').map(([l]) => l);

  const out = [];
  for (const label of labels) {
    try {
      out.push(await syncDialogs(db, label, { limit }));
    } catch (e) {
      out.push({ account: label, error: e.message });
    }
    if (label !== labels[labels.length - 1]) await sleep(rnd(pause[0], pause[1]) * 1000);
  }
  return { accounts: meta.accounts, default: meta.default, synced: out, ...db_.stats(db) };
}

// ── очередь: заход в чаты по чуть-чуть ────────────────────────────────────

const floodSeconds = (e) => {
  if (typeof e?.seconds === 'number') return e.seconds;
  const m = /wait of (\d+) seconds/i.exec(e?.message || '');
  return m ? parseInt(m[1]) : null;
};

/** Один заход: info (только карточка) | read (карточка + кэш сообщений) | join (вступить). */
async function visit(client, db, item, account, { read_limit = 50 } = {}) {
  const { Api } = require('telegram');
  const target = item.target;

  // ссылка-приглашение в закрытый чат — только join, entity по ней не получить
  const invite = /(?:t\.me\/(?:joinchat\/|\+))([\w-]+)/.exec(target);
  if (invite && item.action === 'join') {
    const res = await client.invoke(new Api.messages.ImportChatInvite({ hash: invite[1] }));
    const chat = res.chats?.[0];
    const id = chat ? peerId(chat) : null;
    if (id) {
      db_.upsertChat(db, { id, kind: chat.megagroup ? 'group' : 'channel', title: chat.title, username: usernameOf(chat), source: 'queue' });
      db_.upsertChatAccount(db, id, account, { joined: 1 });
    }
    return { chat_id: id, title: chat?.title };
  }

  const entity = await client.getEntity(target);
  const id = peerId(entity);
  const isChannelLike = entity.className === 'Channel' || entity.className === 'Chat';

  let about = null, participants = entity.participantsCount ?? null;
  if (isChannelLike) {
    try {
      const full = await client.invoke(new Api.channels.GetFullChannel({ channel: entity }));
      about = full.fullChat?.about ?? null;
      participants = full.fullChat?.participantsCount ?? participants;
    } catch (e) { /* у обычной Chat-группы своего FullChannel нет — не беда */ }
  }

  db_.upsertChat(db, {
    id,
    kind: entity.className === 'User' ? (entity.bot ? 'bot' : 'user') : (entity.megagroup ? 'group' : isChannelLike ? 'channel' : 'group'),
    title: titleOf(entity),
    username: usernameOf(entity),
    about, participants,
    verified: entity.verified ? 1 : 0,
    scam: (entity.scam || entity.fake) ? 1 : 0,
    source: 'queue',
  });

  const res = { chat_id: id, title: titleOf(entity), participants };

  if (item.action === 'join') {
    await client.invoke(new Api.channels.JoinChannel({ channel: entity }));
    db_.upsertChatAccount(db, id, account, { joined: 1 });
    res.joined = true;
  }

  if (item.action === 'read' || item.action === 'join') {
    // публичный чат читается и без вступления
    const msgs = await client.getMessages(entity, { limit: read_limit });
    const saved = db_.saveMessages(db, id, account, rowsOfMessages(msgs));
    res.messages = saved;
  }
  return res;
}

/**
 * Обработать очередь. По одному, с паузой между чатами, не выходя за суточный
 * лимит аккаунта. На FLOOD_WAIT/PEER_FLOOD — стоп и cooldown аккаунту:
 * причина в темпе, а не в конкретном чате, следующий даст то же самое.
 */
async function runQueue(db, { account = null, limit = 5, dry = false, read_limit = 50, pause = null } = {}) {
  const label = account || store.readStore().default;
  const acc = db.prepare('SELECT * FROM accounts WHERE label = ?').get(label);
  if (!acc) throw new Error(`аккаунта "${label}" нет в базе — сначала db_sync`);
  if (['banned', 'limited', 'cooldown'].includes(acc.status)) {
    return { account: label, status: acc.status, done: [], note: `аккаунт в статусе "${acc.status}" — очередь не трогаем` };
  }

  const items = db_.queueNext(db, label, limit);
  const budget = db_.budget(db, label);
  if (!items.length) return { account: label, budget, done: [], note: 'брать нечего: очередь пуста, лимит выбран или всё отложено' };
  if (dry) return { account: label, budget, dry: true, plan: items };

  const [pmin, pmax] = pause || [acc.pause_min_sec, acc.pause_max_sec];
  const done = [], warnings = [];

  await withClient(label, async (client) => {
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      try {
        const r = await visit(client, db, item, label, { read_limit });
        db_.logAction(db, { account: label, action: item.action, chat_id: r.chat_id, target: item.target, ok: 1, queue_id: item.id });
        done.push({ id: item.id, target: item.target, action: item.action, ok: true, ...r });
      } catch (e) {
        const wait = floodSeconds(e);
        const flood = wait != null || /PEER_FLOOD|FLOOD_WAIT/i.test(e.message || '');
        db_.logAction(db, { account: label, action: item.action, target: item.target, ok: 0, error: e.message, queue_id: flood ? null : item.id });
        if (flood) {
          const until = db_.deferQueue(db, item.id, wait || 3600, e.message);
          store.upsertAccount(label, { status: 'cooldown', lastError: e.message });
          db.prepare("UPDATE accounts SET status = 'cooldown', last_error = ? WHERE label = ?").run(e.message, label);
          warnings.push(`${e.message} → аккаунт "${label}" в cooldown, очередь до ${until}, остальное не трогаем`);
          break;
        }
        done.push({ id: item.id, target: item.target, action: item.action, ok: false, error: e.message });
      }
      if (i < items.length - 1) await sleep(rnd(pmin, pmax) * 1000);
    }
  });

  return { account: label, done, warnings, budget: db_.budget(db, label), pending: db.prepare("SELECT COUNT(*) AS n FROM queue WHERE status='pending'").get().n };
}

/** Обновить кэш сообщений уже известного чата (без очереди, точечно). */
async function cacheHistory(db, { chat_id, account = null, limit = 100 }) {
  const label = account || store.readStore().default;
  return withClient(label, async (client, acc) => {
    const entity = await client.getEntity(chat_id);
    const id = peerId(entity);
    db_.upsertChat(db, {
      id, kind: entity.className === 'User' ? (entity.bot ? 'bot' : 'user') : (entity.megagroup ? 'group' : 'channel'),
      title: titleOf(entity), username: usernameOf(entity), source: 'manual',
    });
    const msgs = await client.getMessages(entity, { limit });
    const saved = db_.saveMessages(db, id, acc, rowsOfMessages(msgs));
    db_.logAction(db, { account: acc, action: 'read', chat_id: id, target: String(chat_id), ok: 1 });
    return { chat_id: id, title: titleOf(entity), ...saved };
  });
}

module.exports = { syncDialogs, syncAll, runQueue, cacheHistory, visit, withClient };
