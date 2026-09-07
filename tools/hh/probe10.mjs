import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => {
  const hits = [...document.querySelectorAll('button,a,[role="tab"],[role="button"],label,div')]
    .filter(e => /^Все\s*\d+$/.test((e.textContent || '').replace(/\s+/g,' ').trim()))
    .slice(0, 4)
    .map(e => ({ tag: e.tagName, qa: e.getAttribute('data-qa'), role: e.getAttribute('role'),
                 cls: (e.className||'').toString().slice(0,80),
                 clickable: typeof e.onclick === 'function' || e.tagName === 'BUTTON' || e.tagName === 'A' }));
  return { hits, count: hits.length };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
