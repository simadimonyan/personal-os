import os from "node:os";
import path from "node:path";
import fs from "node:fs";
function defaultDataDir() {
    const home = os.homedir();
    switch (process.platform) {
        case "darwin":
            return path.join(home, "Zotero");
        case "win32":
            return path.join(home, "Zotero");
        default:
            return path.join(home, "Zotero");
    }
}
export function loadConfig() {
    const dataDir = process.env.ZOTERO_DATA_DIR
        ? path.resolve(process.env.ZOTERO_DATA_DIR)
        : defaultDataDir();
    const dbPath = path.join(dataDir, "zotero.sqlite");
    const storageDir = path.join(dataDir, "storage");
    if (!fs.existsSync(dbPath)) {
        throw new Error(`zotero.sqlite not found at ${dbPath}. ` +
            `Set ZOTERO_DATA_DIR to your Zotero data folder ` +
            `(the one that contains zotero.sqlite).`);
    }
    const apiBase = (process.env.ZOTERO_API_BASE ?? "http://127.0.0.1:23119").replace(/\/$/, "");
    const userId = process.env.ZOTERO_USER_ID ?? "0";
    const dbCopyTtlMs = process.env.ZOTERO_DB_TTL_MS
        ? Number(process.env.ZOTERO_DB_TTL_MS)
        : 5000;
    return { dataDir, dbPath, storageDir, apiBase, userId, dbCopyTtlMs };
}
//# sourceMappingURL=config.js.map