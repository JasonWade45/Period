"""نواة قاعدة المعرفة: التطبيع، الحالات، الاستيراد المتكرر، الاسترجاع.

هذا الملف يحرس معايير القبول الحسّاسة:
- `KB_ALLOW_DRAFT=false` وصفر مقاطع معتمدة ⇒ استرجاع فارغ (لا تخمين).
- إعادة الاستيراد لا تغيّر حالة مقطع لم يتغيّر محتواه.
- المحتوى المتغيّر يُعاد إلى `draft_unreviewed`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.kb.embedding import DeterministicLocalEmbedder
from app.kb.ingest import ingest_records, load_registry, read_records
from app.kb.retrieval import HybridRetriever, producible_statuses
from app.kb.schemas import (
    ChunkStatus,
    IngestRecord,
    KbChunk,
    KbSource,
    TransitionError,
    can_transition,
)
from app.kb.store import SqliteKbStore
from app.services.arabic import (
    normalize_arabic,
    normalize_digits,
    normalize_for_search,
    search_tokens,
)


@pytest.fixture
def store(tmp_path) -> SqliteKbStore:
    return SqliteKbStore(tmp_path / "kb.db")


def _chunk(chunk_id: str = "kb-1", content: str = "طول الدورة من 21 إلى 35 يومًا",
           status: ChunkStatus = ChunkStatus.DRAFT_UNREVIEWED) -> KbChunk:
    return KbChunk(id=chunk_id, source_id="src-1", title="طول الدورة",
                   topic="cycle", language="ar", content=content, status=status)


def _approve(store: SqliteKbStore, chunk_id: str) -> None:
    store.set_status(chunk_id, ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")
    store.set_status(chunk_id, ChunkStatus.APPROVED, "د. فلانة", "2026-10-02")


# ============================================================== التطبيع العربي

def test_tashkeel_is_stripped():
    assert normalize_arabic("نَزِيــف شَدِيد") == "نزيف شديد"


def test_alef_variants_are_unified():
    for variant in ("ألم", "إلم", "آلم", "ٱلم"):
        assert normalize_arabic(variant) == "الم"


def test_yaa_and_taa_marbuta_are_unified():
    assert normalize_arabic("منتظمة") == normalize_arabic("منتظمه")
    assert normalize_arabic("مستشفى") == normalize_arabic("مستشفي")


@pytest.mark.parametrize("text,expected", [
    ("٤٥", "45"),
    ("٠١٢٣٤٥٦٧٨٩", "0123456789"),
    ("۰۱۲۳۴۵۶۷۸۹", "0123456789"),
    ("الدورة ٢٨ يومًا", "الدورة 28 يومًا"),
    ("mixed ٤٥ and 45", "mixed 45 and 45"),
])
def test_arabic_indic_digits_are_normalised(text, expected):
    assert normalize_digits(text) == expected


def test_digit_normalisation_applies_inside_normalize_arabic():
    assert "45" in normalize_arabic("٤٥ يومًا")


def test_search_normalisation_removes_punctuation_and_case():
    assert normalize_for_search("النزيف الغزير!") == "النزيف الغزير"
    assert normalize_for_search("Heavy Bleeding") == "heavy bleeding"


def test_search_tokens_drop_single_letters():
    # «و» تُحذف لطولها، والتطبيع يحوّل ة → ه قبل التقطيع
    assert search_tokens("و من الدورة") == ["من", "الدوره"]


# ============================================================== دورة حياة الحالة

def test_new_chunk_is_draft_unreviewed(store):
    assert store.upsert_chunk(_chunk()) == "created"
    assert store.get_chunk("kb-1").status == ChunkStatus.DRAFT_UNREVIEWED


@pytest.mark.parametrize("old,new,allowed", [
    (ChunkStatus.DRAFT_UNREVIEWED, ChunkStatus.PHYSICIAN_REVIEWED, True),
    (ChunkStatus.DRAFT_UNREVIEWED, ChunkStatus.APPROVED, False),      # لا قفز
    (ChunkStatus.PHYSICIAN_REVIEWED, ChunkStatus.APPROVED, True),
    (ChunkStatus.APPROVED, ChunkStatus.RETIRED, True),
    (ChunkStatus.RETIRED, ChunkStatus.APPROVED, False),               # يحتاج مراجعة
    (ChunkStatus.RETIRED, ChunkStatus.DRAFT_UNREVIEWED, True),
])
def test_transition_rules(old, new, allowed):
    assert can_transition(old, new) is allowed


def test_skipping_physician_review_is_refused(store):
    store.upsert_chunk(_chunk())
    with pytest.raises(TransitionError):
        store.set_status("kb-1", ChunkStatus.APPROVED, "د. فلانة", "2026-10-01")


def test_status_change_requires_reviewer_name(store):
    store.upsert_chunk(_chunk())
    with pytest.raises(Exception):
        store.set_status("kb-1", ChunkStatus.PHYSICIAN_REVIEWED, "   ", "2026-10-01")


# ============================================================ الاستيراد المتكرر

def test_reingest_same_content_does_not_change_status(store):
    """معيار قبول: إعادة الاستيراد لا تغيّر حالة المقاطع غير المتغيّرة."""
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    assert store.get_chunk("kb-1").status == ChunkStatus.APPROVED

    outcome = store.upsert_chunk(_chunk())      # نفس المحتوى حرفيًا
    assert outcome == "unchanged"
    chunk = store.get_chunk("kb-1")
    assert chunk.status == ChunkStatus.APPROVED          # لم تُمسّ
    assert chunk.content_version == 1                     # لم يزد
    assert chunk.reviewed_by == "د. فلانة"


def test_reingest_ignores_formatting_differences(store):
    """اختلاف تشكيل أو مسافة أو أرقام عربية ليس «تغيّر محتوى»."""
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا"))
    _approve(store, "kb-1")
    outcome = store.upsert_chunk(_chunk(content="طول  الدورة من ٢١ إلى ٣٥ يومًا"))
    assert outcome == "unchanged"
    assert store.get_chunk("kb-1").status == ChunkStatus.APPROVED


def test_content_change_resets_status_to_draft(store):
    store.upsert_chunk(_chunk(content="النص القديم عن طول الدورة"))
    _approve(store, "kb-1")

    outcome = store.upsert_chunk(_chunk(content="نص جديد تمامًا عن النزيف الغزير"))
    assert outcome == "content_changed"
    chunk = store.get_chunk("kb-1")
    assert chunk.status == ChunkStatus.DRAFT_UNREVIEWED    # أُعيد للمراجعة
    assert chunk.content_version == 2
    assert chunk.reviewed_by == ""                          # الاعتماد القديم سقط


def test_embedding_is_dropped_when_content_changes(store):
    """متجه نص قديم على نص جديد = نتائج بحث لا تخص المحتوى الحالي."""
    store.upsert_chunk(_chunk(content="نص قديم"))
    store.set_embedding("kb-1", [0.1] * 8)
    store.upsert_chunk(_chunk(content="نص جديد مختلف"))
    assert store.get_embedding("kb-1") is None


# ====================================================== بوابة الرخصة في الاستيراد

def _registry(tmp_path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"sources": [
        {"id": "approved-src", "name": "مصدر معتمد", "licence": "CC-BY",
         "approved_for_ingest": True},
        {"id": "unapproved-src", "name": "مصدر لم تُراجَع رخصته",
         "approved_for_ingest": False},
    ]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_registry_parses_approval_flags(tmp_path):
    registry = load_registry(_registry(tmp_path))
    assert registry["approved-src"].approved_for_ingest is True
    assert registry["unapproved-src"].approved_for_ingest is False


def test_missing_registry_means_nothing_approved(tmp_path):
    assert load_registry(tmp_path / "nope.json") == {}


def test_ingest_refuses_chunks_from_unapproved_sources(store, tmp_path):
    registry = load_registry(_registry(tmp_path))
    records = [
        IngestRecord(id="ok-1", source_id="approved-src", content="محتوى معتمد"),
        IngestRecord(id="no-1", source_id="unapproved-src", content="محتوى غير معتمد"),
    ]
    report = ingest_records(store, records, registry,
                            embedder=DeterministicLocalEmbedder(dim=32))

    assert report.created == ["ok-1"]
    assert report.skipped_unapproved_source == [("no-1", "unapproved-src")]
    assert store.get_chunk("no-1") is None      # لم يُكتب إطلاقًا


def test_ingest_skips_everything_when_registry_is_empty(store, tmp_path):
    records = [IngestRecord(id="x", source_id="s", content="محتوى")]
    report = ingest_records(store, records, {}, embedder=DeterministicLocalEmbedder(dim=32))
    assert report.created == []
    assert len(report.skipped_unapproved_source) == 1


def test_invalid_records_are_reported_not_fatal(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text("\n".join([
        json.dumps({"id": "a", "source_id": "s", "content": "نص"}, ensure_ascii=False),
        "{ليس JSON",
        json.dumps({"id": "c", "source_id": "s", "content": ""}, ensure_ascii=False),
        json.dumps({"id": "d", "content": "بلا مصدر"}, ensure_ascii=False),
    ]), encoding="utf-8")

    records, invalid = read_records(path)
    assert [r.id for r in records] == ["a"]
    assert len(invalid) == 3


# ================================================================== الاسترجاع

def test_producible_statuses_depend_on_flag():
    assert producible_statuses(allow_draft=False) == [ChunkStatus.APPROVED]
    assert set(producible_statuses(allow_draft=True)) == {
        ChunkStatus.APPROVED, ChunkStatus.DRAFT_UNREVIEWED}


def test_no_approved_chunks_returns_empty(store):
    """معيار قبول: صفر معتمد ⇒ لا نتائج (والمساعد يقول مافيش مصدر موثوق)."""
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا"))
    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                allow_draft=False, min_similarity=0.0)

    result = retriever.retrieve("إيه طول الدورة الطبيعي؟")
    assert result.chunks == []
    assert "لا مقاطع مطابقة" in result.reason


def test_drafts_are_retrievable_only_with_flag(store):
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا"))

    with_flag = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                allow_draft=True, min_similarity=0.0)
    assert with_flag.retrieve("طول الدورة").chunks


def test_approved_chunk_is_retrievable(store):
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا عادةً"))
    _approve(store, "kb-1")
    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                min_similarity=0.0)

    result = retriever.retrieve("طول الدورة الطبيعي")
    assert [c.id for c in result.chunks] == ["kb-1"]
    assert result.chunks[0].vector_rank or result.chunks[0].keyword_rank


def test_physician_reviewed_is_not_retrievable_without_flag(store):
    """مراجعة الطبيب خطوة في الطريق، لا إذن بالاسترجاع."""
    store.upsert_chunk(_chunk())
    store.set_status("kb-1", ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")

    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                allow_draft=False, min_similarity=0.0)
    assert retriever.retrieve("طول الدورة").chunks == []


def test_retired_chunk_is_never_retrievable(store):
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    store.set_status("kb-1", ChunkStatus.RETIRED, "د. فلانة", "2026-10-03")

    for allow_draft in (False, True):
        retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                    allow_draft=allow_draft, min_similarity=0.0)
        assert retriever.retrieve("طول الدورة").chunks == []


def test_relevance_threshold_filters_weak_matches(store):
    store.upsert_chunk(_chunk(content="طول الدورة من 21 إلى 35 يومًا"))
    _approve(store, "kb-1")
    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                min_similarity=0.99, min_keyword_score=0.99)
    assert retriever.retrieve("سؤال لا علاقة له بالدورة إطلاقًا").chunks == []


def test_top_k_is_respected(store):
    for i in range(8):
        store.upsert_chunk(_chunk(chunk_id=f"kb-{i}",
                                  content=f"الدورة الشهرية والنزيف موضوع رقم {i}"))
        _approve(store, f"kb-{i}")
    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                min_similarity=0.0, min_keyword_score=0.0)
    result = retriever.retrieve("الدورة الشهرية", top_k=5)
    assert len(result.chunks) <= 5


def test_rrf_rewards_chunks_found_by_both_methods():
    """المقطع الذي يظهر في البحثين يسبق مقطعًا ظهر في واحد فقط."""
    merged = HybridRetriever._rrf(
        [("both", 0.90), ("vector-only", 0.80)],
        [("both", 5.0), ("keyword-only", 4.0)],
    )
    ids = [chunk_id for chunk_id, _ in merged]
    assert ids[0] == "both"
    assert set(ids) == {"both", "vector-only", "keyword-only"}


def test_rrf_uses_ranks_not_raw_scores():
    """مقياسان مختلفان لا يتنافسان بقيمهما: الرتبة هي ما يُحسب."""
    strong = [(f"s{i}", 0.99 - i * 0.01) for i in range(10)]
    weak = [("w", 0.01)]
    scores = dict(HybridRetriever._rrf(strong, weak))
    assert scores["w"] > scores["s9"]        # 1/(60+1) > 1/(60+10)


def test_language_filter_prefers_requested_language(store):
    store.upsert_chunk(KbChunk(id="ar-1", source_id="s", language="ar",
                               title="الدورة", content="طول الدورة 21 إلى 35 يومًا"))
    store.upsert_chunk(KbChunk(id="en-1", source_id="s", language="en",
                               title="Cycle", content="cycle length 21 to 35 days"))
    _approve(store, "ar-1")
    _approve(store, "en-1")

    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                min_similarity=0.0, min_keyword_score=0.0, language="ar")
    ids = [c.id for c in retriever.retrieve("طول الدورة").chunks]
    assert ids == ["ar-1"]


def test_empty_query_returns_nothing(store):
    store.upsert_chunk(_chunk())
    _approve(store, "kb-1")
    retriever = HybridRetriever(store, DeterministicLocalEmbedder(dim=64))
    assert retriever.retrieve("   ").chunks == []
