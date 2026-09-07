#!/usr/bin/env node
/**
 * Клиент внутреннего API Яндекс Форм (forms.yandex.ru/admin/gateway/root/form/*).
 *
 * Публичного API на создание форм нет, но конструктор общается с бэкендом
 * обычным JSON: мы открываем страницу залогиненным браузером, берём из
 * window.__DATA__ csrf-токен и дальше шлём те же запросы через fetch внутри
 * страницы — это на порядок надёжнее, чем кликать по DOM.
 *
 *   node yaapi.mjs info <surveyId>        — что знает сервер о форме
 *   node yaapi.mjs list                   — список форм
 *   node yaapi.mjs check <спека>          — проверить спеку, ничего не создавая
 *   node yaapi.mjs build <спека> [--publish]  — собрать форму
 *   node yaapi.mjs insert <surveyId> <спека> [--section N --take N --page N --position N]
 *                                         — дописать вопросы в живую форму, ссылка не меняется
 *   node yaapi.mjs publish <surveyId>
 *   node yaapi.mjs delete <surveyId>
 *
 * <спека> — ключ из forms.json, путь к своему .json или путь.json#ключ.
 *
 * Типы вопросов (ключ в спеке → что уходит в API):
 *   text      Короткий текст     type=text   view=textinput
 *   textarea  Длинный текст      type=text   view=textarea
 *   number    Число              type=text   view=textinput  validator=decimal
 *   email     Почта              type=text   view=textinput  validator=email
 *   phone     Телефон            type=text   view=textinput  validator=phone
 *   link      Ссылка             type=text   view=textinput  validator=url
 *   radio     Один вариант       type=choices view=radio
 *   checkbox  Несколько          type=choices view=checks
 *   select    Выпадающий список  type=choices view=select
 *   boolean   Да/Нет             type=boolean
 *   date      Дата               type=date
 *   scale     Оценка по шкале    type=matrix  rows[] columns[]
 */

import { chromium } from 'playwright';
import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { homedir } from 'os';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROFILE_DIR = join(homedir(), '.yandex-forms-profile');
const GATEWAY = 'https://forms.yandex.ru/admin/gateway/root/form/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let seq = 0;
const newId = () => Number(`${Date.now()}${String(++seq).padStart(3, '0')}`.slice(-16));

async function session() {
  mkdirSync(PROFILE_DIR, { recursive: true });
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.YAF_HEADLESS !== '0',
    channel: 'chrome',
    viewport: { width: 1400, height: 950 },
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto('https://forms.yandex.ru/', { waitUntil: 'domcontentloaded' });
  await sleep(2500);
  if (/passport\.yandex/.test(page.url())) {
    await ctx.close();
    throw new Error('нет сессии Яндекса — сначала: node yaforms.mjs login');
  }
  const token = await page.evaluate(() => {
    const rx = /[a-f0-9]{40}:\d{10}/;
    const m = document.documentElement.innerHTML.match(rx);
    return m ? m[0] : null;
  });
  if (!token) { await ctx.close(); throw new Error('не нашёл csrf-токен на странице'); }

  const call = async (op, body) => {
    const r = await page.evaluate(
      async ([gw, op, body, token]) => {
        const res = await fetch(gw + op, {
          method: 'POST',
          credentials: 'include',
          headers: {
            'content-type': 'application/json',
            'x-csrf-token': token,
            'x-sdk': '1',
            'x-use-collab': '1',
          },
          body: JSON.stringify(body),
        });
        const text = await res.text();
        try { return { status: res.status, data: JSON.parse(text) }; }
        catch { return { status: res.status, data: text }; }
      },
      [GATEWAY, op, body, token]
    );
    if (r.status >= 400) throw new Error(`${op} → ${r.status}: ${JSON.stringify(r.data).slice(0, 300)}`);
    return r.data;
  };
  return { ctx, page, call };
}

// ── сборка вопроса под API ────────────────────────────────────────────────
function questionPayload(q) {
  const id = newId();
  const opts = () =>
    (q.options || []).map((text) => ({ id: newId(), text, key: String(newId()) }));

  switch (q.type) {
    case 'radio':
      return { id, key: `answer_one_answer_${id}`, type: 'choices', view: 'radio', title: q.title, options: opts() };
    case 'checkbox':
      return { id, key: `answer_multiple_answers_${id}`, type: 'choices', view: 'checks', title: q.title, options: opts() };
    case 'select':
      return { id, key: `answer_dropdown_${id}`, type: 'choices', view: 'select', title: q.title, options: opts() };
    case 'textarea':
      return { id, key: `answer_long_text_${id}`, type: 'text', view: 'textarea', title: q.title };
    case 'number':
      return { id, key: `answer_short_text_${id}`, type: 'text', view: 'textinput', validator: 'decimal', title: q.title };
    case 'email':
      return { id, key: `answer_short_text_${id}`, type: 'text', view: 'textinput', validator: 'email', title: q.title };
    case 'phone':
      return { id, key: `answer_short_text_${id}`, type: 'text', view: 'textinput', validator: 'phone', title: q.title };
    case 'link':
      return { id, key: `answer_short_text_${id}`, type: 'text', view: 'textinput', validator: 'url', title: q.title };
    case 'boolean':
      return { id, key: `answer_boolean_${id}`, type: 'boolean', title: q.title };
    case 'date':
      return { id, key: `answer_date_${id}`, type: 'date', title: q.title };
    case 'scale':
      return {
        id, key: `answer_matrix_${id}`, type: 'matrix', title: q.title,
        rows: (q.rows || ['Оценка']).map((label) => ({ id: newId(), label, slug: `matrix_row_${newId()}` })),
        columns: (q.columns || ['1', '2', '3', '4', '5']).map((label) => ({ id: newId(), label, slug: `matrix_column_${newId()}` })),
      };
    default:
      return { id, key: `answer_short_text_${id}`, type: 'text', view: 'textinput', title: q.title };
  }
}

/**
 * Дописать вопросы в уже существующую форму, не пересобирая её.
 * Ссылка на заполнение остаётся прежней — это главное: пересборка даёт новый surveyId,
 * а разосланная ссылка умирает.
 */
async function insertQuestions(call, surveyId, questions, { page = 1, position = 0 } = {}) {
  const info = await call('getSurveyInfo', { surveyId });
  const pages = (info.questions?.pages || []).map((p) => p.id).filter(Boolean);
  const pageId = pages[page - 1];
  if (!pageId) throw new Error(`в форме ${surveyId} нет страницы ${page} (всего ${pages.length})`);

  let done = 0;
  for (const q of questions) {
    const payload = questionPayload(q);
    const saved = await call('addSurveyQuestion', {
      surveyId, page: pageId, position: position + done, question: payload,
    });
    if (q.required || q.description) {
      await call('updateSurveyQuestion', {
        surveyId,
        question: { ...saved, ...(q.required ? { required: true } : {}), ...(q.description ? { description: q.description } : {}) },
      });
    }
    done++;
    process.stderr.write(`  [+${done}] ${q.title.slice(0, 62)}\n`);
  }

  // сверка порядка: сервер обязан был сдвинуть остальные вопросы вниз.
  // Заголовки лежат не в getSurveyInfo, а в surveyQuestionsLA.questionsMap.
  const after = await call('surveyQuestionsLA', { surveyId });
  const map = after.questionsMap || {};
  const head = (after.nodes?.filter((n) => n.type === 'page')[page - 1]?.items || [])
    .slice(0, questions.length + 1)
    .map((it) => {
      const q = map[`q_${it.id}`];
      return q ? `${q.title}${q.required ? ' *' : ''}` : String(it.id);
    });
  return { surveyId, inserted: done, page, position, headAfter: head, editUrl: `https://forms.yandex.ru/admin/${surveyId}/edit` };
}

async function buildForm(call, spec, { publish = false } = {}) {
  const created = await call('createFormFromTemplate', { templateId: 'empty_form_v2' });
  const surveyId = created.id;
  await call('updateSurveyInfo', {
    surveyId,
    surveyInfo: { name: spec.title, ...(spec.description ? { description: spec.description } : {}) },
  });

  const info = await call('getSurveyInfo', { surveyId });
  // пустая форма приходит с одной готовой страницей — её и используем под первый раздел
  const existingPages = (info.questions?.pages || []).map((p) => p.id).filter(Boolean);

  let total = 0;
  for (let s = 0; s < spec.sections.length; s++) {
    const sec = spec.sections[s];
    let pageId = existingPages[s];
    if (!pageId) {
      const p = await call('addPage', { surveyId });
      pageId = p.id;
    }
    for (let i = 0; i < sec.questions.length; i++) {
      const q = sec.questions[i];
      const payload = questionPayload(q);
      const saved = await call('addSurveyQuestion', {
        surveyId, page: pageId, position: i, question: payload,
      });
      if (q.required || q.description) {
        await call('updateSurveyQuestion', {
          surveyId,
          question: { ...saved, ...(q.required ? { required: true } : {}), ...(q.description ? { description: q.description } : {}) },
        });
      }
      total++;
      process.stderr.write(`  [${total}] ${q.title.slice(0, 62)}\n`);
    }
  }

  let published = null;
  if (publish) {
    await call('publishSurvey', { surveyId });
    published = `https://forms.yandex.ru/u/${surveyId}/`;
  }
  return {
    surveyId,
    questions: total,
    pages: spec.sections.length,
    editUrl: `https://forms.yandex.ru/admin/${surveyId}/edit`,
    publicUrl: published,
    name: info?.name ?? spec.title,
  };
}

// ── команды ───────────────────────────────────────────────────────────────
const [, , cmd, arg, ...rest] = process.argv;

/**
 * Спека берётся либо по ключу из общего forms.json, либо из своего файла:
 *   build students              — ключ в forms.json
 *   build /путь/опрос.json      — файл с одной спекой {title, sections}
 *   build /путь/опросы.json#icp — файл-словарь, нужный ключ через #
 * Агентам удобнее второе: свой файл в рабочем пространстве команды, а не правка общего.
 */
function loadSpec(ref) {
  if (!ref) throw new Error('нужен ключ из forms.json или путь к файлу спеки');
  const isPath = ref.includes('/') || ref.replace(/#.*$/, '').endsWith('.json');
  if (!isPath) {
    const forms = JSON.parse(readFileSync(join(HERE, 'forms.json'), 'utf8'));
    const spec = forms[ref];
    if (!spec) throw new Error(`нет формы "${ref}" в forms.json (есть: ${Object.keys(forms)})`);
    return spec;
  }
  const [file, key] = ref.split('#');
  const data = JSON.parse(readFileSync(file, 'utf8'));
  if (key) {
    if (!data[key]) throw new Error(`в ${file} нет ключа "${key}" (есть: ${Object.keys(data)})`);
    return data[key];
  }
  if (data.title && data.sections) return data;
  const keys = Object.keys(data);
  if (keys.length === 1) return data[keys[0]];
  throw new Error(`в ${file} несколько форм — укажи ключ: ${file}#${keys[0]} (есть: ${keys})`);
}

function validateSpec(spec) {
  const problems = [];
  if (!spec.title) problems.push('нет title');
  if (!Array.isArray(spec.sections) || !spec.sections.length) problems.push('нет sections[]');
  (spec.sections || []).forEach((s, i) => {
    if (!Array.isArray(s.questions) || !s.questions.length) problems.push(`раздел ${i + 1}: нет questions[]`);
    (s.questions || []).forEach((q, j) => {
      if (!q.title) problems.push(`раздел ${i + 1}, вопрос ${j + 1}: нет title`);
      if (['radio', 'checkbox', 'select'].includes(q.type) && !(q.options || []).length)
        problems.push(`раздел ${i + 1}, вопрос ${j + 1} (${q.type}): нет options[]`);
    });
  });
  if (problems.length) throw new Error('спека не годится:\n  ' + problems.join('\n  '));
  return spec;
}

const run = async () => {
  // проверка спеки браузера не требует — агент может свериться, ничего не создавая
  if (cmd === 'check') {
    const spec = validateSpec(loadSpec(arg));
    return {
      ok: true,
      title: spec.title,
      pages: spec.sections.length,
      questions: spec.sections.reduce((n, s) => n + s.questions.length, 0),
      required: spec.sections.reduce((n, s) => n + s.questions.filter((q) => q.required).length, 0),
    };
  }

  const { ctx, call } = await session();
  try {
    switch (cmd) {
      case 'list':
        return await call('getForms', { page: 0, pageSize: 50 });
      case 'info': {
        const info = await call('getSurveyInfo', { surveyId: arg });
        const qs = await call('surveyQuestionsLA', { surveyId: arg });
        writeFileSync(join(HERE, 'shots', 'info.json'), JSON.stringify({ info, qs }, null, 1));
        return { info, structureKeys: Object.keys(qs), dump: 'shots/info.json' };
      }
      case 'build': {
        const spec = validateSpec(loadSpec(arg));
        return await buildForm(call, spec, { publish: rest.includes('--publish') });
      }
      case 'insert': {
        // insert <surveyId> <спека> [--section N] [--take N] [--page N] [--position N]
        const flag = (name, def) => {
          const i = rest.indexOf(`--${name}`);
          return i >= 0 ? Number(rest[i + 1]) : def;
        };
        const spec = validateSpec(loadSpec(rest[0]));
        const section = flag('section', 1);
        const take = flag('take', 0);
        const src = spec.sections[section - 1];
        if (!src) throw new Error(`в спеке нет раздела ${section} (всего ${spec.sections.length})`);
        const questions = take ? src.questions.slice(0, take) : src.questions;
        return await insertQuestions(call, arg, questions, {
          page: flag('page', 1), position: flag('position', 0),
        });
      }
      case 'publish':
        await call('publishSurvey', { surveyId: arg });
        return { published: `https://forms.yandex.ru/u/${arg}/` };
      case 'delete':
        return await call('deleteSurvey', { surveyId: arg });
      default:
        throw new Error('команды: list | info <id> | check <спека> | build <спека> [--publish] |\n' +
          '          insert <id> <спека> [--section N --take N --page N --position N] | publish <id> | delete <id>\n' +
          '<спека> — ключ из forms.json, путь к своему .json или путь.json#ключ');
    }
  } finally {
    await ctx.close();
  }
};

run()
  .then((r) => { console.log(JSON.stringify(r, null, 1)); process.exit(0); })
  .catch((e) => { console.error('ОШИБКА:', e.message); process.exit(1); });
