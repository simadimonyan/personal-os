import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => {
  const items = [...document.querySelectorAll('[data-qa="negotiations-item"]')];
  return {
    count: items.length,
    sample: items.slice(0, 3).map(it => ({
      innerQa: [...new Set([...it.querySelectorAll('[data-qa]')].map(e => e.getAttribute('data-qa')))],
      vacancy: it.querySelector('[data-qa="negotiations-item-vacancy"]')?.textContent?.trim(),
      href: it.querySelector('[data-qa="negotiations-item-vacancy"]')?.getAttribute('href'),
      company: it.querySelector('[data-qa="negotiations-item-company"]')?.textContent?.trim(),
      date: it.querySelector('[data-qa="negotiations-item-date"]')?.textContent?.trim(),
      text: it.innerText.replace(/\n+/g, ' | ').slice(0, 220),
    })),
  };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
