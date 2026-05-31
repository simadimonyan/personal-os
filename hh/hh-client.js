import { getPage, saveAuthState } from "./browser.js";
const BASE_URL = "https://hh.ru";
export async function checkAuth() {
    const page = await getPage();
    await page.goto(`${BASE_URL}/applicant/resumes`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    const url = page.url();
    return !url.includes("/account/login") && !url.includes("/login");
}
export async function login(login, password) {
    const page = await getPage();
    await page.goto(`${BASE_URL}/account/login?backurl=%2F`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    await page.waitForTimeout(2000);
    // Try multiple selectors for login field (hh.ru changes layout periodically)
    const loginInput = page.locator([
        'input[name="login"]',
        'input[data-qa="login-input-username"]',
        'input[autocomplete="username"]',
        'input[type="tel"]',
        'input[type="email"]',
        'input[type="text"]',
    ].join(", ")).first();
    await loginInput.waitFor({ timeout: 30000 });
    await loginInput.fill(login);
    const nextBtn = page.locator('button[data-qa="account-login-submit"], button[type="submit"]').first();
    await nextBtn.click();
    await page.waitForTimeout(2000);
    const passwordInput = page.locator([
        'input[name="password"]',
        'input[data-qa="login-input-password"]',
        'input[type="password"]',
    ].join(", ")).first();
    await passwordInput.waitFor({ timeout: 30000 });
    await passwordInput.fill(password);
    const submitBtn = page.locator('button[data-qa="account-login-submit"], button[type="submit"]').first();
    await submitBtn.click();
    await page.waitForTimeout(3000);
    const currentUrl = page.url();
    if (currentUrl.includes("/account/login") || currentUrl.includes("/login")) {
        const errorEl = await page.locator('[data-qa="login-error-hint"], .bloko-form-error').first().textContent().catch(() => null);
        if (errorEl) {
            return `Ошибка входа: ${errorEl}`;
        }
        // Possibly captcha or 2FA needed
        return "Требуется дополнительная проверка. Браузер открыт — пройдите верификацию вручную, затем вызовите save_session.";
    }
    await saveAuthState();
    return "Успешный вход в систему. Сессия сохранена.";
}
export async function saveSession() {
    await saveAuthState();
    return "Сессия сохранена успешно.";
}
export async function searchVacancies(params) {
    const page = await getPage();
    const searchUrl = new URL(`${BASE_URL}/search/vacancy`);
    searchUrl.searchParams.set("text", params.query);
    if (params.area)
        searchUrl.searchParams.set("area", params.area);
    if (params.salary)
        searchUrl.searchParams.set("salary", params.salary.toString());
    if (params.experience)
        searchUrl.searchParams.set("experience", params.experience);
    if (params.employment)
        searchUrl.searchParams.set("employment", params.employment);
    if (params.schedule)
        searchUrl.searchParams.set("schedule", params.schedule);
    if (params.page)
        searchUrl.searchParams.set("page", (params.page - 1).toString());
    if (params.perPage)
        searchUrl.searchParams.set("items_on_page", params.perPage.toString());
    searchUrl.searchParams.set("ored_clusters", "true");
    await page.goto(searchUrl.toString(), { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(2000);
    const vacancies = await page.evaluate(() => {
        const items = document.querySelectorAll('[data-qa="vacancy-serp__vacancy"]');
        const results = [];
        items.forEach((item) => {
            const titleEl = item.querySelector('[data-qa="serp-item__title"]');
            const companyEl = item.querySelector('[data-qa="vacancy-serp__vacancy-employer"]');
            const salaryEl = item.querySelector('[data-qa="vacancy-serp__vacancy-compensation"]');
            const locationEl = item.querySelector('[data-qa="vacancy-serp__vacancy-address"]');
            const linkEl = titleEl;
            const href = linkEl?.href || "";
            const idMatch = href.match(/\/vacancy\/(\d+)/);
            if (idMatch) {
                results.push({
                    id: idMatch[1],
                    title: titleEl?.textContent?.trim() || "",
                    company: companyEl?.textContent?.trim() || "",
                    salary: salaryEl?.textContent?.trim() || undefined,
                    location: locationEl?.textContent?.trim() || "",
                    url: href,
                    hasResponse: !!item.querySelector('[data-qa="vacancy-serp__vacancy_responded"]'),
                });
            }
        });
        return results;
    });
    return vacancies;
}
export async function getVacancyDetails(vacancyId) {
    const page = await getPage();
    await page.goto(`${BASE_URL}/vacancy/${vacancyId}`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    await page.waitForTimeout(1500);
    const details = await page.evaluate(() => {
        const titleEl = document.querySelector('[data-qa="vacancy-title"]');
        const companyEl = document.querySelector('[data-qa="vacancy-company-name"]');
        const salaryEl = document.querySelector('[data-qa="vacancy-salary-compensation-type-net"], [data-qa="vacancy-salary"]');
        const locationEl = document.querySelector('[data-qa="vacancy-view-location"]');
        const descriptionEl = document.querySelector('[data-qa="vacancy-description"]');
        const experienceEl = document.querySelector('[data-qa="vacancy-experience"] span');
        const employmentEl = document.querySelector('[data-qa="vacancy-work-schedule-list"] li');
        const href = window.location.href;
        const idMatch = href.match(/\/vacancy\/(\d+)/);
        return {
            id: idMatch?.[1] || "",
            title: titleEl?.textContent?.trim() || "",
            company: companyEl?.textContent?.trim() || "",
            salary: salaryEl?.textContent?.trim() || undefined,
            location: locationEl?.textContent?.trim() || "",
            url: href,
            description: descriptionEl?.innerText?.trim() || "",
            experience: experienceEl?.textContent?.trim() || "",
            employment: employmentEl?.textContent?.trim() || "",
        };
    });
    return details.id ? details : null;
}
export async function getResumes() {
    const page = await getPage();
    await page.goto(`${BASE_URL}/applicant/resumes`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    await page.waitForTimeout(1500);
    if (page.url().includes("/account/login") || page.url().includes("/login")) {
        throw new Error("Не авторизованы. Вызовите инструмент login.");
    }
    const resumes = await page.evaluate(() => {
        const results = [];
        // Try new selector first, then fall back to old ones
        const selectors = [
            '[data-qa="resume-block"]',
            '[class*="resumeCard"]',
            '[class*="resume-card"]',
            '[class*="ResumeCard"]',
        ];
        let items = null;
        for (const sel of selectors) {
            const found = document.querySelectorAll(sel);
            if (found.length > 0) {
                items = found;
                break;
            }
        }
        // Fallback: find all resume links on the page
        if (!items || items.length === 0) {
            const links = document.querySelectorAll('a[href*="/resume/"]');
            links.forEach((link) => {
                const href = link.href || "";
                const idMatch = href.match(/\/resume\/([a-f0-9]+)/i);
                if (idMatch && !results.find((r) => r.id === idMatch[1])) {
                    results.push({
                        id: idMatch[1],
                        title: link.textContent?.trim() || "Резюме",
                        status: "unknown",
                        updatedAt: "",
                        url: href,
                    });
                }
            });
            return results;
        }
        items.forEach((item) => {
            const titleEl = item.querySelector('[data-qa="resume-block-title-desktop"]') ||
                item.querySelector('[data-qa="resume-title"]') ||
                item.querySelector('h2, h3, [class*="title"]');
            const statusEl = item.querySelector('[data-qa="resume-status"]') ||
                item.querySelector('[class*="status"]');
            const linkEl = item.querySelector('a[href*="/resume/"]');
            const updatedEl = item.querySelector('[data-qa="resume-block-updated"]') ||
                item.querySelector('[class*="updated"], time');
            const href = linkEl?.href || "";
            const idMatch = href.match(/\/resume\/([a-f0-9]+)/i);
            if (idMatch) {
                results.push({
                    id: idMatch[1],
                    title: titleEl?.textContent?.trim() || "Резюме",
                    status: statusEl?.textContent?.trim() || "unknown",
                    updatedAt: updatedEl?.textContent?.trim() || "",
                    url: href,
                });
            }
        });
        return results;
    });
    return resumes;
}
export async function getResumeText(resumeId) {
    const page = await getPage();
    await page.goto(`${BASE_URL}/resume/${resumeId}`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    await page.waitForTimeout(1500);
    const text = await page.evaluate(() => {
        const mainEl = document.querySelector(".resume-block-container, [data-qa=\"resume-view\"], main");
        return mainEl?.innerText?.trim() || document.body?.innerText?.trim() || "";
    });
    return text;
}
export async function applyToVacancy(vacancyId, resumeId, coverLetter) {
    const page = await getPage();
    // If modal is already open from a previous call, use it directly
    const submitBtnLocator = page.locator('[data-qa="vacancy-response-submit-popup"]').first();
    const alreadyOpen = await submitBtnLocator.count().then(n => n > 0).catch(() => false);
    if (!alreadyOpen) {
        await page.goto(`${BASE_URL}/vacancy/${vacancyId}`, {
            waitUntil: "domcontentloaded",
            timeout: 30000,
        });
        await page.waitForTimeout(2000);
        if (page.url().includes("/account/login")) {
            throw new Error("Не авторизованы. Вызовите инструмент login.");
        }
        // Check if already applied
        const alreadyApplied = await page.locator('[data-qa="vacancy-response-link-top"][disabled], .vacancy-response-link_responded').first().isVisible().catch(() => false);
        if (alreadyApplied) {
            return "Вы уже откликались на эту вакансию.";
        }
        // Click response button
        const responseBtn = page.locator('[data-qa="vacancy-response-link-top"]').first();
        const responseBtnVisible = await responseBtn.isVisible().catch(() => false);
        if (!responseBtnVisible) {
            return "Кнопка отклика не найдена. Возможно, вакансия закрыта или требует другого действия.";
        }
        await responseBtn.click();
    }
    // Wait for modal overlay to appear (uses Playwright's built-in retry)
    const modalOverlay = page.locator('div[class*="magritte-modal-overlay"]').first();
    let modalFound = false;
    try {
        await modalOverlay.innerHTML({ timeout: 15000 });
        modalFound = true;
    }
    catch {
        modalFound = false;
    }
    if (modalFound) {
        // Fill cover letter via JS if provided
        if (coverLetter) {
            // Click "Добавить сопроводительное" button if present
            await page.evaluate(() => {
                const btn = document.querySelector('[data-qa="add-cover-letter"]');
                if (btn)
                    btn.click();
            });
            await page.waitForTimeout(1000);
            // Fill textarea
            const filled = await page.evaluate((text) => {
                const textarea = document.querySelector('[data-qa="vacancy-response-popup-form-letter-input"], textarea[name="letter"], textarea');
                if (!textarea)
                    return false;
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                nativeInputValueSetter?.call(textarea, text);
                textarea.dispatchEvent(new Event('input', { bubbles: true }));
                textarea.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }, coverLetter);
            if (!filled) {
                // Fallback: Playwright fill
                const letterInput = page.locator('textarea').first();
                await letterInput.fill(coverLetter).catch(() => { });
            }
            await page.waitForTimeout(500);
        }
        // Submit via JS click
        await page.evaluate(() => {
            const btn = document.querySelector('[data-qa="vacancy-response-submit-popup"]');
            if (btn)
                btn.click();
        });
        await page.waitForTimeout(3000);
        // Check for success — modal closes or success indicator appears
        const stillOpen = await page.evaluate(() => !!document.querySelector('[data-qa="vacancy-response-submit-popup"]'));
        if (!stillOpen) {
            return "Отклик успешно отправлен!";
        }
        const error = await page.locator('[data-qa="response-error"], .bloko-form-error').first().textContent().catch(() => null);
        if (error)
            return `Ошибка при отклике: ${error}`;
        return "Отклик отправлен (статус не подтверждён — проверьте браузер).";
    }
    return "Диалог отклика не появился. Проверьте браузер вручную.";
}
export async function getApplications() {
    const page = await getPage();
    await page.goto(`${BASE_URL}/applicant/negotiations`, {
        waitUntil: "domcontentloaded",
        timeout: 30000,
    });
    await page.waitForTimeout(2000);
    if (page.url().includes("/account/login")) {
        throw new Error("Не авторизованы. Вызовите инструмент login.");
    }
    const applications = await page.evaluate(() => {
        const items = document.querySelectorAll('[data-qa="negotiations-list-item"]');
        const results = [];
        items.forEach((item) => {
            const titleEl = item.querySelector('[data-qa="negotiations-list-item-vacancy"]');
            const companyEl = item.querySelector('[data-qa="negotiations-list-item-employer"]');
            const statusEl = item.querySelector('[data-qa="negotiations-list-item-status"]');
            const dateEl = item.querySelector('[data-qa="negotiations-list-item-date"]');
            const linkEl = titleEl;
            results.push({
                title: titleEl?.textContent?.trim() || "",
                company: companyEl?.textContent?.trim() || "",
                status: statusEl?.textContent?.trim() || "",
                date: dateEl?.textContent?.trim() || "",
                url: linkEl?.href || "",
            });
        });
        return results;
    });
    return applications;
}
export async function openPageManually(url) {
    const page = await getPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    return `Открыто: ${url}`;
}
export async function getPageHtml(selector) {
    const page = await getPage();
    if (selector) {
        const el = page.locator(selector).first();
        const html = await el.innerHTML().catch(() => null);
        if (!html)
            return `Элемент не найден: ${selector}`;
        return html;
    }
    return await page.content();
}
