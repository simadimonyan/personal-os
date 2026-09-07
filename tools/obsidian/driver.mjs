#!/usr/bin/env node
/**
 * Obsidian skill driver — "Органон" personal knowledge base
 * Usage: node driver.mjs <tool_name> [json_args]
 * Output: JSON to stdout
 */

import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const VAULT = "/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон";

const SECTIONS = {
  '00': '00 — Дашборд',
  '01': '01 — Личность',
  '02': '02 — Внутренний мир',
  '03': '03 — Идеи и мысли',
  '04': '04 — Цели и задачи',
  '05': '05 — Знания и навыки',
  '06': '06 — Проекты',
  '07': '07 — Жизнь',
  '08': '08 — Социальный капитал',
  '09': '09 — Шаблоны и ресурсы',
  '10': '10 — Claude',
  '11': '11 — Архив',
};

function allMdFiles(dir = VAULT) {
  const results = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) results.push(...allMdFiles(full));
    else if (entry.name.endsWith('.md')) results.push(full);
  }
  return results;
}

function relPath(p) {
  return p.replace(VAULT + '/', '');
}

function findNote(name) {
  // Exact filename match first, then partial
  const all = allMdFiles();
  const exact = all.find(f => path.basename(f, '.md') === name);
  if (exact) return exact;
  const partial = all.find(f => path.basename(f, '.md').toLowerCase().includes(name.toLowerCase()));
  return partial || null;
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

// ── Tools ────────────────────────────────────────────────────────────────────

const tools = {

  /** Full-text search across all notes */
  obsidian_search({ query, limit = 20, section }) {
    const files = allMdFiles();
    const results = [];
    const q = query.toLowerCase();
    for (const file of files) {
      if (section && !file.includes(section)) continue;
      if (results.length >= limit) break;
      let content;
      try { content = fs.readFileSync(file, 'utf8'); } catch { continue; }
      if (content.toLowerCase().includes(q)) {
        // Find excerpt around match
        const idx = content.toLowerCase().indexOf(q);
        const excerpt = content.slice(Math.max(0, idx - 60), idx + 120).replace(/\n/g, ' ').trim();
        results.push({ path: relPath(file), name: path.basename(file, '.md'), excerpt });
      }
    }
    return { count: results.length, results };
  },

  /** Read a note by name */
  obsidian_read({ name, path: notePath }) {
    let file;
    if (notePath) {
      file = path.join(VAULT, notePath);
    } else {
      file = findNote(name);
    }
    if (!file || !fs.existsSync(file)) throw new Error(`Note not found: ${name || notePath}`);
    const content = fs.readFileSync(file, 'utf8');
    return { path: relPath(file), name: path.basename(file, '.md'), content };
  },

  /** List notes in a section */
  obsidian_list({ section, recursive = true, limit = 50 }) {
    const sectionDir = Object.values(SECTIONS).find(s => s.startsWith(section) || s.includes(section))
      || section;
    const dir = path.join(VAULT, sectionDir);
    if (!fs.existsSync(dir)) throw new Error(`Section not found: ${section}`);
    const files = recursive ? allMdFiles(dir) : fs.readdirSync(dir)
      .filter(f => f.endsWith('.md'))
      .map(f => path.join(dir, f));
    return files.slice(0, limit).map(f => ({
      path: relPath(f),
      name: path.basename(f, '.md'),
      modified: fs.statSync(f).mtime.toISOString().slice(0, 10),
    }));
  },

  /** Create a new note */
  obsidian_create({ title, content, section, tags = [], subfolder }) {
    const sectionDir = Object.values(SECTIONS).find(s => s.startsWith(section) || s.includes(section))
      || section;
    let dir = path.join(VAULT, sectionDir);
    if (subfolder) dir = path.join(dir, subfolder);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    const filename = `${title}.md`;
    const filepath = path.join(dir, filename);
    if (fs.existsSync(filepath)) throw new Error(`Note already exists: ${relPath(filepath)}`);

    const tagLine = tags.length ? `tags: [${tags.join(', ')}]` : `tags: []`;
    const frontmatter = `---\ndate: ${today()}\n${tagLine}\n---\n\n`;
    fs.writeFileSync(filepath, frontmatter + (content || `# ${title}\n\n`));
    return { created: relPath(filepath) };
  },

  /** Append content to an existing note */
  obsidian_append({ name, content, path: notePath }) {
    let file;
    if (notePath) file = path.join(VAULT, notePath);
    else file = findNote(name);
    if (!file || !fs.existsSync(file)) throw new Error(`Note not found: ${name || notePath}`);
    fs.appendFileSync(file, '\n' + content);
    return { appended_to: relPath(file) };
  },

  /** Find notes by tag */
  obsidian_find_by_tag({ tag, limit = 30 }) {
    const files = allMdFiles();
    const results = [];
    const tagPattern = tag.startsWith('#') ? tag : `#${tag}`;
    for (const file of files) {
      if (results.length >= limit) break;
      let content;
      try { content = fs.readFileSync(file, 'utf8'); } catch { continue; }
      // Check both frontmatter tags: [...] and inline #tag
      const hasFrontmatterTag = content.match(/^tags:\s*\[.*?\]/m)?.[0]?.toLowerCase().includes(tag.replace('#','').toLowerCase());
      const hasInlineTag = content.includes(tagPattern) || content.toLowerCase().includes(tagPattern.toLowerCase());
      if (hasFrontmatterTag || hasInlineTag) {
        results.push({ path: relPath(file), name: path.basename(file, '.md') });
      }
    }
    return { tag: tagPattern, count: results.length, results };
  },

  /** Recently modified notes */
  obsidian_recent({ limit = 15, section }) {
    let files = allMdFiles();
    if (section) {
      const s = Object.values(SECTIONS).find(s => s.includes(section)) || section;
      files = files.filter(f => f.includes(s));
    }
    return files
      .map(f => ({ path: relPath(f), name: path.basename(f, '.md'), modified: fs.statSync(f).mtime }))
      .sort((a, b) => b.modified - a.modified)
      .slice(0, limit)
      .map(f => ({ ...f, modified: f.modified.toISOString().slice(0, 10) }));
  },

  /** Create or append to today's diary */
  obsidian_diary({ content, mood, tags = [] }) {
    const date = today();
    const diaryDir = path.join(VAULT, '02 — Внутренний мир', 'Дневник');
    const filename = `Дневник ${date}.md`;
    const filepath = path.join(diaryDir, filename);

    if (!fs.existsSync(filepath)) {
      const allTags = ['дневник', ...tags];
      const moodLine = mood ? `mood: ${mood}\n` : '';
      const front = `---\ndate: ${date}\ntags: [${allTags.join(', ')}]\n${moodLine}---\n\n# Дневник - ${date}\n\n`;
      fs.writeFileSync(filepath, front + (content || ''));
      return { created: relPath(filepath) };
    } else {
      const entry = `\n\n---\n\n${content}`;
      fs.appendFileSync(filepath, entry);
      return { appended_to: relPath(filepath) };
    }
  },

  /** Vault statistics */
  obsidian_stats() {
    const files = allMdFiles();
    const bySections = {};
    for (const [num, name] of Object.entries(SECTIONS)) {
      bySections[name] = files.filter(f => f.includes(name)).length;
    }
    return { total: files.length, by_section: bySections };
  },
};

// ── CLI entry point ───────────────────────────────────────────────────────────

const [,, tool, argsJson] = process.argv;

if (!tool) {
  console.error(`Usage: node driver.mjs <tool> [json_args]
Tools: ${Object.keys(tools).join(', ')}`);
  process.exit(1);
}

if (!tools[tool]) {
  console.error(JSON.stringify({ error: `Unknown tool: ${tool}`, available: Object.keys(tools) }));
  process.exit(1);
}

try {
  const args = argsJson ? JSON.parse(argsJson) : {};
  const result = await tools[tool](args);
  console.log(JSON.stringify(result, null, 2));
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
