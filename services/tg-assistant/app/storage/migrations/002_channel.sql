-- 002_channel.sql — мониторинг Telegram-группы с топиками (forum supergroup).
-- Сообщения из топиков парсятся в Obsidian; таблица хранит статус парсинга
-- для идемпотентности и обработки пропущенных сообщений.

CREATE TABLE IF NOT EXISTS channel_messages (
    id              INTEGER PRIMARY KEY,
    channel_id      INTEGER NOT NULL,     -- Telegram chat id (отрицательный для групп)
    topic_id        INTEGER,              -- message_thread_id (NULL = General)
    message_id      INTEGER NOT NULL,     -- Telegram message_id
    text            TEXT,                 -- текст сообщения (NULL если медиа без caption)
    caption         TEXT,                 -- caption для медиафайлов
    media_type      TEXT,                 -- photo/document/video/audio/voice/sticker/NULL
    media_file_id   TEXT,                 -- file_id для скачивания если нужно
    from_user_id    INTEGER,              -- кто написал
    sender_name     TEXT,                 -- отображаемое имя
    sent_at         TEXT NOT NULL,        -- ISO8601
    parsed          INTEGER NOT NULL DEFAULT 0,   -- 0=pending, 1=done, -1=error
    parsed_at       TEXT,
    obsidian_path   TEXT,                 -- куда записали в Obsidian
    parse_error     TEXT,                 -- текст ошибки при parsed=-1
    UNIQUE(channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_messages_parsed ON channel_messages(parsed);
CREATE INDEX IF NOT EXISTS idx_channel_messages_channel_topic ON channel_messages(channel_id, topic_id);
