import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
let prev = 0, n = 0;
for (let i = 0; i < 25; i++) {
  n = await page.evaluate(() => document.querySelectorAll('[data-qa="negotiations-item"]').length);
  if (n === prev && i > 1) break;
  prev = n;
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(1200);
}
console.log(JSON.stringify({ totalAfterScroll: n }));
process.exit(0);
