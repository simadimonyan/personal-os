#!/usr/bin/env node
/**
 * Zotero skill driver
 * Usage: node driver.mjs <tool_name> [json_args]
 * Output: JSON to stdout, errors to stderr
 * Note: reads directly from SQLite — Zotero does NOT need to be running for read tools.
 *       Write tools (zotero_add_item, zotero_save_from_url) require Zotero open.
 */

import { ZoteroDB } from './zotero-db.js';
import { ZoteroApi } from './zotero-api.js';
import { loadConfig } from './config.js';

const [,, tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

async function run() {
  const cfg = loadConfig();
  const db = new ZoteroDB(cfg);
  const api = new ZoteroApi(cfg);

  try {
    let result;
    switch (tool) {
      case 'zotero_search':
        result = db.search({
          query: args.query,
          creator: args.creator,
          tag: args.tag,
          collection: args.collection,
          itemType: args.itemType,
          limit: args.limit ?? 25,
          includeTrashed: args.includeTrashed ?? false,
        });
        break;
      case 'zotero_get_item':
        result = db.getItem(args.idOrKey ?? args.key ?? args.itemID);
        if (!result) throw new Error(`item not found: ${args.idOrKey ?? args.key ?? args.itemID}`);
        break;
      case 'zotero_get_attachments':
        result = db.getAttachments(args.idOrKey ?? args.itemKey);
        break;
      case 'zotero_list_collections':
        result = db.listCollections();
        break;
      case 'zotero_list_tags':
        result = db.listTags(args.limit ?? 200);
        break;
      case 'zotero_recent':
        result = db.recent(args.limit ?? 25);
        break;
      case 'zotero_stats':
        result = db.stats();
        break;
      case 'zotero_check_api':
        result = await api.ping();
        break;
      case 'zotero_item_template':
        result = await api.itemTemplate(args.itemType);
        break;
      case 'zotero_add_item':
        result = await api.createItems(args.items);
        break;
      case 'zotero_save_from_url':
        result = await api.saveFromUrl({ url: args.url, title: args.title });
        break;
      default:
        throw new Error(`Unknown tool: ${tool}. Available: zotero_search, zotero_get_item, zotero_get_attachments, zotero_list_collections, zotero_list_tags, zotero_recent, zotero_stats, zotero_check_api, zotero_item_template, zotero_add_item, zotero_save_from_url`);
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(JSON.stringify({ error: err.message }));
    process.exit(1);
  }
}

if (!tool) {
  console.error('Usage: node driver.mjs <tool_name> [json_args]\nExample: node driver.mjs zotero_search \'{"query":"machine learning","limit":10}\'');
  process.exit(1);
}

run();
