import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => {
  const old = document.querySelectorAll('[data-qa="negotiations-list-item"]').length;
  const qa = [...document.querySelectorAll('[data-qa]')]
    .map(e => e.getAttribute('data-qa'))
    .filter(v => /negotiation|topic|response|resume/i.test(v));
  const counts = {};
  qa.forEach(v => counts[v] = (counts[v] || 0) + 1);
  return { url: location.href, oldSelector: old,
           candidates: Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0, 20),
           snippet: document.body.innerText.slice(0, 400) };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
