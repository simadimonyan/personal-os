import { getPage } from "./browser.js";
const page = await getPage();
await page.goto(`https://hh.ru/vacancy/${process.argv[2]}`, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(2500);
const info = await page.evaluate(() => {
  const controls = [...document.querySelectorAll('a,button')]
    .filter(e => /откликн|отклик|resume|apply/i.test(e.textContent || ''))
    .slice(0, 10)
    .map(e => ({ tag: e.tagName, text: e.textContent.trim().slice(0, 40),
                 qa: e.getAttribute('data-qa'), href: e.getAttribute('href') }));
  const qa = [...document.querySelectorAll('[data-qa*="response"], [data-qa*="respond"]')]
    .slice(0, 15).map(e => e.getAttribute('data-qa'));
  return { controls, responseQa: [...new Set(qa)] };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
