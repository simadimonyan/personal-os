import { chromium } from "playwright";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";
const STATE_DIR = join(homedir(), ".hh-mcp");
const STATE_FILE = join(STATE_DIR, "auth-state.json");
let browser = null;
let context = null;
export async function getBrowser() {
    if (!browser || !browser.isConnected()) {
        browser = await chromium.launch({
            headless: false,
            args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        });
    }
    return browser;
}
export async function getContext() {
    if (!context) {
        const b = await getBrowser();
        if (!existsSync(STATE_DIR)) {
            mkdirSync(STATE_DIR, { recursive: true });
        }
        if (existsSync(STATE_FILE)) {
            context = await b.newContext({
                storageState: STATE_FILE,
                userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale: "ru-RU",
                timezoneId: "Europe/Moscow",
            });
        }
        else {
            context = await b.newContext({
                userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale: "ru-RU",
                timezoneId: "Europe/Moscow",
            });
        }
    }
    return context;
}
export async function getPage() {
    const ctx = await getContext();
    const pages = ctx.pages();
    if (pages.length > 0) {
        return pages[0];
    }
    return ctx.newPage();
}
export async function saveAuthState() {
    if (!context)
        return;
    if (!existsSync(STATE_DIR)) {
        mkdirSync(STATE_DIR, { recursive: true });
    }
    await context.storageState({ path: STATE_FILE });
}
export async function closeBrowser() {
    if (context) {
        await context.close();
        context = null;
    }
    if (browser) {
        await browser.close();
        browser = null;
    }
}
export function getStateFile() {
    return STATE_FILE;
}
