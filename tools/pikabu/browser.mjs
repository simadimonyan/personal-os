/**
 * Браузер для Пикабу — системный Chrome с твоей живой сессией.
 *
 * Пароль нигде не хранится: вход делается руками один раз командой `login`,
 * дальше состояние живёт в ~/.pikabu-session/auth-state.json (как у hh.ru
 * и Reddit).
 *
 * Читать Пикабу можно и без браузера — это делает pikabu.py обычным
 * urllib. Браузер нужен только для действий: посты, комментарии, входящие.
 * Причина: любое действие требует пары «кука сессии + X-Csrf-Token», а
 * токен выдаётся вместе со страницей и меняется.
 */
import { chromium } from "playwright";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const STATE_DIR = join(homedir(), ".pikabu-session");
export const STATE_FILE = join(STATE_DIR, "auth-state.json");
export const BASE = "https://pikabu.ru";

// Настоящий Chrome, а не Playwright-Chromium: у Пикабу перед сайтом стоит
// антибот, и дефолтный автоматизационный отпечаток он встречает капчей.
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

let browser = null;
let context = null;

export async function getBrowser(headless = true) {
  if (!browser || !browser.isConnected()) {
    browser = await chromium.launch({
      headless,
      channel: process.env.PW_CHANNEL || "chrome",
      args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    });
  }
  return browser;
}

export async function getContext(headless = true) {
  if (context) return context;
  const b = await getBrowser(headless);
  if (!existsSync(STATE_DIR)) mkdirSync(STATE_DIR, { recursive: true });
  const opts = {
    userAgent: UA,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    viewport: { width: 1280, height: 900 },
  };
  if (existsSync(STATE_FILE)) opts.storageState = STATE_FILE;
  context = await b.newContext(opts);
  return context;
}

export async function saveState() {
  if (!context) return false;
  if (!existsSync(STATE_DIR)) mkdirSync(STATE_DIR, { recursive: true });
  await context.storageState({ path: STATE_FILE });
  return true;
}

export function hasSession() {
  return existsSync(STATE_FILE);
}

/**
 * Страница-исполнитель. Запросы идут ИЗ НЕЁ, а не из context.request:
 * так совпадает всё сразу — кукисы, Referer, отпечаток соединения и
 * csrf-токен именно этой загруженной страницы.
 */
let apiPage = null;

/**
 * Переход на страницу. Ждём не "domcontentloaded", а появления блока
 * initParams: на Пикабу висит десяток рекламных сеток, и полная загрузка
 * документа регулярно не укладывается в минуту, хотя сам сайт уже готов.
 */
async function goto(page, url) {
  let lastErr = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await page.goto(url, { waitUntil: "commit", timeout: 45000 });
      await page.waitForSelector('script[data-entry="initParams"]', {
        state: "attached", timeout: 30000,
      });
      return true;
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

async function getApiPage() {
  if (apiPage && !apiPage.isClosed()) return apiPage;
  const ctx = await getContext();
  apiPage = await ctx.newPage();
  await goto(apiPage, BASE + "/");
  return apiPage;
}

/**
 * Параметры страницы: кто я, токен, ограничения новичка.
 * fresh=true перезагружает страницу: блок initParams рисуется сервером один
 * раз, и без перезагрузки опрос вечно видит анонима, даже когда вход уже
 * состоялся в соседней вкладке.
 */
export async function initParams(url = BASE + "/", fresh = false) {
  const page = await getApiPage();
  if (fresh || url !== page.url()) {
    await goto(page, url).catch(() => {});
  }
  return page.evaluate(() => {
    const el = document.querySelector('script[data-entry="initParams"]');
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch {
      return null;
    }
  });
}

/** Ник текущего пользователя — из шапки залогиненной страницы. */
export async function currentUser() {
  const page = await getApiPage();
  return page.evaluate(() => {
    const a = document.querySelector('a.header-menu__item[href*="/@"], a.header-right__item[href*="/@"], .header__user a[href*="/@"]');
    const href = a?.getAttribute("href") || "";
    const m = href.match(/\/@([^/?#]+)/);
    if (m) return decodeURIComponent(m[1]);
    const meta = document.querySelector('meta[name="user-name"]');
    return meta?.getAttribute("content") || null;
  });
}

// Пикабу режет частые запросы: серия без пауз ловит «Не так быстро».
let lastCall = 0;
const MIN_GAP_MS = 800;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Запрос под сессией из настоящей вкладки. Возвращает JSON либо {_error}. */
async function fetchJson(path, opts = {}) {
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const gap = MIN_GAP_MS - (Date.now() - lastCall);
    if (gap > 0) await sleep(gap);
    last = await fetchJsonOnce(path, opts);
    const status = last?._status;
    if (status !== 429 && status !== 503 && status !== 403) return last;
    await sleep(5000 * (attempt + 1));
  }
  return last;
}

async function fetchJsonOnce(path, { method = "POST", form = null } = {}) {
  const page = await getApiPage();
  const url = path.startsWith("http") ? path : BASE + path;
  const res = await page.evaluate(
    async ({ url, method, form }) => {
      const cfgEl = document.querySelector('script[data-entry="initParams"]');
      let csrf = "";
      try {
        csrf = JSON.parse(cfgEl.textContent).csrfToken || "";
      } catch { /* токен необязателен для чтения */ }
      try {
        const headers = {
          "X-Requested-With": "XMLHttpRequest",
          "X-Csrf-Token": csrf,
          "X-Timezone-Offset": String(-new Date().getTimezoneOffset()),
        };
        const opts = { method, credentials: "include", headers };
        if (form) {
          const body = new URLSearchParams();
          for (const [k, v] of Object.entries(form)) {
            if (v === undefined || v === null) continue;
            body.append(k, typeof v === "boolean" ? (v ? "1" : "0") : String(v));
          }
          opts.body = body.toString();
          headers["Content-Type"] =
            "application/x-www-form-urlencoded; charset=UTF-8";
        }
        const r = await fetch(url, opts);
        return { status: r.status, text: await r.text() };
      } catch (e) {
        return { status: 0, text: String(e) };
      }
    },
    { url, method, form }
  );
  if (res.status !== 200) {
    return { _error: `HTTP ${res.status}`, _status: res.status, _body: res.text.slice(0, 300) };
  }
  lastCall = Date.now();
  try {
    return JSON.parse(res.text);
  } catch {
    const loggedOut = /Авторизация|Вход на Пикабу/i.test(res.text.slice(0, 3000));
    return { _error: loggedOut ? "not_logged_in" : "not_json", _body: res.text.slice(0, 300) };
  }
}

/**
 * Действие на сайте. Пикабу отвечает конвертом
 * {result, message, message_code, data} — разворачиваем его здесь,
 * чтобы наверху не разбирать это в каждой команде.
 */
export async function action(path, form = {}) {
  const res = await fetchJson(path, { method: "POST", form });
  if (res?._error) return res;
  if (res && typeof res === "object" && "result" in res) {
    if (!res.result) {
      return {
        _error: "refused",
        message: res.message || "",
        message_code: res.message_code,
        data: res.data,
      };
    }
    return res.data ?? {};
  }
  return res;
}

export async function actionGet(path, params = {}) {
  const url = new URL(path.startsWith("http") ? path : BASE + path);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const res = await fetchJson(url.toString(), { method: "GET" });
  if (res?._error) return res;
  if (res && typeof res === "object" && "result" in res) {
    if (!res.result) return { _error: "refused", message: res.message || "" };
    return res.data ?? {};
  }
  return res;
}

export async function closeBrowser() {
  apiPage = null;
  if (context) {
    await context.close().catch(() => {});
    context = null;
  }
  if (browser) {
    await browser.close().catch(() => {});
    browser = null;
  }
}
