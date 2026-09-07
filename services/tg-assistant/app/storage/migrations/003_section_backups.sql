-- 003_section_backups.sql — бэкап последней секции дневного файла для «↩️ Отменить правку».
-- edit_last затирает последнюю секцию (## ...) read-modify-write. Перед затиранием
-- кладём сюда прежний текст секции, чтобы откатить одной кнопкой.

CREATE TABLE IF NOT EXISTS section_backups (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL,          -- YYYY-MM-DD дневного файла
    section_text TEXT NOT NULL,          -- прежняя последняя секция целиком (## заголовок + тело)
    created_at   TEXT NOT NULL,          -- ISO8601
    used         INTEGER NOT NULL DEFAULT 0  -- 1 = уже откатили, не предлагать повторно
);
CREATE INDEX IF NOT EXISTS idx_section_backups_date ON section_backups(date, used);
