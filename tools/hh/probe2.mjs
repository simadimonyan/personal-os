import { getPage } from "./browser.js";
const id = process.argv[2];
const page = await getPage();
await page.goto(`https://hh.ru/vacancy/${id}`, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(2500);
const info = await page.evaluate(() => {
  const btn = document.querySelector('[data-qa="vacancy-response-link-top"]');
  return {
    url: location.href,
    title: document.querySelector('[data-qa="vacancy-title"]')?.textContent?.trim(),
    btnText: btn?.textContent?.trim(), btnHref: btn?.getAttribute('href'),
    btnDisabled: btn?.hasAttribute('disabled'),
    responded: !!document.querySelector('[data-qa*="responded"], .vacancy-response-link_responded'),
    bodyHas: ['Вы откликнулись','Отклик отправлен','заполните','вопрос','тест']
      .filter(w => document.body.innerText.includes(w)),
  };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
