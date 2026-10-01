-- 001_kb_pgvector.sql
-- قاعدة المعرفة: pgvector + جداول المصادر والمقاطع + الفهارس.
--
-- حالة التحقق: طُبّق وجُرّب على PostgreSQL 16 + pgvector (tests/test_postgres_store.py،
-- ويشمل تطبيقه مرتين بلا خطأ). تنفيذ SQLite المطابق في app/kb/store.py.
--
-- التشغيل:
--   psql "$DATABASE_URL" -f migrations/001_kb_pgvector.sql
-- أو عبر Alembic بعد توليد ملف الترحيل من هذا الملف.

CREATE EXTENSION IF NOT EXISTS vector;

-- --------------------------------------------------------------------- المصادر
CREATE TABLE IF NOT EXISTS kb_sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT        NOT NULL,
    language            TEXT        NOT NULL DEFAULT 'ar',
    url                 TEXT        NOT NULL DEFAULT '',
    licence             TEXT        NOT NULL DEFAULT '',
    -- قرار بشري فقط: لا يُضبط آليًا أبدًا. البريف يمنع استيراد نص محمي بحقوق نشر.
    approved_for_ingest BOOLEAN     NOT NULL DEFAULT FALSE,
    notes               TEXT        NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------------- المقاطع
CREATE TABLE IF NOT EXISTS kb_chunks (
    id                    TEXT PRIMARY KEY,
    source_id             TEXT        NOT NULL REFERENCES kb_sources(id) ON DELETE RESTRICT,
    title                 TEXT        NOT NULL DEFAULT '',
    topic                 TEXT        NOT NULL DEFAULT '',
    language              TEXT        NOT NULL DEFAULT 'ar',
    content               TEXT        NOT NULL,
    -- 1024 بُعد مطابق للنموذجين المرشّحين (bge-m3 / multilingual-e5-large)
    embedding             VECTOR(1024),
    status                TEXT        NOT NULL DEFAULT 'draft_unreviewed'
                          CHECK (status IN ('draft_unreviewed', 'physician_reviewed',
                                            'approved', 'retired')),
    reviewed_by           TEXT        NOT NULL DEFAULT '',
    authored_by           TEXT        NOT NULL DEFAULT '',
    license_note          TEXT        NOT NULL DEFAULT '',
    reviewed_at           DATE,
    content_version       INTEGER     NOT NULL DEFAULT 1,
    source_refs_to_verify JSONB       NOT NULL DEFAULT '[]'::jsonb,
    content_hash          TEXT        NOT NULL DEFAULT '',
    -- نص البحث العربي بعد التطبيع (بلا تشكيل، موحّد الألف/الياء/التاء المربوطة)
    tsv                   TSVECTOR,
    -- نص مُطبَّع يُستخدم لتعبئة tsv وللبحث الكلمي البسيط
    search_text           TEXT        NOT NULL DEFAULT '',
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- فهرس HNSW للمتجهات: يخدم البحث التقريبي الأقرب (cosine).
-- m و ef_construction قيم متوازنة لبيانات بحجم عشرات الآلاف من المقاطع.
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding
    ON kb_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- فهرس GIN للبحث الكلمي عبر tsv
CREATE INDEX IF NOT EXISTS idx_kb_chunks_tsv ON kb_chunks USING gin (tsv);

-- التصفية بالحالة واللغة تسبق أي بحث، فالفهرس عليها يهم
CREATE INDEX IF NOT EXISTS idx_kb_chunks_status_lang ON kb_chunks (status, language);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks (source_id);

-- --------------------------------------------------------------------- دالة tsv
-- تُبنى من نص مُطبَّع عربيًا. `simple` لا `arabic`: الإعداد العربي في PostgreSQL
-- يعتمد على snowball وقد يقطع الكلمات العربية بطريقة غير مناسبة للمصطلحات
-- الطبية؛ استخدمنا التطبيع في بايثون ونُبقيه هنا تكوينًا لغويًا بسيطًا.
CREATE OR REPLACE FUNCTION kb_chunks_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('simple', COALESCE(NEW.search_text, ''));
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kb_chunks_tsv ON kb_chunks;
CREATE TRIGGER trg_kb_chunks_tsv
    BEFORE INSERT OR UPDATE OF search_text ON kb_chunks
    FOR EACH ROW EXECUTE FUNCTION kb_chunks_tsv_update();

-- --------------------------------------------------------------------- عرض مساعد
-- المقاطع القابلة للاسترجاع في الإنتاج (approved فقط). بيئة تجريبية تُضيف
-- draft_unreviewed عبر KB_ALLOW_DRAFT في التطبيق لا هنا.
CREATE OR REPLACE VIEW kb_retrievable AS
    SELECT id, source_id, title, topic, language, content
    FROM kb_chunks
    WHERE status = 'approved';

-- ------------------------------------------------------------------ استعلامات مرجعية
-- بحث متجهي (يُستخدم في app/kb/postgres.py):
--   SELECT id, 1 - (embedding <=> $1::vector) AS similarity
--   FROM kb_chunks
--   WHERE status = ANY($2) AND ($3::text IS NULL OR language = $3)
--     AND 1 - (embedding <=> $1::vector) >= $4
--   ORDER BY embedding <=> $1::vector
--   LIMIT 20;
--
-- بحث كلمي:
--   الدرجة = نسبة كلمات الاستعلام الموجودة (OR وليس AND) — الاستعلام الكامل في
--   PostgresKbStore.keyword_search. لا تستبدليه بـ plainto_tsquery(...) على السؤال
--   كله: يشترط وجود كل الكلمات فيعيد صفرًا لأي سؤال طبيعي.
--
-- تحذير: تغيير نموذج التضمين يغيّر فضاء المتجهات. يجب إعادة تضمين كل المقاطع
-- (ingest --reembed) وإلا صار البحث يقارن متجهات من فضاءين مختلفين.
