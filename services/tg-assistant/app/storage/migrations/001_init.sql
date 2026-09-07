-- 001_init.sql — начальная схема (§5 ARCHITECTURE).
-- Все 5 таблиц + seed расписания.

-- === checkins: каждая сессия чек-ина (§5.1) ===
CREATE TABLE IF NOT EXISTS checkins (
    id            INTEGER PRIMARY KEY,
    date          TEXT NOT NULL,                 -- YYYY-MM-DD (локальная дата слота)
    slot          TEXT NOT NULL,                 -- morning|day|evening|situational
    status        TEXT NOT NULL,                 -- in_progress|done|abandoned
    started_at    TEXT NOT NULL,                 -- ISO8601
    finished_at   TEXT,
    answers_json  TEXT NOT NULL DEFAULT '{}',    -- {metric_key: value} накопительно
    flags_json    TEXT NOT NULL DEFAULT '[]',    -- вычисленные флаги
    written       INTEGER NOT NULL DEFAULT 0     -- 1 = успешно записан в Obsidian
);
CREATE INDEX IF NOT EXISTS idx_checkins_date_slot ON checkins(date, slot);
CREATE INDEX IF NOT EXISTS idx_checkins_status ON checkins(status);

-- === schedule: настраиваемое расписание (§5.2, §2 плана) ===
CREATE TABLE IF NOT EXISTS schedule (
    slot         TEXT PRIMARY KEY,               -- morning|day|evening
    window_start TEXT NOT NULL,                  -- '10:30'
    window_end   TEXT NOT NULL,                  -- '11:00' (джиттер внутри окна)
    enabled      INTEGER NOT NULL DEFAULT 1
);

-- seed стартового расписания (§2 плана). ON CONFLICT — не перетирать пользовательские правки.
INSERT INTO schedule (slot, window_start, window_end, enabled) VALUES
    ('morning', '10:30', '11:00', 1),
    ('day',     '15:00', '16:00', 1),
    ('evening', '22:30', '23:00', 1)
ON CONFLICT(slot) DO NOTHING;

-- === settings: key-value (§5.3) ===
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO settings (key, value) VALUES
    ('light_day_weekday', '6'),        -- воскресенье: только утро
    ('situational_enabled', '1'),
    ('tone', 'neutral')
ON CONFLICT(key) DO NOTHING;

-- === outbox: надёжная доставка записи в Obsidian (§5.4, ADR-2) ===
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY,
    checkin_id   INTEGER NOT NULL REFERENCES checkins(id),
    payload_json TEXT NOT NULL,                  -- что дописать (frontmatter-патч + секция тела)
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    next_attempt_at TEXT,                         -- ISO8601, для backoff
    created_at   TEXT NOT NULL,
    done         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_done ON outbox(done);

-- === rotation_state: анти-повтор формулировок (§5.5, ADR-7) ===
CREATE TABLE IF NOT EXISTS rotation_state (
    pool_key   TEXT PRIMARY KEY,                  -- body_question | morning_q3 | evening_resource
    last_index INTEGER NOT NULL DEFAULT -1,
    updated_at TEXT NOT NULL
);
