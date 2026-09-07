import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => ({
  tabs: [...document.querySelectorAll('a,button,[role="tab"]')]
    .map(e => ({ t: e.textContent.trim(), href: e.getAttribute('href') }))
    .filter(x => /архив|все|актив|скрыт|отклик/i.test(x.t) && x.t.length < 40).slice(0, 12),
}));
console.log(JSON.stringify(info, null, 1));
process.exit(0);
