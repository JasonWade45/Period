"""اختبارات تنفيذ PostgreSQL + pgvector على قاعدة حقيقية (`pytest -m postgres`).

نفس عقد `SqliteKbStore` (tests/test_kb_core.py) يُفحص هنا على PostgreSQL:
دورة حياة الحالة، إعادة الاستيراد، مسح المتجه عند تغيّر المحتوى، والاسترجاع
الهجين عبر `<=>` وفهرس GIN. الفرق الوحيد: المتجهات 1024 بُعدًا (VECTOR(1024)).

مصدر قاعدة البيانات، بالترتيب:
1. `TEST_DATABASE_URL` — خادم موجود (CI). الجداول تُحذف وتُعاد قبل كل اختبار،
   فلا تشيري إلى قاعدة فيها بيانات حقيقية.
2. حزمة `pgserver` (PostgreSQL مدمج مع pgvector): `pip install pgserver "psycopg[binary]"`.
3. غير ذلك ⇒ تُتخطّى الاختبارات.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kb.embedding import EMBEDDING_DIM, DeterministicLocalEmbedder
from app.kb.retrieval import HybridRetriever
from app.kb.schemas import ChunkStatus, KbChunk, KbSource, TransitionError

pytestmark = pytest.mark.postgres

psycopg = pytest.importorskip("psycopg")

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_kb_pgvector.sql"


@pytest.fixture(scope="module")
def dsn(tmp_path_factory):
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="delete")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture
def store(dsn):
    from app.kb.postgres import PostgresKbStore
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS kb_chunks, kb_sources CASCADE")
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    st = PostgresKbStore(dsn)
    st.upsert_source(KbSource(id="src-1", name="مصدر اختبار"))
    st.upsert_source(KbSource(id="s", name="مصدر اختبار"))
    return st


def _chunk(chunk_id="kb-1", content="طول الدورة من 21 إلى 35 يومًا", **kw) -> KbChunk:
    return KbChunk(id=chunk_id, source_id=kw.pop("source_id", "src-1"), title="طول الدورة",
                   topic="cycle", language=kw.pop("language", "ar"), content=content, **kw)


def _approve(store, chunk_id):
    store.set_status(chunk_id, ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")
    store.set_status(chunk_id, ChunkStatus.APPROVED, "د. فلانة", "2026-10-02")


def _embed_all(store, embedder):
    for chunk in store.list_chunks():
        store.set_embedding(chunk.id, embedder.embed([chunk.search_text])[0])


def _retriever(store, embedder, **kw):
    kw.setdefault("min_similarity", 0.0)
    return HybridRetriever(store, embedder, **kw)


@pytest.fixture
def embedder():
    return DeterministicLocalEmbedder(dim=EMBEDDING_DIM)


# ------------------------------------------------------------------ المخطط
def test_migration_is_idempotent(dsn, store):
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))   # مرة ثانية بلا خطأ
        indexes = {r[0] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='kb_chunks'")}
    assert {"idx_kb_chunks_embedding", "idx_kb_chunks_tsv"} <= indexes


def test_health_reports_pgvector(store):
    info = store.health()
    assert info["pgvector"]
    assert info["expected_dim"] == EMBEDDING_DIM


def test_chunk_requires_existing_source(store):
    with pytest.raises(Exception):
        store.upsert_chunk(_chunk(source_id="missing"))


def test_status_check_constraint_rejects_unknown_status(dsn, store):
    store.upsert_chunk(_chunk())
    with psycopg.connect(dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE kb_chunks SET status='verified' WHERE id='kb-1'")


# ------------------------------------------------------------ دورة الحالة
def test_new_chunk_is_draft_unreviewed(store):
    assert store.upsert_chunk(_chunk()) == "created"
    assert store.get_chunk("kb-1").status == ChunkStatus.DRAFT_UNREVIEWED


def test_skipping_physician_review_is_refused(store):
    store.upsert_chunk(_chunk())
    with pytest.raises(TransitionError):
        store.set_status("kb-1", ChunkStatus.APPROVED, "د. فلانة", "2026-10-01")


def test_status_change_requires_reviewer_and_date(store):
    store.upsert_chunk(_chunk())
    with pytest.raises(ValueError):
        store.set_status("kb-1", ChunkStatus.PHYSICIAN_REVIEWED, "  ", "2026-10-01")
    with pytest.raises(ValueError):
        store.set_status("kb-1", ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "")


def test_unknown_chunk_status_change_fails(store):
    with pytest.raises(ValueError):
        store.set_status("nope", ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")


# ----------------------------------------------------------- إعادة الاستيراد
def test_reingest_same_content_keeps_status_and_version(store):
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    assert store.upsert_chunk(_chunk()) == "unchanged"
    chunk = store.get_chunk("kb-1")
    assert chunk.status == ChunkStatus.APPROVED
    assert chunk.content_version == 1
    assert chunk.reviewed_by == "د. فلانة"
    assert chunk.reviewed_at == "2026-10-02"


def test_reingest_ignores_formatting_differences(store):
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا"))
    _approve(store, "kb-1")
    assert store.upsert_chunk(_chunk(content="طول  الدورة من ٢١ إلى ٣٥ يومًا")) == "unchanged"
    assert store.get_chunk("kb-1").status == ChunkStatus.APPROVED


def test_content_change_resets_to_draft_and_drops_embedding(store, embedder):
    store.upsert_chunk(_chunk(content="النص القديم عن طول الدورة"))
    _approve(store, "kb-1")
    store.set_embedding("kb-1", embedder.embed(["قديم"])[0])
    assert store.get_embedding("kb-1") is not None

    assert store.upsert_chunk(_chunk(content="نص جديد تمامًا عن النزيف الغزير")) == "content_changed"
    chunk = store.get_chunk("kb-1")
    assert chunk.status == ChunkStatus.DRAFT_UNREVIEWED
    assert chunk.content_version == 2
    assert chunk.reviewed_by == ""
    assert store.get_embedding("kb-1") is None


def test_wrong_embedding_dimension_is_rejected(store):
    store.upsert_chunk(_chunk())
    with pytest.raises(ValueError):
        store.set_embedding("kb-1", [0.1] * 8)


def test_embedding_roundtrip(store, embedder):
    store.upsert_chunk(_chunk())
    vec = embedder.embed(["طول الدورة"])[0]
    store.set_embedding("kb-1", vec)
    back = store.get_embedding("kb-1")
    assert len(back) == EMBEDDING_DIM
    assert back[:5] == pytest.approx(vec[:5], abs=1e-5)


def test_provenance_fields_roundtrip(store):
    store.upsert_chunk(_chunk(authored_by="ai_draft", license_note="مسودة داخلية",
                              source_refs_to_verify=[{"name": "NHS", "url": "x"}]))
    chunk = store.get_chunk("kb-1")
    assert chunk.authored_by == "ai_draft"
    assert chunk.license_note == "مسودة داخلية"
    assert chunk.source_refs_to_verify == [{"name": "NHS", "url": "x"}]


# ----------------------------------------------------------------- الاسترجاع
def test_no_approved_chunks_returns_empty(store, embedder):
    store.upsert_chunk(_chunk())
    _embed_all(store, embedder)
    assert _retriever(store, embedder, allow_draft=False).retrieve("طول الدورة").chunks == []


def test_drafts_retrievable_only_with_flag(store, embedder):
    store.upsert_chunk(_chunk())
    _embed_all(store, embedder)
    assert _retriever(store, embedder, allow_draft=True).retrieve("طول الدورة").chunks


def test_approved_chunk_is_found_by_vector_and_keyword(store, embedder):
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا عادةً"))
    _approve(store, "kb-1")
    _embed_all(store, embedder)
    result = _retriever(store, embedder, min_keyword_score=0.0).retrieve("طول الدورة الطبيعي")
    assert [c.id for c in result.chunks] == ["kb-1"]
    assert result.chunks[0].vector_rank and result.chunks[0].keyword_rank


def test_keyword_search_works_without_embeddings(store):
    """فهرس GIN/tsv يعمل منفصلًا عن المتجهات، ويتجاهل التشكيل والهمزات."""
    store.upsert_chunk(_chunk(content="النزيفُ الغزير يستدعي استشارة طبيبة"))
    _approve(store, "kb-1")
    hits = store.keyword_search("النزيف الغزير", 10, [ChunkStatus.APPROVED], "ar")
    assert [h[0] for h in hits] == ["kb-1"]


def test_physician_reviewed_and_retired_are_not_retrievable(store, embedder):
    store.upsert_chunk(_chunk("a"))
    store.upsert_chunk(_chunk("b"))
    store.set_status("a", ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")
    _approve(store, "b")
    store.set_status("b", ChunkStatus.RETIRED, "د. فلانة", "2026-10-03")
    _embed_all(store, embedder)
    for allow_draft in (False, True):
        assert _retriever(store, embedder, allow_draft=allow_draft).retrieve("طول الدورة").chunks == []


def test_language_filter(store, embedder):
    store.upsert_chunk(_chunk("ar-1", content="طول الدورة 21 إلى 35 يومًا"))
    store.upsert_chunk(_chunk("en-1", content="cycle length 21 to 35 days", language="en"))
    _approve(store, "ar-1")
    _approve(store, "en-1")
    _embed_all(store, embedder)
    ids = [c.id for c in _retriever(store, embedder, min_keyword_score=0.0,
                                    language="ar").retrieve("طول الدورة").chunks]
    assert ids == ["ar-1"]


def test_similarity_threshold_is_applied_in_sql(store, embedder):
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    _embed_all(store, embedder)
    q = embedder.embed(["سؤال لا علاقة له إطلاقًا"])[0]
    assert store.vector_search(q, 5, [ChunkStatus.APPROVED], "ar", 0.99) == []


def test_sqlite_and_postgres_agree_on_ranking(store, embedder, tmp_path):
    """نفس البيانات والاستعلام ⇒ نفس ترتيب المقاطع في التنفيذين."""
    from app.kb.store import SqliteKbStore
    lite = SqliteKbStore(tmp_path / "kb.db")
    texts = {"k1": "طول الدورة من 21 إلى 35 يومًا", "k2": "النزيف الغزير يحتاج مراجعة",
             "k3": "التغذية والحديد والإرهاق"}
    for target in (store, lite):
        for cid, text in texts.items():
            target.upsert_chunk(_chunk(cid, content=text))
            _approve(target, cid)
        _embed_all(target, embedder)
    pg = [c.id for c in _retriever(store, embedder, min_keyword_score=0.0).retrieve("طول الدورة").chunks]
    lt = [c.id for c in _retriever(lite, embedder, min_keyword_score=0.0).retrieve("طول الدورة").chunks]
    assert pg == lt and pg


def test_keyword_score_is_fraction_of_query_tokens_like_sqlite(store, tmp_path):
    """تطابق جزئي ⇒ درجة كسرية متطابقة في التنفيذين (العتبة تعتمد عليها)."""
    from app.kb.store import SqliteKbStore
    lite = SqliteKbStore(tmp_path / "kb2.db")
    for target in (store, lite):
        target.upsert_source(KbSource(id="src-1", name="x"))
        target.upsert_chunk(_chunk("k1", content="طول الدورة من 21 إلى 35 يومًا"))
        target.upsert_chunk(_chunk("k2", content="الدورة الشهرية والنزيف"))
        for cid in ("k1", "k2"):
            _approve(target, cid)
    q = "طول الدورة الطبيعي؟"       # «الطبيعي» غير موجودة في أي مقطع
    pg = store.keyword_search(q, 10, [ChunkStatus.APPROVED], "ar")
    lt = lite.keyword_search(q, 10, [ChunkStatus.APPROVED], "ar")
    assert [h[0] for h in pg] == [h[0] for h in lt] == ["k1", "k2"]
    assert [round(h[1], 3) for h in pg] == [round(h[1], 3) for h in lt]


def test_keyword_search_survives_hostile_input(store):
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    for q in ("'; DROP TABLE kb_chunks; --", "a & b | !c :* (", "\\' \" <-> ()", "!!!"):
        store.keyword_search(q, 10, [ChunkStatus.APPROVED], "ar")   # لا استثناء
    assert store.get_chunk("kb-1") is not None
