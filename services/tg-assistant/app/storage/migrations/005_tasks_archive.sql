-- 005_tasks_archive.sql — «очистка» выполненных задач из активного статуса.
-- Архивные задачи убираются из бота и из активного Задачи.md, НО их история
-- остаётся в Obsidian (Архив задач.md) и в самой таблице (запись не удаляется).
-- archived=1 трактуется во всех активных выборках как «вне активного управления»
-- (как deleted), но, в отличие от deleted, это осознанная архивация выполненного.

ALTER TABLE tasks ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;  -- 1 = убрана из активного статуса
ALTER TABLE tasks ADD COLUMN archived_at TEXT;                     -- ISO8601, когда заархивировали

CREATE INDEX IF NOT EXISTS idx_tasks_archived ON tasks(archived);
