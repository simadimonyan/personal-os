import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/negotiations", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);
const info = await page.evaluate(() => {
  const el = [...document.querySelectorAll('*')]
    .filter(e => e.children.length === 0 && /^Все\s*\d+$/.test((e.textContent||'').trim()))[0];
  if (!el) return { found: false };
  const chain = [];
  let n = el;
  for (let i = 0; i < 5 && n; i++, n = n.parentElement)
    chain.push({ tag: n.tagName, qa: n.getAttribute('data-qa'), role: n.getAttribute('role'),
                 href: n.getAttribute('href'), cls: (n.className||'').toString().slice(0,60) });
  return { found: true, text: el.textContent.trim(), chain };
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
