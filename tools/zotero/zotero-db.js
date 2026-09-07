import Database from "better-sqlite3";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const LINK_MODES = {
    0: "imported_file",
    1: "imported_url",
    2: "linked_file",
    3: "linked_url",
    4: "embedded_image",
};
export class ZoteroDB {
    cfg;
    db = null;
    copyPath;
    copyMtime = 0;
    lastCheck = 0;
    constructor(cfg) {
        this.cfg = cfg;
        this.copyPath = path.join(os.tmpdir(), `zotero-mcp-${process.pid}-${Date.now()}.sqlite`);
        process.on("exit", () => this.cleanup());
        process.on("SIGINT", () => {
            this.cleanup();
            process.exit(0);
        });
        process.on("SIGTERM", () => {
            this.cleanup();
            process.exit(0);
        });
    }
    cleanup() {
        try {
            this.db?.close();
        }
        catch {
            /* ignore */
        }
        try {
            if (fs.existsSync(this.copyPath))
                fs.unlinkSync(this.copyPath);
        }
        catch {
            /* ignore */
        }
    }
    /**
     * Ensure the in-memory connection points at a fresh copy of the live DB.
     * Cheap when invoked within TTL; we re-check source mtime only after TTL
     * to avoid stat'ing on every query.
     */
    connect() {
        const now = Date.now();
        const needsCheck = now - this.lastCheck > this.cfg.dbCopyTtlMs;
        if (this.db && !needsCheck)
            return this.db;
        const srcStat = fs.statSync(this.cfg.dbPath);
        if (this.db && srcStat.mtimeMs === this.copyMtime) {
            this.lastCheck = now;
            return this.db;
        }
        // Close prior connection if any.
        try {
            this.db?.close();
        }
        catch {
            /* ignore */
        }
        // Copy DB. Also copy the -wal sidecar if present so we see the latest
        // writes that Zotero hasn't checkpointed yet.
        fs.copyFileSync(this.cfg.dbPath, this.copyPath);
        const wal = this.cfg.dbPath + "-wal";
        const walCopy = this.copyPath + "-wal";
        if (fs.existsSync(wal)) {
            fs.copyFileSync(wal, walCopy);
        }
        else if (fs.existsSync(walCopy)) {
            fs.unlinkSync(walCopy);
        }
        this.db = new Database(this.copyPath, { readonly: true, fileMustExist: true });
        this.db.pragma("query_only = ON");
        this.copyMtime = srcStat.mtimeMs;
        this.lastCheck = now;
        return this.db;
    }
    // ---------------------------------------------------------------- helpers
    /**
     * Aggregate creator names (last, first) for the given item IDs. Returns a
     * map keyed by itemID so callers can stitch results back into rows.
     */
    getCreatorsForItems(itemIds) {
        const map = new Map();
        if (itemIds.length === 0)
            return map;
        const db = this.connect();
        const placeholders = itemIds.map(() => "?").join(",");
        const rows = db
            .prepare(`SELECT ic.itemID AS itemId,
                c.lastName AS lastName,
                c.firstName AS firstName
         FROM itemCreators ic
         JOIN creators c ON c.creatorID = ic.creatorID
         WHERE ic.itemID IN (${placeholders})
         ORDER BY ic.itemID, ic.orderIndex`)
            .all(...itemIds);
        for (const r of rows) {
            const parts = [r.lastName, r.firstName].filter(Boolean);
            const name = parts.join(", ");
            if (!map.has(r.itemId))
                map.set(r.itemId, []);
            map.get(r.itemId).push(name);
        }
        return map;
    }
    getFieldsForItems(itemIds, fieldNames) {
        const map = new Map();
        if (itemIds.length === 0)
            return map;
        const db = this.connect();
        const placeholders = itemIds.map(() => "?").join(",");
        let sql = `
      SELECT id.itemID  AS itemId,
             f.fieldName AS fieldName,
             idv.value   AS value
      FROM itemData id
      JOIN fieldsCombined f ON f.fieldID = id.fieldID
      JOIN itemDataValues idv ON idv.valueID = id.valueID
      WHERE id.itemID IN (${placeholders})`;
        const params = [...itemIds];
        if (fieldNames && fieldNames.length > 0) {
            sql += ` AND f.fieldName IN (${fieldNames.map(() => "?").join(",")})`;
            params.push(...fieldNames);
        }
        const rows = db.prepare(sql).all(...params);
        for (const r of rows) {
            if (!map.has(r.itemId))
                map.set(r.itemId, {});
            map.get(r.itemId)[r.fieldName] = r.value;
        }
        return map;
    }
    getTagsForItems(itemIds) {
        const map = new Map();
        if (itemIds.length === 0)
            return map;
        const db = this.connect();
        const placeholders = itemIds.map(() => "?").join(",");
        const rows = db
            .prepare(`SELECT it.itemID AS itemId, t.name AS name
         FROM itemTags it
         JOIN tags t ON t.tagID = it.tagID
         WHERE it.itemID IN (${placeholders})
         ORDER BY t.name`)
            .all(...itemIds);
        for (const r of rows) {
            if (!map.has(r.itemId))
                map.set(r.itemId, []);
            map.get(r.itemId).push(r.name);
        }
        return map;
    }
    getCollectionsForItems(itemIds) {
        const map = new Map();
        if (itemIds.length === 0)
            return map;
        const db = this.connect();
        const placeholders = itemIds.map(() => "?").join(",");
        const rows = db
            .prepare(`SELECT ci.itemID AS itemId, c.collectionName AS name
         FROM collectionItems ci
         JOIN collections c ON c.collectionID = ci.collectionID
         WHERE ci.itemID IN (${placeholders})
         ORDER BY c.collectionName`)
            .all(...itemIds);
        for (const r of rows) {
            if (!map.has(r.itemId))
                map.set(r.itemId, []);
            map.get(r.itemId).push(r.name);
        }
        return map;
    }
    /** Build summaries from a list of itemIDs preserving input order. */
    buildSummaries(itemIds) {
        if (itemIds.length === 0)
            return [];
        const db = this.connect();
        const placeholders = itemIds.map(() => "?").join(",");
        const baseRows = db
            .prepare(`SELECT i.itemID AS itemId,
                i.key    AS key,
                it.typeName AS itemType,
                i.dateAdded AS dateAdded,
                i.dateModified AS dateModified
         FROM items i
         JOIN itemTypesCombined it ON it.itemTypeID = i.itemTypeID
         WHERE i.itemID IN (${placeholders})`)
            .all(...itemIds);
        const fields = this.getFieldsForItems(itemIds, [
            "title",
            "date",
            "publicationTitle",
            "bookTitle",
            "websiteTitle",
            "proceedingsTitle",
        ]);
        const creators = this.getCreatorsForItems(itemIds);
        const byId = new Map(baseRows.map((r) => [r.itemId, r]));
        const summaries = [];
        for (const id of itemIds) {
            const row = byId.get(id);
            if (!row)
                continue;
            const f = fields.get(id) ?? {};
            const publication = f.publicationTitle ?? f.bookTitle ?? f.websiteTitle ?? f.proceedingsTitle ?? null;
            summaries.push({
                itemId: row.itemId,
                key: row.key,
                itemType: row.itemType,
                title: f.title ?? null,
                creators: creators.get(id) ?? [],
                date: f.date ?? null,
                publication,
                dateAdded: row.dateAdded,
                dateModified: row.dateModified,
            });
        }
        return summaries;
    }
    // ---------------------------------------------------------------- public
    /**
     * Full-text-ish search. Matches the query against title, abstract,
     * creator names, publication, and tags using LIKE %query%.
     * Filters: creator name substring, tag, collection name, item type.
     */
    search(opts) {
        const db = this.connect();
        const limit = Math.min(Math.max(opts.limit ?? 25, 1), 200);
        const q = opts.query?.trim();
        const conds = ["i.itemTypeID != 14"]; // exclude attachments at top level
        const params = [];
        if (!opts.includeTrashed) {
            conds.push("i.itemID NOT IN (SELECT itemID FROM deletedItems)");
        }
        if (q) {
            const like = `%${q}%`;
            conds.push(`(
        EXISTS (
          SELECT 1 FROM itemData id
            JOIN fieldsCombined f ON f.fieldID = id.fieldID
            JOIN itemDataValues idv ON idv.valueID = id.valueID
          WHERE id.itemID = i.itemID
            AND f.fieldName IN ('title','abstractNote','publicationTitle','bookTitle','websiteTitle','shortTitle','series','seriesTitle')
            AND idv.value LIKE ?
        )
        OR EXISTS (
          SELECT 1 FROM itemCreators ic
            JOIN creators c ON c.creatorID = ic.creatorID
          WHERE ic.itemID = i.itemID
            AND (c.lastName LIKE ? OR c.firstName LIKE ?)
        )
        OR EXISTS (
          SELECT 1 FROM itemTags it JOIN tags t ON t.tagID = it.tagID
          WHERE it.itemID = i.itemID AND t.name LIKE ?
        )
      )`);
            params.push(like, like, like, like);
        }
        if (opts.creator) {
            conds.push(`EXISTS (
        SELECT 1 FROM itemCreators ic
          JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID = i.itemID
          AND (c.lastName LIKE ? OR c.firstName LIKE ?)
      )`);
            const like = `%${opts.creator}%`;
            params.push(like, like);
        }
        if (opts.tag) {
            conds.push(`EXISTS (
        SELECT 1 FROM itemTags it JOIN tags t ON t.tagID = it.tagID
        WHERE it.itemID = i.itemID AND t.name = ?
      )`);
            params.push(opts.tag);
        }
        if (opts.collection) {
            conds.push(`EXISTS (
        SELECT 1 FROM collectionItems ci
          JOIN collections c ON c.collectionID = ci.collectionID
        WHERE ci.itemID = i.itemID AND c.collectionName = ?
      )`);
            params.push(opts.collection);
        }
        if (opts.itemType) {
            conds.push(`i.itemTypeID = (SELECT itemTypeID FROM itemTypesCombined WHERE typeName = ?)`);
            params.push(opts.itemType);
        }
        const sql = `
      SELECT i.itemID AS itemId
      FROM items i
      WHERE ${conds.join(" AND ")}
      ORDER BY i.dateModified DESC
      LIMIT ?`;
        params.push(limit);
        const rows = db.prepare(sql).all(...params);
        return this.buildSummaries(rows.map((r) => r.itemId));
    }
    /** Resolve an item by either its numeric itemID or its 8-character item key. */
    resolveItemId(idOrKey) {
        const db = this.connect();
        if (typeof idOrKey === "number" || /^\d+$/.test(idOrKey)) {
            const row = db
                .prepare("SELECT itemID FROM items WHERE itemID = ?")
                .get(Number(idOrKey));
            return row?.itemID ?? null;
        }
        const row = db
            .prepare("SELECT itemID FROM items WHERE key = ?")
            .get(idOrKey);
        return row?.itemID ?? null;
    }
    getItem(idOrKey) {
        const itemId = this.resolveItemId(idOrKey);
        if (itemId === null)
            return null;
        const [summary] = this.buildSummaries([itemId]);
        if (!summary)
            return null;
        const fields = this.getFieldsForItems([itemId]).get(itemId) ?? {};
        const tags = this.getTagsForItems([itemId]).get(itemId) ?? [];
        const collections = this.getCollectionsForItems([itemId]).get(itemId) ?? [];
        const db = this.connect();
        const noteRows = db
            .prepare(`SELECT n.note AS note FROM itemNotes n
         WHERE n.parentItemID = ?`)
            .all(itemId);
        return {
            ...summary,
            fields,
            tags,
            collections,
            abstract: fields.abstractNote ?? null,
            notes: noteRows.map((r) => r.note),
            attachments: this.getAttachments(itemId),
        };
    }
    /**
     * Return attachment children for a top-level item.
     * Resolves linkMode and the on-disk path under {storage}/{key}/.
     */
    getAttachments(idOrKey) {
        const itemId = this.resolveItemId(idOrKey);
        if (itemId === null)
            return [];
        const db = this.connect();
        const rows = db
            .prepare(`SELECT i.itemID AS itemId,
                i.key    AS key,
                ia.contentType AS contentType,
                ia.path  AS pathField,
                ia.linkMode AS linkMode
         FROM itemAttachments ia
         JOIN items i ON i.itemID = ia.itemID
         WHERE ia.parentItemID = ?
           AND i.itemID NOT IN (SELECT itemID FROM deletedItems)`)
            .all(itemId);
        const titleMap = this.getFieldsForItems(rows.map((r) => r.itemId), ["title", "url"]);
        return rows.map((r) => {
            const link = r.linkMode != null ? LINK_MODES[r.linkMode] ?? String(r.linkMode) : null;
            const fields = titleMap.get(r.itemId) ?? {};
            let filename = null;
            let filePath = null;
            if (r.pathField) {
                if (r.pathField.startsWith("storage:")) {
                    filename = r.pathField.slice("storage:".length);
                    filePath = path.join(this.cfg.storageDir, r.key, filename);
                }
                else {
                    filename = path.basename(r.pathField);
                    filePath = r.pathField;
                }
            }
            return {
                itemId: r.itemId,
                key: r.key,
                title: fields.title ?? null,
                contentType: r.contentType,
                filename,
                filePath,
                url: fields.url ?? null,
                linkMode: link,
            };
        });
    }
    listCollections() {
        const db = this.connect();
        const rows = db
            .prepare(`SELECT c.collectionID AS collectionId,
                c.key           AS key,
                c.collectionName AS name,
                p.key           AS parentKey,
                (SELECT COUNT(*) FROM collectionItems ci WHERE ci.collectionID = c.collectionID) AS itemCount
         FROM collections c
         LEFT JOIN collections p ON p.collectionID = c.parentCollectionID
         ORDER BY c.collectionName`)
            .all();
        return rows;
    }
    listTags(limit = 200) {
        const db = this.connect();
        const rows = db
            .prepare(`SELECT t.tagID AS tagId,
                t.name  AS name,
                COUNT(it.itemID) AS itemCount
         FROM tags t
         LEFT JOIN itemTags it ON it.tagID = t.tagID
         GROUP BY t.tagID
         ORDER BY itemCount DESC, t.name
         LIMIT ?`)
            .all(limit);
        return rows;
    }
    /** Most recently modified items (excluding attachments and trashed items). */
    recent(limit = 25) {
        const db = this.connect();
        const rows = db
            .prepare(`SELECT i.itemID AS itemId
         FROM items i
         WHERE i.itemTypeID != 14
           AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
           AND i.itemID NOT IN (SELECT itemID FROM itemAttachments)
           AND i.itemID NOT IN (SELECT itemID FROM itemNotes)
         ORDER BY i.dateAdded DESC
         LIMIT ?`)
            .all(Math.min(Math.max(limit, 1), 200));
        return this.buildSummaries(rows.map((r) => r.itemId));
    }
    /** Count of items per type — useful for orientation when inspecting a library. */
    stats() {
        const db = this.connect();
        const total = db
            .prepare(`SELECT COUNT(*) AS c FROM items i
         WHERE i.itemTypeID != 14
           AND i.itemID NOT IN (SELECT itemID FROM deletedItems)`)
            .get();
        const byType = db
            .prepare(`SELECT it.typeName AS itemType, COUNT(*) AS count
         FROM items i
         JOIN itemTypesCombined it ON it.itemTypeID = i.itemTypeID
         WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
         GROUP BY it.typeName
         ORDER BY count DESC`)
            .all();
        return { totalItems: total.c, byType };
    }
}
//# sourceMappingURL=zotero-db.js.map