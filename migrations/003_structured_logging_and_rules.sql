-- المهاجرة 003: نظام الأسئلة السياقية، السجل اليومي الهيكلي، اشتقاق الدورات، وأحداث السلامة
-- استراتيجية الترحيل: متوافقة عكسيًا (Backward-Compatible Dual Migration)
-- تحافظ على جميع الأعمدة والمسارات القائمة (cycles.length_days, symptoms) مع إضافة
-- الجداول والأعمدة الجديدة لضمان عدم فقد أي إجابة ودعم التخطّي والمزامنة الحقلية.

-- 1) توسيع جدول الملف الشخصي (الاستبيان الأولي التدريجي — البند 3)
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS age_band TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS menarche_age INTEGER;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS last_period_start DATE;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS currently_bleeding BOOLEAN;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS cycle_len_typical TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS cycle_len_variation TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS bleeding_len_typical INTEGER;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS meds_affecting_bleeding_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS family_bleeding TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS show_sensitive_questions BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS enable_lifestyle_questions BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS answered_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS skipped_json TEXT NOT NULL DEFAULT '[]';

-- 2) توسيع جدول الدورات (البند 5.1 و5.2.7: فصل bleeding_days واشتقاق الدورات من السجلات)
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS bleeding_days INTEGER;
ALTER TABLE cycles ADD COLUMN IF NOT EXISTS derived_from_logs BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE cycles SET bleeding_days = length_days WHERE bleeding_days IS NULL AND length_days IS NOT NULL;

-- 3) توسيع جدول السجل اليومي الهيكلي (البند 4 و5.1)
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS log_uuid TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS bleeding_level TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS pain_score INTEGER;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS pain_blocks_life BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS mood_tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS energy INTEGER;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS pad_count INTEGER;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS soak_hourly BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS clots TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS dizzy BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS bleeding_color TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS discharge TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS intermenstrual BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS post_intercourse_bleeding BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS intercourse BOOLEAN;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS contraception_used TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS ovulation_test TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS pregnancy_test TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS bbt REAL;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS water_ml INTEGER;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS stress INTEGER;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS flags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS answered_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS skipped_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS field_timestamps_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS client_id TEXT;
ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'local';

-- 4) جدول سجلات الأعراض المرتبط بالسجل اليومي (symptom_logs — البند 5.1)
CREATE TABLE IF NOT EXISTS symptom_logs (
    id           SERIAL PRIMARY KEY,
    daily_log_id INTEGER REFERENCES daily_logs(id) ON DELETE CASCADE,
    user_key     TEXT NOT NULL,
    log_date     DATE NOT NULL,
    symptom_id   TEXT NOT NULL,
    severity     INTEGER CHECK (severity IS NULL OR (severity >= 1 AND severity <= 5)),
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_symptom_logs_daily ON symptom_logs (daily_log_id);
CREATE INDEX IF NOT EXISTS idx_symptom_logs_user_date ON symptom_logs (user_key, log_date);

-- 5) جدول سجل التعديلات على الحقول (log_edits — البند 5.1)
CREATE TABLE IF NOT EXISTS log_edits (
    id           SERIAL PRIMARY KEY,
    daily_log_id INTEGER NOT NULL REFERENCES daily_logs(id) ON DELETE CASCADE,
    user_key     TEXT NOT NULL,
    log_date     DATE NOT NULL,
    field        TEXT NOT NULL,
    old_val      TEXT,
    new_val      TEXT,
    edited_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_log_edits_daily ON log_edits (daily_log_id);

-- 6) جدول أحداث السلامة الحتمية (safety_events — البند 5.1: بلا نص حر إطلاقًا)
CREATE TABLE IF NOT EXISTS safety_events (
    id           SERIAL PRIMARY KEY,
    user_key     TEXT NOT NULL,
    log_date     DATE NOT NULL,
    kind         TEXT NOT NULL,          -- 'medical_emergency' | 'crisis'
    triggered_by TEXT NOT NULL,          -- معرّف الخيار المهيكل فقط (مثال: 'flag.heavy_soak')، بلا نص حر
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_safety_events_user ON safety_events (user_key, at);
