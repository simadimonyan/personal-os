-- 004_tasks.sql — локальный таск-менеджер с 3-сторонней синхронизацией.
-- Бот (эта таблица) — координатор/хаб между Obsidian (Задачи.md) и Todoist API.
-- uid — короткий стабильный id, используется как ^uid в Obsidian и для маппинга.
-- dirty_* флаги — что ещё не дотолкнули во внешнюю систему (надёжность как в outbox).

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY,
    uid           TEXT NOT NULL UNIQUE,           -- стабильный короткий id (^uid в Obsidian)
    content       TEXT NOT NULL,                  -- текст задачи
    done          INTEGER NOT NULL DEFAULT 0,     -- 0=активна, 1=выполнена
    todoist_id    TEXT,                           -- id задачи в Todoist (NULL пока не синхронизирована)
    source        TEXT NOT NULL DEFAULT 'bot',    -- откуда создана: bot|obsidian|todoist
    created_at    TEXT NOT NULL,                  -- ISO8601
    updated_at    TEXT NOT NULL,                  -- ISO8601 — для last-write-wins
    completed_at  TEXT,                           -- когда отметили done
    deleted       INTEGER NOT NULL DEFAULT 0,     -- soft-delete
    dirty_obsidian INTEGER NOT NULL DEFAULT 1,    -- нужно перерисовать в Obsidian
    dirty_todoist  INTEGER NOT NULL DEFAULT 1     -- нужно дотолкнуть в Todoist
);
CREATE INDEX IF NOT EXISTS idx_tasks_uid ON tasks(uid);
CREATE INDEX IF NOT EXISTS idx_tasks_todoist ON tasks(todoist_id);
CREATE INDEX IF NOT EXISTS idx_tasks_active ON tasks(deleted, done);
