import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(2000);
const info = await page.evaluate(() => {
  const more = [...document.querySelectorAll('button,a')]
    .filter(e => /ещё|еще|показать|дальше|след/i.test(e.textContent || ''))
    .slice(0, 6).map(e => ({ t: e.textContent.trim().slice(0,30), qa: e.getAttribute('data-qa'), href: e.getAttribute('href') }));
  const pagerQa = [...document.querySelectorAll('[data-qa*="pager"], [data-qa*="page"]')]
    .slice(0, 10).map(e => ({ qa: e.getAttribute('data-qa'), t: e.textContent.trim().slice(0,20), href: e.getAttribute('href') }));
  return { items: document.querySelectorAll('[data-qa="negotiations-item"]').length, more, pagerQa };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
