#!/usr/bin/env node
/**
 * Драйвер Пикабу — транспорт действий под живой сессией Chrome.
 *
 *   node driver.mjs login                     — открыть Chrome и войти руками
 *   node driver.mjs whoami                    — кто я по сессии
 *   node driver.mjs post '{"path":"/ajax/comments_actions.php","data":{...}}'
 *   node driver.mjs get  '{"path":"/ajax/gtpost_actions.php","params":{...}}'
 *   node driver.mjs open '{"url":"https://pikabu.ru/add"}'
 *   node driver.mjs serve                     — построчный режим для pikabu.py
 *
 * Результат — JSON в stdout, всё служебное — в stderr (иначе потребитель
 * получит мусор вместо ответа: грабля, на которой уже спотыкался gramjs).
 */
import {
  action, actionGet, initParams, currentUser,
  getContext, saveState, hasSession, closeBrowser, BASE,
} from "./browser.mjs";

const [, , tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

const log = (...a) => console.error(...a);
const emit = (obj) =>
  new Promise((res) => process.stdout.write(JSON.stringify(obj, null, 2) + "\n", res));

/** Ручной вход: открываем настоящее окно и ждём, пока Пикабу признает сессию. */
async function login() {
  const ctx = await getContext(false); // headed
  const page = await ctx.newPage();
  await page.goto(BASE + "/", { waitUntil: "commit" });
  log("Открыл окно Chrome. Войди в свой аккаунт — жду до 5 минут…");
  log("Вкладку можно листать: слежу за сессией, а не за конкретной страницей.");

  const deadline = Date.now() + 5 * 60 * 1000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let params = null;

  while (Date.now() < deadline) {
    await sleep(3000);
    try {
      const p = await initParams(BASE + "/", true);
      if (p && Number(p.userID) > 0) {
        params = p;
        break;
      }
    } catch (e) {
      const msg = String(e?.message || e);
      if (/has been closed|Target closed|browser has disconnected/i.test(msg)) {
        return { ok: false, error: "окно Chrome закрыли до завершения входа" };
      }
    }
  }

  if (!params) {
    await closeBrowser();
    return { ok: false, error: "вход не завершён за 5 минут" };
  }
  const name = await currentUser().catch(() => null);
  await saveState();
  await closeBrowser();
  return {
    ok: true,
    user_id: Number(params.userID),
    user: name,
    note: "сессия сохранена в ~/.pikabu-session",
  };
}

async function whoami() {
  if (!hasSession()) return { ok: false, error: "not_logged_in" };
  const p = await initParams();
  if (!p) return { ok: false, error: "не удалось прочитать параметры страницы" };
  if (!(Number(p.userID) > 0)) return { ok: false, error: "not_logged_in" };
  // Ник берём из параметров страницы, а не из вёрстки шапки: разметка
  // header-меню меняется от темы к теме, а userName есть всегда.
  const name = p.userName || (await currentUser().catch(() => null));
  return {
    ok: true,
    user_id: Number(p.userID),
    user: name,
    karma: p.userKarma,
    signup_date: p.userSignupDate,
    is_new_user: Boolean(p.isNewUser),
    is_confirmed: Boolean(p.isConfirmed),
    is_golden: Boolean(p.isGoldenUser),
    is_banned: Boolean(p.isUserBanned),
    // «медленный режим» — потолок комментариев в сутки для свежих аккаунтов
    slow_mode: Boolean(p.isSlowModeEnabled),
    is_deleted: Boolean(p.isDeleted),
  };
}

/** Открыть страницу глазами (проверить капчу, дописать пост руками). */
async function open(url) {
  const ctx = await getContext(false);
  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded" });
  log("Страница открыта. Окно останется до Ctrl+C.");
  await page.waitForTimeout(10 * 60 * 1000);
  return { ok: true };
}

/**
 * Постоянный режим: браузер поднимается один раз, запросы приходят строками
 * JSON в stdin, ответы уходят строками в stdout.
 */
async function serve() {
  log("serve: жду запросы построчно в stdin");
  let buf = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    buf += chunk;
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      let req;
      try {
        req = JSON.parse(line);
      } catch {
        process.stdout.write(JSON.stringify({ _error: "плохой JSON" }) + "\n");
        continue;
      }
      let result;
      try {
        if (req.tool === "post") result = await action(req.args?.path, req.args?.data || {});
        else if (req.tool === "get") result = await actionGet(req.args?.path, req.args?.params || {});
        else if (req.tool === "whoami") result = await whoami();
        else if (req.tool === "params") result = await initParams(req.args?.url);
        else if (req.tool === "quit") {
          process.stdout.write(JSON.stringify({ id: req.id, result: { ok: true } }) + "\n");
          await closeBrowser();
          process.exit(0);
        } else result = { _error: `неизвестный tool: ${req.tool}` };
      } catch (e) {
        result = { _error: String(e?.message || e).split("\n")[0] };
      }
      process.stdout.write(JSON.stringify({ id: req.id, result }) + "\n");
    }
  }
  await closeBrowser();
}

async function run() {
  if (tool === "serve") {
    await serve();
    return;
  }
  let result;
  switch (tool) {
    case "login":
      result = await login();
      break;
    case "whoami":
      result = await whoami();
      break;
    case "post":
      if (!hasSession()) { result = { _error: "not_logged_in" }; break; }
      result = await action(args.path, args.data || {});
      break;
    case "get":
      result = await actionGet(args.path, args.params || {});
      break;
    case "params":
      result = await initParams(args.url);
      break;
    case "open":
      result = await open(args.url);
      break;
    default:
      result = {
        error: "неизвестная команда",
        commands: ["login", "whoami", "get", "post", "params", "open", "serve"],
      };
  }
  await emit(result);
  await closeBrowser();
  process.exit(0);
}

run().catch(async (e) => {
  await emit({ _error: String(e?.message || e) });
  await closeBrowser();
  process.exit(1);
});
