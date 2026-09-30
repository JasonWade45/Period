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

def test_shipped_sources_have_full_provenance():
    """كل مقطع مُتحقَّق يجب أن يحمل مصدرًا وقسمًا ومراجعًا وتاريخ مراجعة."""
    for chunk in load_chunks([REAL_SOURCES]):
        assert chunk.status == VERIFIED
        assert chunk.source_name.strip()
        assert chunk.section.strip()
        assert chunk.reviewed_at
        assert chunk.reviewer.strip(), f"{chunk.id} بلا مراجع مُسجَّل"


def test_shipped_drafts_are_declared_drafts_with_origin():
    drafts = load_chunks([REAL_DRAFTS])
    assert drafts, "لا مسودات محمّلة"
    for chunk in drafts:
        assert chunk.status == DRAFT, f"{chunk.id} ليس draft"
        assert chunk.drafted_at, f"{chunk.id} بلا تاريخ كتابة"
        assert chunk.derived_from, f"{chunk.id} بلا مراجع أصلية"
        assert not chunk.reviewed_at, f"{chunk.id} يحمل reviewed_at وهو مسودة"
        assert "غير مُراجَعة" in chunk.source_name or "مسودة" in chunk.source_name


def test_draft_source_name_does_not_impersonate_a_body():
    """المسودة لا تُنسب إلى NHS/ACOG/NICE لأن ذلك إسناد زائف."""
    for chunk in load_chunks([REAL_DRAFTS]):
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


def test_drafts_load_only_when_explicitly_enabled():
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=True)
    # المسودات موجودة في الحالتين (للعدّ والمراجعة) لكن تُسترجع فقط عند التفعيل
    assert rag.draft_chunks
    hits = rag.retrieve("بطانة الرحم المهاجرة", top_k=20)
    assert any(c.status == DRAFT for c in hits), "التفعيل الصريح لم يُدرج المسودات"


def test_verified_chunks_are_still_retrievable():
    rag = KeywordRag(REAL_SOURCES, drafts_path=REAL_DRAFTS, include_drafts=False)
    assert rag.retrieve("طول الدورة الشهرية", top_k=5)


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

def test_production_rag_excludes_drafts():
    """الإعداد الافتراضي في التطبيق نفسه: بلا مسودات."""
    assert settings.knowledge_include_drafts is False
    assert len(main._rag.retrievable) == len(load_chunks([REAL_SOURCES]))
    assert len(main._rag.draft_chunks) > 0  # موجودة للمراجعة، محجوبة عن الاستشهاد


def test_prompt_never_contains_draft_ids(monkeypatch):
    """حتى لو طلبت المستخدمة موضوعًا تغطيه المسودة فقط، لا يُرسل نص المسودة."""
    from app.schemas import ChatRequest, UserContext
    from app.services.prompt_builder import PromptBuilder

    builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
    query = "عندي ألم في الكتف مع حمل محتمل وضغط"
    sources = main._rag.retrieve(query, settings.rag_top_k)
    prompt = builder.build(
        mode="chat", user_context=UserContext(), findings=[],
        sources=sources, rules_glossary={}, current_date="2026-09-30",
        emergency_number="123", crisis_line="",
    )
    for chunk in load_chunks([REAL_DRAFTS]):
        assert chunk.id not in prompt, f"تسرّبت مسودة {chunk.id} إلى البرومبت"


def test_health_reports_knowledge_gating():
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        body = client.get("/health").json()
    assert body["drafts_included"] is False
    assert body["drafts_pending_review"] > 0
    assert body["chunks_citable"] == body["chunks_loaded"] - body["drafts_pending_review"]
