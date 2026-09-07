import { getPage } from "./browser.js";
const page = await getPage();
await page.goto("https://hh.ru/applicant/resumes", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForSelector('a[href*="/resume/"]', { timeout: 15000 }).catch(()=>{});
await page.waitForTimeout(2000);
const info = await page.evaluate(() => {
  const links = [...document.querySelectorAll('a[href*="/resume/"]')];
  return links.slice(0, 8).map(l => ({
    href: (l.getAttribute('href')||'').slice(0, 70),
    lines: (l.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean).slice(0, 6),
  }));
});
console.log(JSON.stringify(info, null, 1));
process.exit(0);
