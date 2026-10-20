-- المهاجرة 002: نموذج البيانات الأساسي للمرحلة 1 و2
-- يشمل: المستخدمات، الجلسات (تجديد التوكن)، الملفات الشخصية، السجل اليومي،
-- التوقعات بنطاق، التذكيرات، والموافقات الصريحة.

CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    pin_hash       TEXT NOT NULL DEFAULT '',
    pin_salt       TEXT NOT NULL DEFAULT '',
    disguise_mode  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    expires_at         TIMESTAMPTZ NOT NULL,
    revoked_at         TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_id);

CREATE TABLE IF NOT EXISTS profiles (
    user_key                  TEXT PRIMARY KEY,
    user_id                   INTEGER REFERENCES users(id) ON DELETE CASCADE,
    goal                      TEXT NOT NULL DEFAULT 'track',
    age                       INTEGER,
    country_code              TEXT NOT NULL DEFAULT 'EG',
    pregnancy_status          TEXT,
    contraception             TEXT,
    diagnosed_conditions_json TEXT NOT NULL DEFAULT '[]',
    medications_json          TEXT NOT NULL DEFAULT '[]',
    typical_cycle_days        INTEGER,
    typical_bleeding_days     INTEGER,
    onboarding_completed      BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles (user_id);

CREATE TABLE IF NOT EXISTS cycles (
    id          SERIAL PRIMARY KEY,
    user_key    TEXT NOT NULL,
    start_date  DATE NOT NULL,
    length_days INTEGER,
    flow_level  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_key, start_date)
);

CREATE INDEX IF NOT EXISTS idx_cycles_user_date ON cycles (user_key, start_date);

CREATE TABLE IF NOT EXISTS daily_logs (
    id          SERIAL PRIMARY KEY,
    user_key    TEXT NOT NULL,
    log_date    DATE NOT NULL,
    flow        TEXT,
    mood        TEXT,
    sleep_hours REAL,
    water_cups  INTEGER,
    exercise    TEXT,
    medications TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_key, log_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_logs_user_date ON daily_logs (user_key, log_date);

CREATE TABLE IF NOT EXISTS symptoms (
    id         SERIAL PRIMARY KEY,
    user_key   TEXT NOT NULL,
    log_date   DATE NOT NULL,
    symptom    TEXT NOT NULL,
    severity   INTEGER,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_symptoms_user_date ON symptoms (user_key, log_date);

CREATE TABLE IF NOT EXISTS predictions (
    id           SERIAL PRIMARY KEY,
    user_key     TEXT NOT NULL,
    cycle_id     INTEGER,
    window_start DATE NOT NULL,
    window_end   DATE NOT NULL,
    confidence   TEXT NOT NULL,
    basis_json   TEXT NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions (user_key, window_start);

CREATE TABLE IF NOT EXISTS reminders (
    id         SERIAL PRIMARY KEY,
    user_key   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    time_hhmm  TEXT NOT NULL DEFAULT '20:00',
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_key);

CREATE TABLE IF NOT EXISTS consents (
    id           SERIAL PRIMARY KEY,
    user_key     TEXT NOT NULL,
    consent_type TEXT NOT NULL,
    granted_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_key, consent_type)
);

CREATE INDEX IF NOT EXISTS idx_consents_user ON consents (user_key, consent_type);
