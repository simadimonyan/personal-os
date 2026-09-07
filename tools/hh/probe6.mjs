import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => {
  const items = document.querySelectorAll('[data-qa="negotiations-item"]').length;
  const pager = [...document.querySelectorAll('[data-qa*="pager"], a[href*="page="]')]
    .map(e => e.textContent.trim()).filter(Boolean).slice(0, 12);
  const counter = [...document.querySelectorAll('h1, [data-qa*="title"], [data-qa*="count"]')]
    .map(e => e.textContent.trim()).filter(t => /\d/.test(t)).slice(0, 6);
  return { itemsOnPage: items, pager, counter, bodyStart: document.body.innerText.slice(0, 200) };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
