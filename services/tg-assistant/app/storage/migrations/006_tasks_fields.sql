-- 006_tasks_fields.sql — богатые поля задач (как в Todoist): срок, приоритет,
-- проект, секция, метки. Нужны для категорий/фильтров (время/проект/приоритет/
-- метки) и двусторонней синхронизации этих атрибутов бот↔Obsidian↔Todoist.

ALTER TABLE tasks ADD COLUMN priority     INTEGER NOT NULL DEFAULT 1;  -- Todoist 1..4 (4=p1, высший)
ALTER TABLE tasks ADD COLUMN due_date     TEXT;   -- YYYY-MM-DD (срок без времени) либо дата datetime-срока
ALTER TABLE tasks ADD COLUMN due_datetime TEXT;   -- ISO8601 с временем, если задано
ALTER TABLE tasks ADD COLUMN due_string   TEXT;   -- человеческая строка ("завтра 18:00") — как ввёл пользователь
ALTER TABLE tasks ADD COLUMN project_id   TEXT;   -- id проекта Todoist (NULL = Inbox)
ALTER TABLE tasks ADD COLUMN section_id   TEXT;   -- id секции Todoist (опц.)
ALTER TABLE tasks ADD COLUMN labels       TEXT NOT NULL DEFAULT '[]';  -- JSON-массив меток

CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);

-- Кэш проектов Todoist — для имён и фильтра «по проектам» в боте.
-- Обновляется на каждом синке (upsert из list_projects).
CREATE TABLE IF NOT EXISTS todoist_projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    is_inbox   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
