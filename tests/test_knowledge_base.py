"""بوابة المعرفة: ما يُستشهد به وما لا يُستشهد به.

أهم اختبارات هذا الملف: أن مسودة غير مُراجَعة لا يمكن أن تصل إلى رد مستخدمة،
حتى لو أشار إليها الموديل بنفسه، وحتى لو كانت موجودة على القرص.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.main as main
from app.config import settings
from app.schemas import SourceChunk
from app.services.knowledge_review import list_pending, promote, stats
from app.services.rag import DRAFT, VERIFIED, KeywordRag, KnowledgeError, load_chunks

REAL_SOURCES = Path(settings.sources_path)
REAL_DRAFTS = Path(settings.draft_sources_path)


# --------------------------------------------------------------- سلامة الملفات

def test_shipped_sources_are_owner_reviewed_and_license_held():
    """`sources.json` الآن معتمد ملكيًا (قرار المالك) — والمحجوز ترخيصيًا بقي محجوبًا.

    قرار مقصود (2026-09-30): لا يُشترط طبيب؛ المحتوى مرجعي إرشادي مع سطر
    استشارة الطبيب، والمراجع المسجّل هو صاحبة المشروع. المقاطع الستة التي نصها
    محجوب بانتظار الترخيص تبقى غير قابلة للاستشهاد مهما كان حالتها.
    """
    chunks = load_chunks([REAL_SOURCES])
    assert len(chunks) == 44
    assert all(c.status == VERIFIED for c in chunks)
    assert all(c.reviewer.strip() for c in chunks)
    citable = [c for c in chunks if not c.text_removed]
    assert len(citable) == 38
    # مع تضمين المسودات (وضع تجريبي): لا مسودة متبقية، والمحجوز لا يظهر
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=True)
    assert rag.draft_chunks == []
    assert len(rag.retrievable) == 38
    assert all(not c.text_removed for c in rag.retrievable)


def test_any_future_verified_chunk_must_carry_full_provenance():
    """الفحص نفسه يبقى على أي مقطع يضيفه إنسان لاحقًا بعد المراجعة."""
    for chunk in load_chunks([REAL_SOURCES]):
        assert chunk.status == VERIFIED
        assert chunk.source_name.strip()
        assert chunk.section.strip()
        assert chunk.reviewed_at
        assert chunk.reviewer.strip(), f"{chunk.id} بلا مراجع مُسجَّل"


def test_text_removed_chunks_are_never_retrievable_even_with_drafts_on():
    """مقطع أُزيل نصه بانتظار الترخيص لا يُسترجع ولو فُعِّل تضمين المسودات."""
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=True)
    removed = rag.text_removed_chunks
    assert len(removed) == 6
    for chunk in removed:
        assert chunk.text_removed is True
        assert chunk.attribution_unverified is True
        assert rag.is_citable(chunk.id) is False
        assert chunk not in rag.retrievable
    # البحث لا يُرجع أي مقطع أُزيل نصه مهما كان الاستعلام
    for query in ("طول الدورة", "نزيف غزير", "PMS", "تكيس"):
        assert all(not c.text_removed for c in rag.retrieve(query, top_k=50))


def test_shipped_drafts_were_all_promoted():
    """لا مسودة متبقية: كل المسودات رُقّت بمسار مراجعة مسجّل باسم المراجع."""
    assert load_chunks([REAL_DRAFTS]) == []
    assert list_pending(REAL_DRAFTS) == []


def test_no_shipped_chunk_impersonates_a_body():
    """لا ينسب أي مقطع — مسودة كان أو معتمدًا — نفسه إلى جهة حقيقية."""
    chunks = load_chunks([REAL_SOURCES]) + load_chunks([REAL_DRAFTS])
    assert chunks
    for chunk in chunks:
        assert not any(body in chunk.source_name for body in ("NHS", "ACOG", "NICE", "WHO"))


# ------------------------------------------------------------ بوابة الاسترجاع

def test_drafts_are_never_retrieved_by_default():
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=False)
    for query in ["تكيس المبايض", "بطانة الرحم المهاجرة", "نزيف غزير", "ألم الدورة",
                  "PCOS", "heavy bleeding", "PMS"]:
        for chunk in rag.retrieve(query, top_k=10):
            assert chunk.status == VERIFIED, f"ظهرت مسودة {chunk.id} لسؤال: {query}"


def test_draft_ids_are_not_citable():
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=False)
    for chunk in rag.draft_chunks:
        assert rag.is_citable(chunk.id) is False, f"{chunk.id} قابل للاستشهاد!"


def test_include_drafts_flag_gates_draft_retrieval(tmp_path):
    """تضمّن المسودات خيار صريح: محجوبة افتراضيًا، وتظهر فقط عند تفعيلها."""
    drafts, sources = _draft_files(tmp_path)

    off = KeywordRag(sources, drafts_path=drafts, include_drafts=False)
    assert off.draft_chunks                      # موجودة للعدّ والمراجعة
    assert off.retrieve("كلمة", top_k=10) == []  # لكنها محجوبة عن الاسترجاع

    on = KeywordRag(sources, drafts_path=drafts, include_drafts=True)
    assert any(c.status == DRAFT for c in on.retrieve("كلمة", top_k=10))


def test_no_verified_chunks_means_the_assistant_says_so():
    """معيار القبول: بلا مصدر معتمد يُقال ذلك صراحةً ولا يُستدعى الموديل.

    (نعزل المسار على مُسترجع فارغ بدل الاعتماد على بيانات الشحن.)
    """
    from app.i18n import get_translator
    from app.schemas import AiChatRequest, UserContext
    from tests.pipeline_helpers import build_pipeline

    class _WatchedLLM:
        calls = 0

        def complete(self, *args, **kwargs):
            _WatchedLLM.calls += 1
            return "{}", 1

    pipe = build_pipeline(llm=_WatchedLLM(), use_stubs=False)
    response = pipe.chat(AiChatRequest(message="إيه طول الدورة الشهرية؟",
                                       user_context=UserContext()))

    assert response.answer == get_translator().t("answer.no_reliable_source", "ar")
    assert response.sources_used == []
    assert _WatchedLLM.calls == 0


# -------------------------------------------------------- رفض الإسناد الناقص

@pytest.mark.parametrize("mutation,expected", [
    ({"source_name": ""}, "source_name"),
    ({"section": ""}, "section"),
    ({"text": ""}, "text"),
    ({"keywords": []}, "keywords"),
    ({"id": ""}, "id"),
    ({"status": "whatever"}, "status"),
])
def test_invalid_chunk_is_rejected(tmp_path, mutation, expected):
    chunk = {
        "id": "x", "source_name": "NHS", "section": "s", "reviewed_at": "2026-01-01",
        "reviewer": "NHS", "text": "t", "keywords": ["k"], "status": "verified",
    }
    chunk.update(mutation)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([chunk], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(KnowledgeError) as err:
        load_chunks([path])
    assert expected in str(err.value)


def test_verified_chunk_without_review_date_is_rejected(tmp_path):
    """الاستشهاد يتطلب تاريخ مراجعة — منعًا لمعرفة بلا سند زمني."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{
        "id": "x", "source_name": "NHS", "section": "s", "reviewer": "NHS",
        "reviewed_at": "", "text": "t", "keywords": ["k"], "status": "verified",
    }], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(KnowledgeError):
        load_chunks([path])


def test_duplicate_ids_are_rejected(tmp_path):
    chunk = {"id": "dup", "source_name": "NHS", "section": "s", "reviewer": "NHS",
             "reviewed_at": "2026-01-01", "text": "t", "keywords": ["k"]}
    path = tmp_path / "dup.json"
    path.write_text(json.dumps([chunk, chunk], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(KnowledgeError) as err:
        load_chunks([path])
    assert "مكرّر" in str(err.value)


def test_invalid_json_is_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KnowledgeError):
        load_chunks([path])


# ------------------------------------------------------------- سير المراجعة

def _draft_files(tmp_path) -> tuple[Path, Path]:
    drafts = tmp_path / "draft.json"
    sources = tmp_path / "sources.json"
    drafts.write_text(json.dumps([{
        "id": "d1", "source_name": "مسودة تثقيفية داخلية — غير مُراجَعة طبيًا",
        "section": "قسم", "reviewed_at": "", "drafted_at": "2026-09-30",
        "status": "draft", "derived_from": ["NHS: X"],
        "keywords": ["كلمة"], "text": "نص",
    }], ensure_ascii=False), encoding="utf-8")
    sources.write_text("[]", encoding="utf-8")
    return drafts, sources


def test_stats_counts_verified_and_pending(tmp_path):
    drafts, sources = _draft_files(tmp_path)
    result = stats(sources, drafts)
    assert result["verified"] == 0
    assert result["draft"] == 1
    assert result["draft_ids"] == ["d1"]


def test_promote_moves_chunk_out_of_drafts(tmp_path):
    drafts, sources = _draft_files(tmp_path)

    chunk = promote(drafts, sources, "d1", reviewer="د. فلانة — نسا وتوليد",
                    reviewed_at="2026-10-05")

    assert chunk.status == VERIFIED
    assert chunk.reviewer == "د. فلانة — نسا وتوليد"
    assert chunk.reviewed_at == "2026-10-05"
    # المقطع انتقل فعلًا: لم يعد مسودة، وصار مُتحقَّقًا وقابلًا للاستشهاد
    assert list_pending(drafts) == []
    rag = KeywordRag(sources, drafts_path=drafts)
    assert rag.is_citable("d1") is True
    assert rag.retrieve("كلمة", top_k=5)


def test_promote_requires_a_reviewer(tmp_path):
    drafts, sources = _draft_files(tmp_path)
    for bad in ("", "   "):
        with pytest.raises(KnowledgeError):
            promote(drafts, sources, "d1", reviewer=bad)
    assert len(list_pending(drafts)) == 1  # لم يتغيّر شيء


def test_promote_rejects_invalid_date(tmp_path):
    drafts, sources = _draft_files(tmp_path)
    with pytest.raises(KnowledgeError):
        promote(drafts, sources, "d1", reviewer="د. فلانة", reviewed_at="05/10/2026")


def test_promote_unknown_id_fails(tmp_path):
    drafts, sources = _draft_files(tmp_path)
    with pytest.raises(KnowledgeError):
        promote(drafts, sources, "nope", reviewer="د. فلانة")


def test_promote_twice_fails(tmp_path):
    drafts, sources = _draft_files(tmp_path)
    promote(drafts, sources, "d1", reviewer="د. فلانة")
    with pytest.raises(KnowledgeError):
        promote(drafts, sources, "d1", reviewer="د. فلانة")


# ---------------------------------------------------- ضمان على مستوى النظام

def test_production_rag_excludes_drafts_and_license_holds():
    """الإعداد الافتراضي في التطبيق نفسه: بلا مسودات، ومحجوز الترخيص خارج القابل للاستشهاد."""
    assert settings.knowledge_include_drafts is False
    chunks = load_chunks([REAL_SOURCES])
    citable = [c for c in chunks if not c.text_removed]
    assert len(main._rag.retrievable) == len(citable) == 38
    assert main._rag.draft_chunks == []          # كلها رُقّت
    assert len(main._rag.text_removed_chunks) == 6  # محجوزة بانتظار الترخيص


def test_prompt_never_contains_blocked_or_draft_ids(monkeypatch):
    """حتى لو طلبت المستخدمة موضوعًا تغطيه مسودة أو مقطع محجوز، لا يُرسل نصّهما."""
    from app.schemas import UserContext
    from app.services.prompt_builder import PromptBuilder

    builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
    query = "عندي ألم في الكتف مع حمل محتمل وضغط"
    sources = main._rag.retrieve(query, settings.rag_top_k)
    prompt = builder.build(
        mode="chat", user_context=UserContext(), findings=[],
        sources=sources, rules_glossary={}, current_date="2026-09-30",
        emergency_number="123", crisis_line="",
    )
    blocked = main._rag.text_removed_chunks + main._rag.draft_chunks
    assert blocked  # محجوز الترخيص موجود، لكنه خارج الاسترجاع دائمًا
    for chunk in blocked:
        assert chunk.id not in prompt, f"تسرّب مقطع محجوب {chunk.id} إلى البرومبت"
    for chunk in load_chunks([REAL_DRAFTS]):
        assert chunk.id not in prompt, f"تسرّبت مسودة {chunk.id} إلى البرومبت"


def test_health_reports_knowledge_gating():
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        body = client.get("/health").json()
    assert body["drafts_included"] is False
    assert body["drafts_pending_review"] == 0        # كلها رُقّت
    assert body["chunks_citable"] == (
        body["chunks_loaded"] - body["drafts_pending_review"]
        - body["chunks_text_removed"])               # 6 محجوزة بانتظار الترخيص
