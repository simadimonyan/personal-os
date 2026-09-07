/**
 * Браузер для Reddit — системный Chrome с твоей живой сессией.
 *
 * Никаких client_id/секретов: инструмент ходит на old.reddit.com под теми же
 * кукисами, что и ты сам. Сессия сохраняется один раз командой `login`
 * и живёт в ~/.reddit-session/auth-state.json.
 *
 * old.reddit вместо нового: там простые HTML-формы и стабильные .json-ручки,
 * которые не переезжают с каждым редизайном.
 */
import { chromium } from "playwright";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const STATE_DIR = join(homedir(), ".reddit-session");
export const STATE_FILE = join(STATE_DIR, "auth-state.json");
export const BASE = "https://old.reddit.com";

// Настоящий Chrome, а не Playwright-Chromium: у Reddit жёсткая фильтрация
// по User-Agent, на дефолтном автоматизационном прилетает 403 Blocked.
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
    locale: "en-US",
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
 * Страница-исполнитель. Запросы делаются ИЗ НЕЁ, а не из
 * context.request: Reddit фильтрует по отпечатку соединения, и внешние
 * запросы получают 403 Blocked даже с браузерным User-Agent и живыми
 * кукисами. Изнутри настоящей вкладки те же ручки отдают 200 (проверено).
 */
let apiPage = null;

async function getApiPage() {
  if (apiPage && !apiPage.isClosed()) return apiPage;
  const ctx = await getContext();
  apiPage = await ctx.newPage();
  await apiPage.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 60000 });
  return apiPage;
}

// Reddit режет частые запросы: после ~60 подряд начинает отдавать 403/429 на
// всё подряд. Держим минимальный интервал и повторяем с нарастающей паузой —
// иначе половина проверки сабреддитов выглядит как «сабреддит не существует».
let lastCall = 0;
const MIN_GAP_MS = 700;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Запрос под сессией из настоящей вкладки. Возвращает JSON либо {_error}. */
async function fetchJson(path, opts = {}) {
  let last = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    const gap = MIN_GAP_MS - (Date.now() - lastCall);
    if (gap > 0) await sleep(gap);
    last = await fetchJsonOnce(path, opts);
    const status = last?._status;
    if (status !== 403 && status !== 429 && status !== 503) return last;
    // 403 здесь — почти всегда троттлинг, а не приватность
    await sleep(4000 * (attempt + 1));
  }
  return last;
}

async function fetchJsonOnce(path, { method = "GET", form = null, headers = {} } = {}) {
  const page = await getApiPage();
  const url = path.startsWith("http") ? path : BASE + path;
  const res = await page.evaluate(
    async ({ url, method, form, headers }) => {
      try {
        const opts = { method, credentials: "include", headers: { Accept: "application/json", ...headers } };
        if (form) {
          opts.body = new URLSearchParams(form).toString();
          opts.headers["Content-Type"] = "application/x-www-form-urlencoded";
        }
        const r = await fetch(url, opts);
        return { status: r.status, text: await r.text() };
      } catch (e) {
        return { status: 0, text: String(e) };
      }
    },
    { url, method, form, headers }
  );
  if (res.status !== 200) {
    return { _error: `HTTP ${res.status}`, _status: res.status, _body: res.text.slice(0, 200) };
  }
  lastCall = Date.now();
  try {
    return JSON.parse(res.text);
  } catch {
    const loggedOut = /login|Log in/i.test(res.text.slice(0, 2000));
    return { _error: loggedOut ? "not_logged_in" : "not_json" };
  }
}

export async function apiGet(path, params = {}) {
  const url = new URL(path.startsWith("http") ? path : BASE + path);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  url.searchParams.set("raw_json", "1");
  return fetchJson(url.toString());
}

/** Токен формы (modhash) — старый Reddit требует его на любое действие. */
export async function getModhash() {
  const me = await apiGet("/api/me.json");
  const hash = me?.data?.modhash;
  if (!hash) return null;
  return hash;
}

/** POST формы под сессией (submit, comment). */
export async function apiPost(path, data = {}) {
  const modhash = await getModhash();
  if (!modhash) return { _error: "not_logged_in" };
  return fetchJson(path, {
    method: "POST",
    form: { api_type: "json", uh: modhash, ...data },
    headers: { "X-Modhash": modhash },
  });
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
