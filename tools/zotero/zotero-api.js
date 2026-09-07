/**
 * Thin wrapper around the local HTTP API that Zotero exposes on
 * http://127.0.0.1:23119 when it's running with the default setting
 * "Allow other applications on this computer to communicate with Zotero".
 *
 * Two endpoints are useful for writes:
 *
 *  1. POST  /api/users/{userId}/items
 *     Adds raw item JSON. Body must be an array of item objects shaped like
 *     the Zotero schema (itemType, title, creators, ...).
 *
 *  2. POST  /connector/saveItems
 *     The endpoint browser connectors hit. Given a URL Zotero will run its
 *     web translator and import the resulting items into the library.
 *     This is the "save from URL" flow.
 */
export class ZoteroApi {
    cfg;
    constructor(cfg) {
        this.cfg = cfg;
    }
    async ping() {
        try {
            const r = await fetch(`${this.cfg.apiBase}/`, { method: "GET" });
            // Zotero answers 200 with a small status page on /. Anything 2xx/3xx
            // means the server is reachable.
            if (r.status >= 200 && r.status < 500)
                return { ok: true };
            return { ok: false, reason: `HTTP ${r.status}` };
        }
        catch (err) {
            return { ok: false, reason: err.message };
        }
    }
    /**
     * Create one or more items in the user library.
     * `items` must conform to the Zotero item schema. Required fields depend
     * on the itemType; at minimum: { itemType, title }. Returns the response
     * body, which contains successful/failed maps keyed by the index of each
     * submitted item.
     */
    async createItems(items) {
        const url = `${this.cfg.apiBase}/api/users/${this.cfg.userId}/items`;
        const res = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Zotero-API-Version": "3",
            },
            body: JSON.stringify(items),
        });
        const text = await res.text();
        if (!res.ok) {
            throw new Error(`Zotero API ${res.status} ${res.statusText}: ${text || "<empty>"}`);
        }
        try {
            return JSON.parse(text);
        }
        catch {
            return { raw: text };
        }
    }
    /**
     * Ask Zotero to fetch the given URL through its web-translator stack and
     * save the resulting item(s) into the library — same path that the
     * Zotero Connector browser extension uses.
     */
    async saveFromUrl(opts) {
        const endpoint = `${this.cfg.apiBase}/connector/saveItems`;
        // Use a stable but per-call session id so subsequent /connector/saveSnapshot
        // calls (if any) can be correlated by the Zotero side.
        const sessionID = opts.sessionID ?? crypto.randomUUID();
        const body = {
            url: opts.url,
            sessionID,
            uri: opts.url,
            // The connector usually sends an HTML snapshot of the page along with the
            // URL. Without it Zotero falls back to fetching the URL itself, which is
            // exactly what we want here.
            items: [],
        };
        if (opts.title)
            body.title = opts.title;
        const res = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                // Zotero's connector endpoint validates this header — it must look
                // like the connector is talking to it.
                "X-Zotero-Connector-API-Version": "3",
            },
            body: JSON.stringify(body),
        });
        const text = await res.text();
        if (!res.ok) {
            throw new Error(`Zotero connector ${res.status} ${res.statusText}: ${text || "<empty>"}`);
        }
        try {
            return JSON.parse(text);
        }
        catch {
            return { raw: text };
        }
    }
    /**
     * Fetch the JSON template for a given item type — useful when callers
     * want to know which fields are valid before submitting createItems.
     */
    async itemTemplate(itemType) {
        const url = `${this.cfg.apiBase}/api/items/new?itemType=${encodeURIComponent(itemType)}`;
        const res = await fetch(url, {
            headers: { "Zotero-API-Version": "3" },
        });
        if (!res.ok) {
            throw new Error(`Zotero API ${res.status} ${res.statusText}`);
        }
        return res.json();
    }
}
//# sourceMappingURL=zotero-api.js.map