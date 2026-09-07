#!/usr/bin/env node
/**
 * Создание форм на forms.yandex.ru под аккаунтом пользователя.
 *
 * Публичного API на создание форм у Яндекса нет — работаем через браузер
 * с отдельным постоянным профилем (реальный профиль Chrome не трогаем: он
 * залочен работающим браузером и его легко испортить).
 *
 *   node yaforms.mjs login            — открыть окно, войти в Яндекс руками (один раз)
 *   node yaforms.mjs recon            — снять состояние конструктора (скриншот + разметка)
 *   node yaforms.mjs create students  — собрать форму по forms.json
 *   node yaforms.mjs create authors
 *
 * Окно по умолчанию видимое: конструктор — SPA, в headless он капризен,
 * да и вход руками иначе не сделать. YAF_HEADLESS=1 — принудительно без окна.
 */

import { chromium } from 'playwright';
import { readFileSync, mkdirSync, existsSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { homedir } from 'os';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROFILE_DIR = join(homedir(), '.yandex-forms-profile');
const SHOT_DIR = join(HERE, 'shots');
const FORMS = JSON.parse(readFileSync(join(HERE, 'forms.json'), 'utf8'));

const HEADLESS = process.env.YAF_HEADLESS === '1';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function open() {
  mkdirSync(PROFILE_DIR, { recursive: true });
  mkdirSync(SHOT_DIR, { recursive: true });
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: HEADLESS,
    channel: 'chrome',
    viewport: { width: 1400, height: 950 },
    args: ['--disable-blink-features=AutomationControlled', '--window-size=1400,1000'],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  return { ctx, page };
}

async function shot(page, name) {
  const p = join(SHOT_DIR, `${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  console.error(`  скриншот: ${p}`);
  return p;
}

/** Залогинен ли профиль в Яндексе */
async function isLoggedIn(page) {
  await page.goto('https://forms.yandex.ru/', { waitUntil: 'domcontentloaded' });
  await sleep(2500);
  const url = page.url();
  if (/passport\.yandex/.test(url)) return false;
  const body = await page.content();
  return !/Войти в аккаунт|Войти с Яндекс ID/i.test(body) || /Мои формы|Создать форму/i.test(body);
}

async function cmdLogin() {
  const { ctx, page } = await open();
  if (await isLoggedIn(page)) {
    console.error('Уже залогинен — вход не нужен.');
    await shot(page, 'login-ok');
    await ctx.close();
    return { loggedIn: true };
  }
  await page.goto('https://passport.yandex.ru/auth', { waitUntil: 'domcontentloaded' });
  console.error('\n>>> Войди в Яндекс в открывшемся окне. Жду до 5 минут...\n');
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await sleep(3000);
    if (!/passport\.yandex/.test(page.url())) break;
  }
  const ok = await isLoggedIn(page);
  await shot(page, ok ? 'login-ok' : 'login-fail');
  await ctx.close();
  return { loggedIn: ok };
}

/** Разведка: что вообще показывает конструктор и какие в нём элементы */
async function cmdRecon() {
  const { ctx, page } = await open();
  const logged = await isLoggedIn(page);
  if (!logged) {
    await shot(page, 'recon-not-logged');
    await ctx.close();
    return { loggedIn: false, hint: 'сначала: node yaforms.mjs login' };
  }
  await shot(page, 'recon-list');

  // пробуем открыть создание формы
  const createSelectors = [
    'text=Создать форму',
    'text=Новая форма',
    'button:has-text("Создать")',
    'a:has-text("Создать")',
  ];
  let opened = null;
  for (const sel of createSelectors) {
    const el = page.locator(sel).first();
    if (await el.count().catch(() => 0)) {
      await el.click({ timeout: 5000 }).catch(() => {});
      await sleep(4000);
      opened = sel;
      break;
    }
  }
  await shot(page, 'recon-after-create');

  const dump = await page.evaluate(() => {
    const pick = (el) => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role'),
      type: el.getAttribute('type'),
      cls: (el.className || '').toString().slice(0, 90),
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
      text: (el.innerText || el.value || '').trim().slice(0, 60),
    });
    const nodes = [...document.querySelectorAll('button,[role="button"],input,textarea,[contenteditable="true"],a')];
    return {
      url: location.href,
      title: document.title,
      controls: nodes.slice(0, 120).map(pick).filter((c) => c.text || c.testid),
    };
  });
  writeFileSync(join(SHOT_DIR, 'recon.json'), JSON.stringify(dump, null, 1));
  await ctx.close();
  return { loggedIn: true, opened, url: dump.url, controls: dump.controls.length, dump: join(SHOT_DIR, 'recon.json') };
}

/** Разведка второго уровня: полный список типов и редактор вопроса */
async function cmdRecon2(url) {
  if (!url) throw new Error('recon2 <url формы /admin/<id>/edit>');
  const { ctx, page } = await open();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await sleep(4000);

  // 1. полный список типов вопросов
  const all = page.locator('text=Показать все вопросы').first();
  if (await all.count()) {
    await all.click().catch(() => {});
    await sleep(1500);
  }
  await shot(page, 'r2-all-types');
  const types = await page.evaluate(() =>
    [...document.querySelectorAll('button,[role="button"],li,div')]
      .map((e) => (e.innerText || '').trim())
      .filter((t) => t && t.length < 40 && !t.includes('\n'))
      .filter((v, i, a) => a.indexOf(v) === i)
      .slice(0, 80)
  );

  // 2. открыть редактор вопроса типа «Список»
  const list = page.locator('text=Список').first();
  if (await list.count()) {
    await list.click().catch(() => {});
    await sleep(2500);
  }
  await shot(page, 'r2-list-editor');

  const editor = await page.evaluate(() => {
    const pick = (el) => ({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      ph: el.getAttribute('placeholder'),
      aria: el.getAttribute('aria-label'),
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
      name: el.getAttribute('name'),
      text: (el.innerText || el.value || '').trim().slice(0, 50),
    });
    return {
      url: location.href,
      inputs: [...document.querySelectorAll('input,textarea,[contenteditable="true"]')].map(pick),
      buttons: [...document.querySelectorAll('button,[role="button"]')]
        .map(pick)
        .filter((b) => b.text || b.aria || b.testid)
        .slice(0, 60),
    };
  });
  writeFileSync(join(SHOT_DIR, 'recon2.json'), JSON.stringify({ types, editor }, null, 1));
  await ctx.close();
  return { types: types.length, inputs: editor.inputs.length, dump: join(SHOT_DIR, 'recon2.json') };
}

/** Точечная разведка редактора: открыть форму и выбрать тип вопроса по точному тексту */
async function cmdProbe(argStr) {
  const { url, type } = JSON.parse(argStr);
  const { ctx, page } = await open();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await sleep(4000);

  const more = page.getByText('Показать все вопросы', { exact: true });
  if (await more.count()) { await more.click().catch(() => {}); await sleep(1200); }

  const item = page.getByText(type, { exact: true }).first();
  await item.click({ timeout: 8000 });
  await sleep(3000);
  await shot(page, `probe-${type.replace(/\s+/g, '-')}`);

  const dump = await page.evaluate(() => {
    const pick = (el) => ({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      ph: el.getAttribute('placeholder'),
      aria: el.getAttribute('aria-label'),
      testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
      ce: el.getAttribute('contenteditable'),
      text: (el.innerText || el.value || '').trim().slice(0, 60),
    });
    return {
      url: location.href,
      fields: [...document.querySelectorAll('input,textarea,[contenteditable="true"]')].map(pick),
      buttons: [...document.querySelectorAll('button,[role="button"]')].map(pick)
        .filter((b) => b.text || b.aria || b.testid).slice(0, 50),
      labels: [...document.querySelectorAll('label,span')].map((e) => (e.innerText || '').trim())
        .filter((t) => t && t.length < 30).filter((v, i, a) => a.indexOf(v) === i).slice(0, 60),
    };
  });
  writeFileSync(join(SHOT_DIR, 'probe.json'), JSON.stringify(dump, null, 1));
  await ctx.close();
  return { url: dump.url, fields: dump.fields.length, dump: join(SHOT_DIR, 'probe.json') };
}

// ── сборка формы ──────────────────────────────────────────────────────────
// Типы в конструкторе Яндекса, на которые ложится спека
const TYPE_LABEL = {
  radio: 'Один вариант',
  checkbox: 'Несколько вариантов',
  text: 'Короткий текст',
  textarea: 'Длинный текст',
  number: 'Число',
};

const Q_TITLE = 'textarea[placeholder="Введите текст вопроса"]';
const Q_OPTION = 'textarea[placeholder="Введите вариант ответа"]';

async function pickType(page, label) {
  // список типов свёрнут — раскрываем
  const more = page.getByText('Показать все вопросы', { exact: true });
  if (await more.count().catch(() => 0)) {
    await more.first().click().catch(() => {});
    await sleep(600);
  }
  await page.getByText(label, { exact: true }).first().click({ timeout: 10000 });
  await sleep(900);
}

async function addQuestion(page, q, isFirstOnPage) {
  if (!isFirstOnPage) {
    await page.getByRole('button', { name: 'Добавить вопрос' }).first().click({ timeout: 10000 });
    await sleep(700);
  }
  await pickType(page, TYPE_LABEL[q.type] || TYPE_LABEL.text);

  await page.locator(Q_TITLE).last().fill(q.title);
  await sleep(250);

  if (q.required) {
    const toggle = page.getByText('Обязательный', { exact: true }).last();
    if (await toggle.count().catch(() => 0)) await toggle.click().catch(() => {});
    await sleep(200);
  }

  if (q.options?.length) {
    const boxes = page.locator(Q_OPTION);
    // свежая карточка уже содержит один пустой вариант — заполняем его,
    // остальные добавляем по одному, дожидаясь появления поля (React рисует не мгновенно)
    await boxes.last().fill(q.options[0]);
    for (let i = 1; i < q.options.length; i++) {
      const before = await boxes.count();
      await page.getByText('Добавить вариант', { exact: true }).last().click({ timeout: 8000 });
      for (let t = 0; t < 40 && (await boxes.count()) === before; t++) await sleep(100);
      await boxes.last().fill(q.options[i]);
      await sleep(80);
    }
  }
  await sleep(300);
}

async function cmdCreate(key) {
  const spec = FORMS[key];
  if (!spec) throw new Error(`нет формы "${key}" в forms.json`);
  const { ctx, page } = await open();

  await page.goto('https://forms.yandex.ru/', { waitUntil: 'domcontentloaded' });
  await sleep(2500);
  await page.getByText('Создать пустую форму', { exact: true }).first().click({ timeout: 15000 });
  await sleep(5000);
  const editUrl = page.url();
  console.error(`форма создана: ${editUrl}`);

  // название формы
  const heading = page.getByText('Новая форма', { exact: true }).first();
  if (await heading.count().catch(() => 0)) {
    await heading.click().catch(() => {});
    await sleep(500);
    const titleBox = page.locator('input,textarea').filter({ hasNot: page.locator('[type=file]') }).first();
    await titleBox.fill(spec.title).catch(() => {});
    await page.keyboard.press('Tab').catch(() => {});
    await sleep(600);
  }

  const LIMIT = parseInt(process.env.YAF_LIMIT || '0', 10); // 0 = без ограничения (для обкатки)
  let n = 0;
  for (let s = 0; s < spec.sections.length; s++) {
    if (LIMIT && n >= LIMIT) break;
    const sec = spec.sections[s];
    if (s > 0) {
      await page.getByRole('button', { name: 'Добавить страницу' }).first().click({ timeout: 10000 });
      await sleep(1500);
    }
    for (let i = 0; i < sec.questions.length; i++) {
      if (LIMIT && n >= LIMIT) break;
      const first = s === 0 && i === 0; // на самой первой странице форма уже ждёт выбора типа
      await addQuestion(page, sec.questions[i], first);
      n++;
      console.error(`  [${n}] ${sec.questions[i].title.slice(0, 60)}`);
    }
  }

  await shot(page, `create-${key}`);
  await sleep(1500);
  await ctx.close();
  return { key, questions: n, editUrl };
}

/** Снять сетевые вызовы конструктора при добавлении вопроса */
async function cmdSniff(url) {
  if (!url) throw new Error('sniff <url формы /admin/<id>/edit>');
  const { ctx, page } = await open();
  const calls = [];
  page.on('request', (r) => {
    const u = r.url();
    if (!/forms\.yandex/.test(u)) return;
    if (['xhr', 'fetch'].includes(r.resourceType())) {
      calls.push({ method: r.method(), url: u, body: (r.postData() || '').slice(0, 4000) });
    }
  });
  page.on('response', async (r) => {
    const u = r.url();
    if (!/forms\.yandex/.test(u)) return;
    if (['xhr', 'fetch'].includes(r.request().resourceType())) {
      const hit = calls.find((c) => c.url === u && !c.status);
      if (hit) {
        hit.status = r.status();
        hit.resp = (await r.text().catch(() => '')).slice(0, 1500);
      }
    }
  });

  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await sleep(4500);
  calls.length = 0; // интересует только то, что произойдёт дальше

  // добавляем один вопрос-список руками и смотрим, что улетает на сервер
  const add = page.getByRole('button', { name: 'Добавить вопрос' });
  if (await add.count().catch(() => 0)) { await add.last().click().catch(() => {}); await sleep(800); }
  const more = page.getByText('Показать все вопросы', { exact: true });
  if (await more.count().catch(() => 0)) { await more.first().click().catch(() => {}); await sleep(600); }
  await page.getByText('Один вариант', { exact: true }).first().click({ timeout: 10000 });
  await sleep(2000);
  await page.locator(Q_TITLE).last().fill('ПРОБА вопроса');
  await page.keyboard.press('Tab');
  await sleep(2500);
  await page.locator(Q_OPTION).last().fill('ПРОБА варианта');
  await page.keyboard.press('Tab');
  await sleep(3000);

  writeFileSync(join(SHOT_DIR, 'sniff.json'), JSON.stringify(calls, null, 1));
  await ctx.close();
  return { calls: calls.length, dump: join(SHOT_DIR, 'sniff.json') };
}

/** Полная разведка API: прокликать основные возможности и снять все вызовы gateway */
async function cmdSniffAll() {
  const { ctx, page } = await open();
  const calls = [];
  const want = (r) => /forms\.yandex\.ru\/admin\/gateway/.test(r.url());
  page.on('request', (r) => {
    if (want(r)) calls.push({
      method: r.method(),
      op: r.url().split('/').pop(),
      url: r.url(),
      headers: Object.fromEntries(Object.entries(r.headers()).filter(([k]) =>
        /csrf|token|x-|content-type/i.test(k) && !/user-agent|sec-|accept-lang/i.test(k))),
      body: (r.postData() || '').slice(0, 3000),
    });
  });
  page.on('response', async (r) => {
    if (!want(r.request())) return;
    const hit = [...calls].reverse().find((c) => c.url === r.url() && c.status === undefined);
    if (hit) { hit.status = r.status(); hit.resp = (await r.text().catch(() => '')).slice(0, 1200); }
  });

  const step = async (name, fn) => {
    const mark = { step: name, at: calls.length };
    try { await fn(); } catch (e) { mark.error = e.message; }
    await sleep(1800);
    calls.push({ MARK: name, ...mark });
  };

  await page.goto('https://forms.yandex.ru/', { waitUntil: 'domcontentloaded' });
  await sleep(2500);

  await step('createForm', async () => {
    await page.getByText('Создать пустую форму', { exact: true }).first().click({ timeout: 15000 });
    await sleep(4000);
  });
  const formUrl = page.url();

  const pick = async (label) => {
    const more = page.getByText('Показать все вопросы', { exact: true });
    if (await more.count().catch(() => 0)) { await more.first().click().catch(() => {}); await sleep(500); }
    await page.getByText(label, { exact: true }).first().click({ timeout: 8000 });
    await sleep(1500);
  };
  const addQ = async (label) => {
    const add = page.getByRole('button', { name: 'Добавить вопрос' });
    if (await add.count().catch(() => 0)) { await add.last().click().catch(() => {}); await sleep(700); }
    await pick(label);
  };

  await step('q_radio', async () => { await pick('Один вариант'); });
  await step('q_radio_title', async () => {
    await page.locator(Q_TITLE).last().fill('Проба радио'); await page.keyboard.press('Tab');
  });
  await step('q_radio_required', async () => {
    await page.getByText('Обязательный', { exact: true }).last().click();
  });
  await step('q_checkbox', async () => { await addQ('Несколько вариантов'); });
  await step('q_short', async () => { await addQ('Короткий текст'); });
  await step('q_long', async () => { await addQ('Длинный текст'); });
  await step('q_number', async () => { await addQ('Число'); });
  await step('q_dropdown', async () => { await addQ('Выпадающий список'); });
  await step('q_scale', async () => { await addQ('Оценка по шкале'); });
  await step('q_yesno', async () => { await addQ('Да/Нет'); });
  await step('q_date', async () => { await addQ('Дата'); });
  await step('addPage', async () => {
    await page.getByRole('button', { name: 'Добавить страницу' }).first().click({ timeout: 8000 });
  });
  await step('formTitle', async () => {
    const h = page.getByText('Новая форма', { exact: true }).first();
    await h.click(); await sleep(400);
    await page.keyboard.type('Проба названия'); await page.keyboard.press('Tab');
  });
  await step('publish', async () => {
    await page.getByRole('button', { name: 'Опубликовать' }).first().click({ timeout: 8000 });
    await sleep(2500);
  });
  await shot(page, 'sniffall-final');

  writeFileSync(join(SHOT_DIR, 'sniff-all.json'), JSON.stringify({ formUrl, calls }, null, 1));
  await ctx.close();
  const ops = [...new Set(calls.filter((c) => c.op).map((c) => `${c.method} ${c.op}`))];
  return { formUrl, calls: calls.length, ops, dump: join(SHOT_DIR, 'sniff-all.json') };
}

const CMDS = { login: cmdLogin, recon: cmdRecon, recon2: cmdRecon2, probe: cmdProbe, create: cmdCreate, sniff: cmdSniff, sniffall: cmdSniffAll };

const [, , cmd, arg] = process.argv;
if (!CMDS[cmd]) {
  console.error('Команды: login | recon | create <students|authors>');
  console.error('Формы в спеке:', Object.keys(FORMS).join(', '));
  process.exit(1);
}
CMDS[cmd](arg)
  .then((r) => {
    console.log(JSON.stringify(r, null, 1));
    process.exit(0);
  })
  .catch((e) => {
    console.error('ОШИБКА:', e.message);
    process.exit(1);
  });
