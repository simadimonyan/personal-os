// Открыть вакансию, нажать «Откликнуться» и снять разметку модалки с опросом.
import { getPage } from "./browser.js";
const id = process.argv[2];
const page = await getPage();
await page.goto(`https://hh.ru/vacancy/${id}`, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(2000);
const btn = page.locator('[data-qa="vacancy-response-link-top"]').first();
if (await btn.isVisible().catch(() => false)) { await btn.click(); await page.waitForTimeout(4000); }
const info = await page.evaluate(() => {
  const modal = document.querySelector('div[class*="magritte-modal"]');
  if (!modal) return { modal: false, url: location.href };
  const q = [...modal.querySelectorAll('[data-qa]')].map(e => e.getAttribute('data-qa'));
  return {
    modal: true,
    text: modal.innerText.slice(0, 1500),
    dataQa: [...new Set(q)].slice(0, 60),
    inputs: [...modal.querySelectorAll('input,textarea,select')].map(e => ({
      tag: e.tagName, type: e.type, name: e.name, qa: e.getAttribute('data-qa'),
      required: e.required, value: (e.value || '').slice(0, 40),
    })),
  };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
