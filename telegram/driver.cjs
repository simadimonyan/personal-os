#!/usr/bin/env node
/**
 * Telegram skill driver
 * Usage: node driver.cjs <tool_name> [json_args]
 * Output: JSON to stdout, errors to stderr
 * Reads credentials from telegram-mcp-server/.env automatically.
 */

'use strict';

const path = require('path');
const fs = require('fs');

// Load .env from skill directory
const envPath = path.join(__dirname, '.env');
const envContent = fs.readFileSync(envPath, 'utf8');
envContent.split('\n').forEach(line => {
  const [key, ...rest] = line.split('=');
  if (key && rest.length) process.env[key.trim()] = rest.join('=').trim();
});

const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions');

const API_ID = parseInt(process.env.TELEGRAM_API_ID);
const API_HASH = process.env.TELEGRAM_API_HASH;
const SESSION_STRING = process.env.TELEGRAM_SESSION || '';

const [,, tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

async function run() {
  if (!tool) {
    console.error('Usage: node driver.cjs <tool_name> [json_args]\nExample: node driver.cjs get_dialogs \'{"limit":10}\'');
    process.exit(1);
  }

  const client = new TelegramClient(
    new StringSession(SESSION_STRING),
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
        throw new Error(`Unknown tool: ${tool}. Available: get_me, get_dialogs, get_chat_history, send_message, edit_message, delete_message, get_entity, restrict_user`);
    }

    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(JSON.stringify({ error: err.message }));
    process.exit(1);
  } finally {
    await client.disconnect().catch(() => {});
    process.exit(0);
  }
}

run();
