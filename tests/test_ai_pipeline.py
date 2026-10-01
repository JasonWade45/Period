"""اختبار تكامل خط أنابيب `/api/v1/ai` بموديل مُحاكى (بلا شبكة).

يغطّي معايير القبول:
- صفر مقاطع معتمدة + KB_ALLOW_DRAFT=false ⇒ «لا أملك مصدرًا موثوقًا» بلا موديل.
- الطوارئ/الأزمة لا تصل إلى الموديل أبدًا.
- كل إجابة تحمل sources_used ⊆ المقاطع المسترجَعة.
- الرد بالعربية لسؤال عربي، وبالإنجليزية لسؤال إنجليزي.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.eval.run import RecordingLLM
from app.i18n import get_translator
from app.kb.embedding import DeterministicLocalEmbedder
from app.kb.retrieval import HybridRetriever
from app.kb.schemas import ChunkStatus, KbChunk
from app.kb.store import SqliteKbStore
from app.schemas import AiChatRequest, UserContext
from app.services.ai_pipeline import AiPipeline, PipelineDeps
from app.services.emergency_numbers import EmergencyNumbers
from app.services.prompt_builder import PromptBuilder


def _chunk(chunk_id: str, content: str, language: str = "ar") -> KbChunk:
    return KbChunk(id=chunk_id, source_id="src-1", title="طول الدورة", topic="cycle",
                   language=language, content=content, status=ChunkStatus.DRAFT_UNREVIEWED)


def _approve(store: SqliteKbStore, chunk_id: str) -> None:
    store.set_status(chunk_id, ChunkStatus.PHYSICIAN_REVIEWED, "د. فلانة", "2026-10-01")
    store.set_status(chunk_id, ChunkStatus.APPROVED, "د. فلانة", "2026-10-02")


@pytest.fixture
def kb_store(tmp_path) -> SqliteKbStore:
    return SqliteKbStore(tmp_path / "kb.db")


def _pipeline(store: SqliteKbStore, llm=None, *, allow_draft: bool = False) -> AiPipeline:
    return AiPipeline(PipelineDeps(
        retriever=HybridRetriever(store, DeterministicLocalEmbedder(dim=64),
                                  allow_draft=allow_draft, min_similarity=0.0,
                                  min_keyword_score=0.0),
        prompt_builder=PromptBuilder(settings.prompt_path, settings.prompt_version),
        llm=llm if llm is not None else RecordingLLM(),
        numbers=EmergencyNumbers(),
    ))


def _request(message: str, language: str = "ar") -> AiChatRequest:
    return AiChatRequest(message=message, user_context=UserContext(), language=language,
                         user_key="test-user")


# ============================================================ معيار: بلا مصادر

def test_no_approved_chunks_returns_no_source_answer(kb_store):
    kb_store.upsert_chunk(_chunk("kb-1", "طول الدورة من 21 إلى 35 يومًا"))
    llm = RecordingLLM()
    pipeline = _pipeline(kb_store, llm, allow_draft=False)

    response = pipeline.chat(_request("إيه طول الدورة الطبيعي؟"))

    assert response.decision == "no_source"
    assert response.sources_used == []
    assert response.retrieval.kind == "no_source"
    assert "مصدرًا موثوقًا" in response.answer
    assert llm.calls == []                    # الموديل لم يُستدعَ إطلاقًا


def test_no_source_answer_is_english_for_english_request(kb_store):
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("What is a normal cycle length?", language="en"))
    assert response.language == "en"
    assert "reliable source" in response.answer


# ============================================================ معيار: الطوارئ

@pytest.mark.parametrize("message", [
    "عندي نزيف غزير وبغير فوطه كل ساعه",
    "بنزف كتير وطلعت جلطات كبيره",
    "حامل وحصل عندي نزيف",
    "حامل وكتفي بيوجعني جامد",
    "دوخه شديده ومغشي عليا",
    "الم شديد ومفاجي في بطني",
    "I am soaking through a pad an hour",
])
def test_medical_emergency_never_reaches_llm(kb_store, message):
    _seed_approved(kb_store)
    llm = RecordingLLM()
    pipeline = _pipeline(kb_store, llm)

    response = pipeline.chat(_request(message))

    assert response.emergency is True
    assert response.decision == "emergency_filter"
    assert response.sources_used == []
    assert llm.calls == []
    assert response.emergency_payload is not None
    assert response.emergency_payload.number
    assert response.emergency_payload.number in response.answer


@pytest.mark.parametrize("message", [
    "مش عايزه اعيش خلاص",
    "بفكر اموت",
    "عايزه اذي نفسي",
    "I want to end my life",
])
def test_crisis_never_reaches_llm(kb_store, message):
    llm = RecordingLLM()
    pipeline = _pipeline(kb_store, llm)

    response = pipeline.chat(_request(message))

    assert response.crisis is True
    assert llm.calls == []
    assert "شخص تثق" in response.answer or "someone you trust" in response.answer


def test_emergency_does_not_require_augmentation_from_retrieved_text(kb_store):
    """نص مسترجَع لا يغيّر قرار الطوارئ ولا يستبدل الرد الثابت."""
    _seed_approved(kb_store)
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("نزيف غزير جدًا ولا يتوقف"))
    assert response.emergency is True
    assert response.sources_used == []
    assert not any(c.startswith("kb-") for c in response.sources_used)


# ============================================================ معيار: المصادر

def _seed_approved(store: SqliteKbStore) -> None:
    store.upsert_chunk(_chunk("kb-cycle", "طول الدورة الشهرية من 21 إلى 35 يومًا عادةً"))
    store.upsert_chunk(_chunk("kb-bleed", "يستمر النزيف عادةً من 3 إلى 7 أيام"))
    _approve(store, "kb-cycle")
    _approve(store, "kb-bleed")


def test_answer_uses_only_retrieved_chunk_ids(kb_store):
    _seed_approved(kb_store)
    llm = RecordingLLM()
    pipeline = _pipeline(kb_store, llm)

    response = pipeline.chat(_request("إيه طول الدورة الشهرية الطبيعي؟"))

    assert response.decision == "ok"
    assert response.sources_used
    assert set(response.sources_used) <= {"kb-cycle", "kb-bleed"}
    assert llm.calls, "الموديل يجب أن يُستدعى عند وجود مصدر معتمد"


def test_draft_chunks_are_not_retrievable_in_production(kb_store):
    kb_store.upsert_chunk(_chunk("kb-draft", "طول الدورة من 21 إلى 35 يومًا"))
    pipeline = _pipeline(kb_store, RecordingLLM(), allow_draft=False)
    response = pipeline.chat(_request("إيه طول الدورة؟"))
    assert response.decision == "no_source"
    assert "kb-draft" not in response.sources_used


def test_invalid_model_output_falls_back_safely(kb_store):
    _seed_approved(kb_store)
    pipeline = _pipeline(kb_store, RecordingLLM(invalid_json=True))

    response = pipeline.chat(_request("إيه طول الدورة الشهرية؟"))

    assert response.decision == "fallback"
    assert response.sources_used == []
    assert response.answer
    assert "مصدرًا موثوقًا" not in response.answer       # ليست رسالة انعدام المصدر


def test_model_citation_outside_retrieved_ids_is_rejected(kb_store):
    """موديل يستشهد بمقطع غير مُسترجَع ⇒ رفض وإعادة ثم احتياطي (لا إسناد كاذب)."""
    _seed_approved(kb_store)

    class LyingLLM(RecordingLLM):
        def complete(self, system, user, *, retry_feedback=None):
            self.calls.append({"system": system, "user": user})
            return json.dumps({
                "answer": "نص", "sources_used": ["kb-not-retrieved"],
                "needs_doctor": False, "emergency": False, "crisis": False,
                "missing_info": [],
            }, ensure_ascii=False), 1

    llm = LyingLLM()
    pipeline = _pipeline(kb_store, llm)
    response = pipeline.chat(_request("إيه طول الدورة الشهرية؟"))

    assert response.decision == "fallback"
    assert response.sources_used == []
    assert len(llm.calls) == 2          # المحاولة الأصلية + إعادة واحدة


def test_emergency_declared_by_model_gets_number_in_text(kb_store):
    """إن أعلن الموديل طوارئ لم يلتقطها الفلتر، يظهر الرقم والتعليمات في النص."""
    _seed_approved(kb_store)

    class EmergencyLLM(RecordingLLM):
        def complete(self, system, user, *, retry_feedback=None):
            self.calls.append({"system": system, "user": user})
            ids = [line for line in system.split('"id": "')[1:]]
            cited = [line.split('"')[0] for line in ids][:1]
            return json.dumps({
                "answer": "حالتك تبدو حرجة.", "sources_used": cited,
                "needs_doctor": True, "emergency": True, "crisis": False,
                "missing_info": [],
            }, ensure_ascii=False), 1

    pipeline = _pipeline(kb_store, EmergencyLLM())
    response = pipeline.chat(_request("إيه طول الدورة الشهرية؟"))

    assert response.emergency is True
    assert response.emergency_payload is not None
    assert response.emergency_payload.number in response.answer
    assert response.answer.count("الإسعاف") >= 1


# ================================================================ الترجمة/اللغة

def test_response_language_follows_request_not_message_script(kb_store):
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("ما هو طول الدورة؟", language="en"))
    assert response.language == "en"


def test_fixed_answers_come_from_resource_files(kb_store):
    """الردود الثابتة من locales/ar.json لا من نص في الكود."""
    t = get_translator()
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("سؤال بلا مصدر"))
    assert response.answer == t.t("answer.no_reliable_source", "ar")


def test_accept_language_header_is_used_when_request_has_no_language(kb_store):
    pipeline = _pipeline(kb_store, RecordingLLM())
    req = AiChatRequest(message="What is a normal cycle?", user_context=UserContext())
    response = pipeline.chat(req, accept_language="en-GB,en;q=0.9")
    assert response.language == "en"


def test_arabic_indic_digits_are_normalised_before_emergency_filter(kb_store):
    """«٩٠ يوم» و«90 يوم» يجب أن يتصرفا بنفس الطريقة (تطبيع الأرقام موحّد)."""
    from app.services.arabic import normalize_digits
    assert normalize_digits("٩٠") == "90"

    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("الدورة متأخرة ٩٠ يوم"))
    assert response.language == "ar"


# =================================================================== الملخّص

def test_summary_without_sources_returns_safe_summary(kb_store):
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.summary(_request(""))
    assert response.decision == "no_source"
    assert response.overview
    assert response.what_this_does_not_mean
    assert 1 <= len(response.questions_for_doctor) <= 5
    assert response.sources_used == []


def test_summary_with_source_returns_model_fields(kb_store):
    _seed_approved(kb_store)
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.summary(_request(""))
    assert response.decision == "ok"
    assert response.overview
    assert response.what_this_does_not_mean
    assert 3 <= len(response.questions_for_doctor) <= 5
    assert set(response.sources_used) <= {"kb-cycle", "kb-bleed"}


# ============================================================== سجل التدقيق

def test_audit_entry_has_no_message_text_and_keeps_chunk_ids(kb_store):
    _seed_approved(kb_store)
    captured: list[tuple] = []
    pipeline = _pipeline(kb_store, RecordingLLM())
    pipeline.deps.audit_writer = lambda entry, extra: captured.append((entry, extra))

    secret = "عندي سؤال خاص جدًا عن النزيف واسمي فلانة"
    pipeline.chat(_request(f"إيه طول الدورة الشهرية؟ {secret}"))

    assert captured, "التدقيق يجب أن يُكتب"
    entry, extra = captured[0]
    assert secret not in json.dumps(entry.model_dump(), ensure_ascii=False)
    assert extra["model"] == getattr(settings, "groq_model", "")
    assert extra["decision"] == "ok"
    assert isinstance(extra["message_len"], int)


def test_audit_for_emergency_stores_no_excerpt(kb_store):
    captured: list[tuple] = []
    pipeline = _pipeline(kb_store, RecordingLLM())
    pipeline.deps.audit_writer = lambda entry, extra: captured.append((entry, extra))

    pipeline.chat(_request("بنزف كتير واسمي فلانة محمد"))

    entry, extra = captured[0]
    # الحقل أُزيل من المخطط أصلًا: لا يوجد مكان لنص رسالة أن يُخزَّن فيه
    assert "request_excerpt" not in entry.model_dump()
    assert extra["decision"] == "emergency_filter"
    assert "فلانة" not in json.dumps(entry.model_dump(), ensure_ascii=False)


# ==================================================== سطر الاستشارة الإلزامي

def test_normal_answer_is_sealed_with_referral_notice(kb_store):
    """قرار المالك: كل إجابة عادية مختومة بسطر الاستشارة — فرض الخادم لا حسن ظن الموديل."""
    from app.services.referral import ensure_referral_notice  # noqa: F401 — النص من locales

    _seed_approved(kb_store)
    pipeline = _pipeline(kb_store, RecordingLLM())
    response = pipeline.chat(_request("إيه طول الدورة الشهرية الطبيعي؟"))

    assert response.decision == "ok"
    notice = get_translator().t("answer.referral_notice", "ar")
    assert response.answer.endswith(notice)


def test_referral_notice_is_appended_exactly_once():
    from app.services.referral import ensure_referral_notice

    notice = get_translator().t("answer.referral_notice", "ar")
    once = ensure_referral_notice("الإجابة باختصار.", "ar")
    assert once.endswith(notice)
    assert ensure_referral_notice(once, "ar") == once        # لا تكرار
    assert ensure_referral_notice("", "ar") == ""            # بلا نص لا شيء يُختم

    en_notice = get_translator().t("answer.referral_notice", "en")
    assert ensure_referral_notice("Short answer.", "en").endswith(en_notice)


def test_emergency_answers_are_excluded_from_the_referral_seal(kb_store):
    """رقم الطوارئ يسبق كل شيء: رد الطوارئ لا يُختم بسطر الاستشارة."""
    _seed_approved(kb_store)
    response = _pipeline(kb_store, RecordingLLM()).chat(
        _request("عندي نزيف غزير وبغير فوطه كل ساعه"))

    assert response.emergency is True
    notice = get_translator().t("answer.referral_notice", "ar")
    assert notice not in response.answer
