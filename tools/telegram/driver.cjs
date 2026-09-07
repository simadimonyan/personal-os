#!/usr/bin/env node
/**
 * Telegram skill driver
 * Usage: node driver.cjs <tool_name> [json_args]
 * Output: JSON to stdout, errors to stderr
 * Reads credentials from telegram-mcp-server/.env automatically.
 */

'use strict';

// gramjs пишет INFO-логи через console.log в stdout и асинхронно интерливит их
// с итоговым JSON.stringify — под пайпом (subprocess) это ломает результат
// посреди массива. Контракт драйвера: «JSON to stdout, errors to stderr».
// Уводим ВЕСЬ console.log (в т.ч. gramjs) в stderr, а настоящий результат
// печатаем прямо в stdout через emit(). Так stdout = только результат.
// emit ждёт, пока данные реально уйдут в stdout: без этого process.exit(0) в
// finally обрывает большой результат посреди строки (pipe пишется асинхронно).
const emit = (s) => new Promise((res) => process.stdout.write(s + '\n', res));
console.log = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');

const store = require('./accounts.cjs');

// Shared app credentials live in .env; per-account sessions in accounts.json
const env = store.loadEnv();
const API_ID = parseInt(env.TELEGRAM_API_ID);
const API_HASH = env.TELEGRAM_API_HASH;

const [,, tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

// Account selection: args.account → TELEGRAM_ACCOUNT env → store default → legacy .env
const accountLabel = args.account;
delete args.account;

// Инструменты базы чатов: читают/пишут chats.db, в Telegram ходят только db_sync,
// db_cache и queue_run (и те — своими клиентами через chatsync).
const DB_TOOLS = new Set([
  'db_sync', 'db_chats', 'db_chat', 'db_accounts', 'db_account_set', 'db_tag',
  'db_messages', 'db_cache', 'db_stats',
  'queue_add', 'queue_list', 'queue_next', 'queue_run', 'queue_set',
]);

// node:sqlite до Node 23.4 доступен только под флагом, а флаг нельзя включить
// изнутри процесса. Чтобы наружу оставался один вход (`node driver.cjs <tool>`),
// перезапускаем себя с флагом — потребителям драйвера про это знать не нужно.
if (DB_TOOLS.has(tool) && !require('./chatsdb.cjs').available()) {
  const { spawnSync } = require('child_process');
  const r = spawnSync(
    process.execPath,
    ['--experimental-sqlite', __filename, ...process.argv.slice(2)],
    { stdio: 'inherit', env: { ...process.env, NODE_NO_WARNINGS: '1' } }
  );
  process.exit(r.status ?? 1);
}

async function run() {
  if (!tool) {
    console.error('Usage: node driver.cjs <tool_name> [json_args]\nExample: node driver.cjs get_dialogs \'{"limit":10,"account":"work"}\'');
    process.exit(1);
  }

  // Инструменты фермы — работают со стором, свой клиент им не нужен
  if (tool === 'list_accounts') {
    const s = store.readStore();
    await emit(JSON.stringify({
      default: s.default,
      accounts: Object.entries(s.accounts).map(([label, a]) => store.publicAccount(label, a, s.default)),
    }, null, 2));
    process.exit(0);
  }

  if (tool === 'check_accounts') {
    const { checkAccounts } = require('./farm.cjs');
    const labels = args.accounts || (accountLabel ? [accountLabel] : null);
    await emit(JSON.stringify(await checkAccounts(labels), null, 2));
    process.exit(0);
  }

  // База чатов: свой клиент поднимают только те, кому правда нужен Telegram.
  if (DB_TOOLS.has(tool)) {
    const chatsdb = require('./chatsdb.cjs');
    const chatsync = require('./chatsync.cjs');
    const db = chatsdb.open();
    let result;

    switch (tool) {
      case 'db_sync':
        result = await chatsync.syncAll(db, {
          limit: args.limit ?? 500,
          accounts: args.accounts || (accountLabel ? [accountLabel] : null),
        });
        break;
      case 'db_chats':
        result = chatsdb.findChats(db, { ...args, account: args.account_filter || accountLabel });
        break;
      case 'db_chat': {
        const key = args.chat_id || args.username;
        if (!key) throw new Error('db_chat: нужен chat_id или username');
        result = chatsdb.getChat(db, key);
        if (!result) throw new Error(`чата "${key}" нет в базе — добавь в очередь (queue_add) или прогони db_sync`);
        break;
      }
      case 'db_accounts':
        result = chatsdb.listAccounts(db);
        break;
      case 'db_account_set': {
        const label = args.label || accountLabel;
        if (!label) throw new Error('db_account_set: нужен label');
        const { label: _l, ...patch } = args;
        result = chatsdb.setAccount(db, label, patch);
        break;
      }
      case 'db_tag': {
        if (!args.chat_id) throw new Error('db_tag: нужен chat_id (или @username)');
        result = chatsdb.tagChat(db, args.chat_id, args);
        break;
      }
      case 'db_messages':
        result = chatsdb.readMessages(db, args);
        break;
      case 'db_cache':
        if (!args.chat_id) throw new Error('db_cache: нужен chat_id');
        result = await chatsync.cacheHistory(db, { ...args, account: accountLabel });
        break;
      case 'db_stats':
        result = chatsdb.stats(db);
        break;
      case 'queue_add':
        result = chatsdb.queueAdd(db, args.items || (args.target ? [args] : []));
        break;
      case 'queue_list':
        result = chatsdb.queueList(db, { ...args, account: args.account_filter || accountLabel });
        break;
      case 'queue_next':
        result = chatsdb.queueNext(db, accountLabel || args.account_filter || null, args.limit ?? 10, args.action);
        break;
      case 'queue_run':
        result = await chatsync.runQueue(db, { ...args, account: accountLabel });
        break;
      case 'queue_set':
        if (!args.id) throw new Error('queue_set: нужен id записи очереди');
        result = chatsdb.queueSet(db, args.id, args);
        break;
    }

    await emit(JSON.stringify(result, null, 2));
    process.exit(0);
  }

  const { StringSession } = require('telegram/sessions');
  const { TelegramClient } = require('telegram');

  const resolved = store.resolveSession(accountLabel);

  const client = new TelegramClient(
    new StringSession(resolved.session),
    API_ID,
    API_HASH,
    { connectionRetries: 3 }
  );

  await client.connect();

  try {
    let result;

    switch (tool) {
      case 'get_me': {
        const me = await client.getMe();
        result = { id: me.id?.toString(), username: me.username, firstName: me.firstName, lastName: me.lastName, phone: me.phone };
        break;
      }
      case 'get_dialogs': {
        const dialogs = await client.getDialogs({ limit: args.limit ?? 20 });
        result = dialogs.map(d => ({
          id: d.id?.toString(),
          name: d.name || d.title,
          username: d.entity?.username,
          unreadCount: d.unreadCount,
          isChannel: d.isChannel,
          isGroup: d.isGroup,
          isUser: d.isUser,
          // ниже — только для личных диалогов: отделить живых людей от ботов
          // и вытащить то, что Telegram знает о контакте (телефон — если он
          // сохранён в адресной книге)
          isBot: d.entity?.bot ?? false,
          isContact: d.entity?.contact ?? false,
          firstName: d.entity?.firstName,
          lastName: d.entity?.lastName,
          phone: d.entity?.phone,
        }));
        break;
      }
      case 'get_chat_history': {
        const messages = await client.getMessages(args.chat_id, { limit: args.limit ?? 10 });
        result = messages.map(m => ({
          id: m.id,
          text: m.text,
          date: new Date(m.date * 1000).toISOString(),
          fromId: m.fromId?.toString(),
          out: m.out,
        }));
        break;
      }
      case 'send_message': {
        const entity = await client.getEntity(args.chat_id);
        const msg = await client.sendMessage(entity, {
          message: args.text,
          parseMode: args.parse_mode || 'html',
        });
        result = { messageId: msg.id, success: true };
        break;
      }
      case 'edit_message': {
        await client.editMessage(args.chat_id, {
          message: args.message_id,
          text: args.text,
        });
        result = { success: true };
        break;
      }
      case 'delete_message': {
        await client.deleteMessages(args.chat_id, [args.message_id], { revoke: true });
        result = { success: true };
        break;
      }
      case 'get_entity': {
        const entity = await client.getEntity(args.entity);
        result = {
          id: entity.id?.toString(),
          username: entity.username,
          firstName: entity.firstName,
          lastName: entity.lastName,
          title: entity.title,
          type: entity.className,
        };
        break;
      }
      case 'search_public': {
        // Глобальный поиск публичных каналов/групп/пользователей по названию
        // и по @юзернейму (contacts.Search: myResults — свои, chats/users — общий индекс).
        if (!args.query) throw new Error('search_public: query обязателен');
        const { Api } = require('telegram');
        const res = await client.invoke(new Api.contacts.Search({
          q: args.query,
          limit: args.limit ?? 30,
        }));

        const rows = [];
        for (const c of res.chats || []) {
          rows.push({
            id: c.id?.toString(),
            title: c.title,
            username: c.username || (c.usernames?.[0]?.username),
            type: c.className,           // Channel | Chat
            isChannel: c.broadcast === true,
            isGroup: c.megagroup === true || c.className === 'Chat',
            participants: c.participantsCount ?? null,
            verified: c.verified === true,
            scam: c.scam === true || c.fake === true,
          });
        }
        for (const u of res.users || []) {
          rows.push({
            id: u.id?.toString(),
            title: [u.firstName, u.lastName].filter(Boolean).join(' '),
            username: u.username || (u.usernames?.[0]?.username),
            type: 'User',
            isBot: u.bot === true,
          });
        }
        result = rows;
        break;
      }
      case 'chat_info': {
        // Подробности публичного канала/группы: описание и число участников.
        if (!args.chat_id) throw new Error('chat_info: chat_id обязателен');
        const { Api } = require('telegram');
        const entity = await client.getEntity(args.chat_id);
        let about = null, participants = null, online = null;
        try {
          const full = await client.invoke(new Api.channels.GetFullChannel({ channel: entity }));
          about = full.fullChat?.about ?? null;
          participants = full.fullChat?.participantsCount ?? null;
          online = full.fullChat?.onlineCount ?? null;
        } catch (e) {
          about = null;
        }
        result = {
          id: entity.id?.toString(),
          title: entity.title,
          username: entity.username || entity.usernames?.[0]?.username,
          isChannel: entity.broadcast === true,
          isGroup: entity.megagroup === true,
          participants,
          online,
          about,
        };
        break;
      }
      case 'export_chat': {
        const fs = require('fs');
        const path = require('path');

        if (!args.chat_id) throw new Error('export_chat: chat_id обязателен');
        if (!args.out_dir) throw new Error('export_chat: out_dir обязателен');

        const withMedia = args.media !== false;
        const maxMediaBytes = (args.max_media_mb ?? 100) * 1024 * 1024;
        const limit = args.limit && args.limit > 0 ? args.limit : undefined;

        const entity = await client.getEntity(args.chat_id);
        const me = await client.getMe();
        const meName = me.firstName || me.username || 'Я';
        const chatName = args.name
          || entity.title
          || [entity.firstName, entity.lastName].filter(Boolean).join(' ')
          || entity.username
          || String(entity.id);

        const outDir = args.out_dir;
        const mediaDir = path.join(outDir, 'media');
        fs.mkdirSync(outDir, { recursive: true });
        if (withMedia) fs.mkdirSync(mediaDir, { recursive: true });

        // --- media helpers -------------------------------------------------
        const attrs = m => m.media?.document?.attributes || [];
        const fileNameAttr = m => attrs(m).find(a => a.className === 'DocumentAttributeFilename')?.fileName;
        const durationOf = m => attrs(m).find(a => typeof a.duration === 'number')?.duration;
        const mimeExt = {
          'image/webp': 'webp', 'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif',
          'application/x-tgsticker': 'tgs', 'video/webm': 'webm', 'video/mp4': 'mp4',
          'audio/ogg': 'ogg', 'audio/mpeg': 'mp3', 'application/pdf': 'pdf',
        };
        const mmss = s => s == null ? '' : ` ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

        function mediaInfo(m) {
          const mime = m.media?.document?.mimeType;
          const byName = () => {
            const fn = fileNameAttr(m);
            return fn && fn.includes('.') ? fn.split('.').pop().toLowerCase() : null;
          };
          if (m.photo) return { kind: 'photo', ext: 'jpg', label: '[фото]' };
          if (m.voice) return { kind: 'voice', ext: 'ogg', label: `[голосовое${mmss(durationOf(m))}]` };
          if (m.videoNote) return { kind: 'video_note', ext: 'mp4', label: `[кружок${mmss(durationOf(m))}]` };
          if (m.gif) return { kind: 'gif', ext: byName() || 'mp4', label: '[гиф]' };
          if (m.video) return { kind: 'video', ext: byName() || 'mp4', label: `[видео${mmss(durationOf(m))}]` };
          if (m.audio) return { kind: 'audio', ext: byName() || 'mp3', label: `[аудио${mmss(durationOf(m))}]` };
          if (m.sticker) return { kind: 'sticker', ext: mimeExt[mime] || 'webp', label: '[стикер]' };
          if (m.document) {
            const fn = fileNameAttr(m);
            return { kind: 'document', ext: byName() || mimeExt[mime] || 'bin', label: `[файл${fn ? ' ' + fn : ''}]` };
          }
          if (m.media) return { kind: 'other', ext: null, label: '[медиа]' };
          return null;
        }
        // изображения/видео/аудио Obsidian показывает встроенно, остальное — ссылкой
        const embeddable = new Set(['photo', 'voice', 'video_note', 'video', 'audio', 'gif', 'sticker']);

        // --- сбор сообщений ------------------------------------------------
        const pad = n => String(n).padStart(2, '0');
        const dayKey = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        const stats = { saved: 0, skipped: 0, failed: 0, bytes: 0 };
        const rows = [];
        let count = 0;

        for await (const m of client.iterMessages(entity, { limit, reverse: true })) {
          count++;
          if (count % 200 === 0) console.error(`… ${count} сообщений`);

          const when = new Date(m.date * 1000);
          const who = m.out
            ? meName
            : (m.sender?.firstName || m.sender?.title || m.sender?.username || chatName);

          const parts = [];
          const info = m.media ? mediaInfo(m) : null;

          if (info) {
            let rel = null;
            const size = Number(m.media?.document?.size ?? 0);
            if (!withMedia || info.ext === null) {
              stats.skipped++;
            } else if (size > maxMediaBytes) {
              stats.skipped++;
              parts.push(`${info.label} (не сохранено, ${(size / 1048576).toFixed(1)} МБ)`);
            } else {
              const file = `${m.id}_${info.kind}.${info.ext}`;
              const dest = path.join(mediaDir, file);
              try {
                if (!fs.existsSync(dest)) await client.downloadMedia(m, { outputFile: dest });
                stats.saved++;
                stats.bytes += fs.existsSync(dest) ? fs.statSync(dest).size : 0;
                rel = `media/${file}`;
              } catch (e) {
                stats.failed++;
                console.error(`! медиа ${m.id}: ${e.message}`);
              }
            }
            if (rel) {
              parts.push(embeddable.has(info.kind) ? `${info.label}\n  ![](${rel})` : `${info.label} [${rel}](${rel})`);
            } else if (!parts.length) {
              parts.push(info.label);
            }
          }

          if (m.text) parts.unshift(m.text.replace(/\n/g, '\n  '));
          if (!parts.length) continue;

          rows.push({
            day: dayKey(when),
            time: `${pad(when.getHours())}:${pad(when.getMinutes())}`,
            who,
            body: parts.join(' '),
            date: when,
          });
        }

        if (!rows.length) throw new Error('export_chat: в чате нет сообщений для экспорта');

        // --- markdown ------------------------------------------------------
        const today = dayKey(new Date());
        const title = `${chatName}${entity.username ? ` (@${entity.username})` : ''}`;
        const out = [
          '---',
          'type: telegram-export',
          `chat: "${title.replace(/"/g, "'")}"`,
          `chat_id: "${entity.id?.toString()}"`,
          `date: ${today}`,
          `messages: ${rows.length}`,
          `media: ${stats.saved}`,
          `period: ${rows[0].day} — ${rows[rows.length - 1].day}`,
          'tags:',
          '  - telegram-export',
          '---',
          '',
          `# ${title} — полный экспорт диалога · ${today}`,
          '',
        ];
        let day = null;
        for (const r of rows) {
          if (r.day !== day) { day = r.day; out.push('', `## ${day}`, ''); }
          out.push(`- ${r.time} — **${r.who}:** ${r.body}`);
        }

        const file = path.join(outDir, `${today} export.md`);
        fs.writeFileSync(file, out.join('\n') + '\n');

        result = {
          file,
          messages: rows.length,
          period: `${rows[0].day} — ${rows[rows.length - 1].day}`,
          media: { saved: stats.saved, skipped: stats.skipped, failed: stats.failed, mb: +(stats.bytes / 1048576).toFixed(1) },
        };
        break;
      }
      // Кто в группе. Нужен, чтобы сверять состав служебных групп (например Farm,
      // где собрана вся ферма) без ручных скриптов.
      case 'group_members': {
        const { Api } = require('telegram');
        if (!args.chat_id) throw new Error('group_members: нужен chat_id (или @username)');
        const entity = await client.getEntity(args.chat_id);
        const limit = args.limit ?? 200;
        const users = [];

        if (entity.className === 'Chat') {
          // обычная группа: участники приезжают сразу, пагинации нет
          const full = await client.invoke(new Api.messages.GetFullChat({ chatId: entity.id }));
          users.push(...full.users);
        } else {
          for (let offset = 0; offset < limit; offset += 100) {
            const part = await client.invoke(new Api.channels.GetParticipants({
              channel: entity,
              filter: new Api.ChannelParticipantsRecent(),
              offset,
              limit: Math.min(100, limit - offset),
              hash: 0,
            }));
            users.push(...part.users);
            if (part.users.length < 100) break;
          }
        }

        // метка фермы рядом с участником: сразу видно, кто из своих внутри
        const farm = store.readStore().accounts;
        const byId = new Map(Object.entries(farm).map(([label, a]) => [String(a.id), label]));

        result = {
          chat_id: entity.id?.toString(),
          title: entity.title,
          participants: entity.participantsCount ?? users.length,
          members: users.map((u) => ({
            id: u.id?.toString(),
            username: u.username || null,
            name: [u.firstName, u.lastName].filter(Boolean).join(' ') || null,
            bot: u.bot ?? false,
            account: byId.get(u.id?.toString()) || null,
          })),
        };
        break;
      }
      // Добавить участника. `user` — метка фермы (`acc2`), @username или id.
      //
      // Приглашение делает аккаунт-админ (`account`). Если он не может получить
      // input-entity приглашаемого (тот не в его кэше сущностей — обычное дело для
      // аккаунтов фермы, они друг другу не диалоги), для своих есть обход: админ
      // выпускает ссылку-приглашение, а приглашаемый входит по ней своей сессией.
      case 'add_member': {
        const { Api } = require('telegram');
        const who = args.user ?? args.user_id ?? args.username;
        if (!args.chat_id) throw new Error('add_member: нужен chat_id (или @username группы)');
        if (!who) throw new Error('add_member: нужен user — метка фермы, @username или id');

        const farm = store.readStore().accounts;
        const guestLabel = farm[who] ? who : null;   // свой из фермы → доступен обход
        const guest = guestLabel ? farm[guestLabel] : null;
        const entity = await client.getEntity(args.chat_id);
        const isChannel = entity.className !== 'Chat';
        const out = { chat_id: entity.id?.toString(), title: entity.title, user: who };

        // состав до попытки: повторный вызов не должен упираться в
        // USER_ALREADY_PARTICIPANT — команда идемпотентна
        const guestId = guest ? String(guest.id) : (/^\d+$/.test(String(who)) ? String(who) : null);
        const roster = async () => {
          const part = await client.invoke(isChannel
            ? new Api.channels.GetParticipants({ channel: entity, filter: new Api.ChannelParticipantsRecent(), offset: 0, limit: 200, hash: 0 })
            : new Api.messages.GetFullChat({ chatId: entity.id }));
          return part.users || [];
        };
        const already = (users) => users.some((u) => guestId
          ? u.id?.toString() === guestId
          : u.username && '@' + u.username === String(who));

        if (already(await roster())) {
          result = { ...out, method: 'уже внутри', inside: true };
          break;
        }

        // кого приглашаем: у своего берём username (по нему entity находится), иначе id
        const targets = guest
          ? [guest.username ? '@' + guest.username : null, guest.id].filter(Boolean)
          : [who];

        for (const target of targets) {
          try {
            const input = await client.getInputEntity(target);
            if (isChannel) {
              await client.invoke(new Api.channels.InviteToChannel({ channel: entity, users: [input] }));
            } else {
              await client.invoke(new Api.messages.AddChatUser({ chatId: entity.id, userId: input, fwdLimit: 0 }));
            }
            out.method = `приглашение админом (${target})`;
            break;
          } catch (e) {
            out.tried = [...(out.tried || []), { target: String(target), error: e.errorMessage || e.message }];
          }
        }

        if (!out.method) {
          if (!guest) throw new Error(
            `не удалось пригласить ${who}: ${out.tried?.map((t) => t.error).join(' · ')}. ` +
            'Обход через ссылку-приглашение возможен только для аккаунтов фермы (нужна их сессия).'
          );
          // обход: ссылка от админа + вход сессией приглашаемого
          const inv = await client.invoke(new Api.messages.ExportChatInvite({ peer: entity }));
          const hash = /(?:\+|joinchat\/)([\w-]+)/.exec(inv.link || '')?.[1];
          if (!hash) throw new Error('не разобрал ссылку-приглашение: ' + inv.link);

          const { StringSession } = require('telegram/sessions');
          const { TelegramClient } = require('telegram');
          const gc = new TelegramClient(new StringSession(guest.session), API_ID, API_HASH, { connectionRetries: 3 });
          await gc.connect();
          try {
            await gc.invoke(new Api.messages.ImportChatInvite({ hash }));
          } finally {
            await gc.disconnect().catch(() => {});
          }
          out.method = 'вход по ссылке-приглашению';
        }

        // сверка: реально ли внутри (Telegram принимает запрос и молча не добавляет)
        out.inside = already(await roster());
        out.hint = 'состав в базе обновится после db_sync по этому аккаунту';
        result = out;
        break;
      }
      case 'restrict_user': {
        const { ChatBannedRights } = require('telegram/tl/types');
        const until = args.duration_seconds ? Math.floor(Date.now() / 1000) + args.duration_seconds : 0;
        const rights = new ChatBannedRights({
          sendMessages: ['mute', 'no_send_messages', 'full_restrict'].includes(args.restrict_type),
          sendMedia: ['no_send_media', 'full_restrict'].includes(args.restrict_type),
          sendStickers: args.restrict_type === 'full_restrict',
          sendGifs: args.restrict_type === 'full_restrict',
          sendGames: args.restrict_type === 'full_restrict',
          sendInline: args.restrict_type === 'full_restrict',
          untilDate: until,
        });
        await client.invoke(new (require('telegram/tl/functions/channels').EditBanned)({
          channel: args.chat_id,
          participant: args.user_id,
          bannedRights: rights,
        }));
        result = { success: true };
        break;
      }
      default:
        throw new Error(
          `Unknown tool: ${tool}.\n` +
          `Telegram: get_me, get_dialogs, get_chat_history, export_chat, send_message, edit_message, delete_message, get_entity, search_public, chat_info, group_members, add_member, restrict_user\n` +
          `Ферма: list_accounts, check_accounts\n` +
          `База чатов: ${[...DB_TOOLS].join(', ')}`
        );
    }

    await emit(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(JSON.stringify({ error: err.message }));
    process.exit(1);
  } finally {
    await client.disconnect().catch(() => {});
    process.exit(0);
  }
}

// Инструменты до основного try (ферма, база чатов) кидают наружу — ловим здесь,
// чтобы наружу всегда уходил JSON с ошибкой, а не стек-трейс.
run().catch((err) => {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
});
