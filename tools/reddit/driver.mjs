#!/usr/bin/env node
/**
 * Reddit driver — транспорт под живой сессией Chrome.
 *
 *   node driver.mjs login                 — открыть Chrome и войти руками (один раз)
 *   node driver.mjs whoami                — кто я по сессии
 *   node driver.mjs get  '{"path":"/r/languagelearning/about.json"}'
 *   node driver.mjs post '{"path":"/api/submit","data":{...}}'
 *   node driver.mjs open '{"url":"https://old.reddit.com/r/x/submit"}'
 *
 * Результат — JSON в stdout, служебные сообщения — в stderr (иначе потребитель
 * получит мусор вместо ответа: та же грабля, что была с gramjs в телеграме).
 */
import { apiGet, apiPost, getContext, saveState, hasSession, closeBrowser, BASE }
  from "./browser.mjs";

const [, , tool, argsJson] = process.argv;
const args = argsJson ? JSON.parse(argsJson) : {};

const log = (...a) => console.error(...a);
const emit = (obj) =>
  new Promise((res) => process.stdout.write(JSON.stringify(obj, null, 2) + "\n", res));

/** Ручной вход: открываем настоящее окно и ждём, пока Reddit признает сессию. */
async function login() {
  const ctx = await getContext(false); // headed
  const page = await ctx.newPage();
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
  log("Открыл окно Chrome. Войди в свой аккаунт — жду до 5 минут…");
  log("Вкладку входа можно листать и закрывать: слежу за сессией, а не за ней.");

  const deadline = Date.now() + 5 * 60 * 1000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let name = null;
  let lastError = null;

  while (Date.now() < deadline) {
    await sleep(3000);
    // Опрос не должен зависеть от вкладки входа: её закрывают, по ней уходят
    // на страницы Reddit, и любое такое действие роняло прежний цикл.
    try {
      const me = await apiGet("/api/me.json");
      if (me?.data?.name) {
        name = me.data.name;
        break;
      }
      lastError = me?._error || null;
    } catch (e) {
      const msg = String(e?.message || e);
      if (/has been closed|Target closed|browser has disconnected/i.test(msg)) {
        return { ok: false, error: "окно Chrome закрыли до завершения входа" };
      }
      lastError = msg.split("\n")[0];
    }
  }

  if (!name) {
    await closeBrowser();
    return {
      ok: false,
      error: "вход не завершён за 5 минут" + (lastError ? ` (${lastError})` : ""),
    };
  }
  await saveState();
  await closeBrowser();
  return { ok: true, user: name, note: "сессия сохранена в ~/.reddit-session" };
}

async function whoami() {
  if (!hasSession()) return { ok: false, error: "not_logged_in" };
  const me = await apiGet("/api/me.json");
  if (me?._error) return { ok: false, error: me._error };
  const d = me.data || {};
  return {
    ok: true,
    name: d.name,
    link_karma: d.link_karma,
    comment_karma: d.comment_karma,
    total_karma: d.total_karma,
    created_utc: d.created_utc,
    is_suspended: d.is_suspended || false,
    has_modhash: Boolean(d.modhash),
  };
}

/** Открыть страницу глазами (для ручной проверки/капчи). */
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
 * JSON в stdin, ответы уходят строками в stdout. Без него проверка трёх
 * десятков сабреддитов = сотня запусков Chrome.
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
        if (req.tool === "get") result = await apiGet(req.args?.path, req.args?.params || {});
        else if (req.tool === "post") result = await apiPost(req.args?.path, req.args?.data || {});
        else if (req.tool === "whoami") result = await whoami();
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
    // Чтение Reddit отдаёт и без входа — сессия нужна только для действий
    case "get":
      result = await apiGet(args.path, args.params || {});
      break;
    case "post":
      if (!hasSession()) { result = { _error: "not_logged_in" }; break; }
      result = await apiPost(args.path, args.data || {});
      break;
    case "open":
      result = await open(args.url);
      break;
    default:
      result = {
        error: "неизвестная команда",
        commands: ["login", "whoami", "get", "post", "open"],
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
