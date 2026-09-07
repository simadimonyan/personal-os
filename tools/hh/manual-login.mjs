#!/usr/bin/env node
/**
 * Ручной вход на hh.ru с сохранением сессии.
 *
 * Открывает ВИДИМОЕ окно Chrome на странице входа hh, ждёт пока пользователь
 * авторизуется вручную (ловит cookie hhtoken), затем сохраняет storageState в
 * ~/.hh-mcp/auth-state.json — тот же файл, что использует драйвер.
 *
 * Контекст чистый (без старой сессии), чтобы войти именно в нужный аккаунт.
 * Таймаут ожидания входа — 8 минут.
 */
import { chromium } from "playwright";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const STATE_DIR = join(homedir(), ".hh-mcp");
const STATE_FILE = join(STATE_DIR, "auth-state.json");
const TIMEOUT_MS = 10 * 60 * 1000;
const POLL_MS = 3000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Надёжный признак входа: cookie hhrole. У гостя — "anonymous", у вошедшего
// соискателя — "applicant". cookie hhtoken hh ставит и анонимам — ему нельзя верить.
async function isLoggedIn(context) {
  const cookies = await context.cookies();
  const role = cookies.find((c) => c.name === "hhrole");
  return Boolean(role && role.value && role.value !== "anonymous");
}

async function main() {
  if (!existsSync(STATE_DIR)) mkdirSync(STATE_DIR, { recursive: true });

  const browser = await chromium.launch({
    headless: false,
    channel: process.env.PW_CHANNEL || "chrome",
    args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
  });
  // Чистый контекст — без подгрузки старой сессии.
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
  });
  const page = await context.newPage();
  await page.goto("https://hh.ru/account/login?backurl=%2Fapplicant%2Fresumes", {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });

  console.log("WAITING_FOR_LOGIN");
  const deadline = Date.now() + TIMEOUT_MS;
  let ok = false;
  while (Date.now() < deadline) {
    if (await isLoggedIn(context)) {
      ok = true;
      break;
    }
    await sleep(POLL_MS);
  }

  if (!ok) {
    console.error(JSON.stringify({ error: "timeout: вход не выполнен за 8 минут" }));
    await browser.close();
    process.exit(1);
  }

  // дать hh догрузить профиль и доп. куки после входа
  await sleep(3000);
  await context.storageState({ path: STATE_FILE });
  console.log(JSON.stringify({ saved: STATE_FILE }));
  await browser.close();
  process.exit(0);
}

main().catch(async (e) => {
  console.error(JSON.stringify({ error: String(e && e.message || e) }));
  process.exit(1);
});
