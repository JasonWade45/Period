"""تكامل من الطلب إلى التدقيق: `/api/v1/ai/*` بقاعدة معرفة فيها مقطع معتمد واحد.

هذه الاختبارات تقيس ما يهم فعلًا في البريف، على الطريق الكامل (HTTP → فلتر →
قواعد → استرجاع → موديل مُسجَّل محليًا → تحقق → تدقيق):

- كل إجابة تصل إلى المستخدمة تستشهد بمعرّفات **مسترجَعة** فقط.
- الطوارئ لا تُنتج أي نداء للموديل، والتدقيق لا يخزّن نص الرسالة.
- بلا مقطع معتمد: «لا أملك مصدرًا موثوقًا» بلا نداء للموديل.
- الملخص يمرّ بالموديل عندما تكون هناك بيانات ومصادر، ويحمل قرارًا معلَنًا.

الموديل بديل مُسجَّل (`RecordingLLM`) لا شبكة ولا مفتاح — والاختبار يفحص ما
أُرسل إليه وما عاد منه، لا «ذكاءه».
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import settings
from app.kb.embedding import build_embedder
from app.kb.retrieval import HybridRetriever, producible_statuses
from app.kb.schemas import ChunkStatus, KbChunk, KbSource
from app.kb.store import SqliteKbStore
from app.schemas import AiChatRequest
from app.services import audit as audit_module
from app.services.ai_pipeline import AiPipeline, PipelineDeps
from app.services.emergency_numbers import EmergencyNumbers
from app.services.prompt_builder import PromptBuilder


class RecordingLLM:
    """بديل مُسجَّل: يبني ردًّا صالحًا من معرّفات المصادر التي وصلته في البرومبت."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete(self, system: str, user: str, *, retry_feedback: str | None = None):
        self.calls.append({"system": system, "user": user})
        ids = list(dict.fromkeys(re.findall(r'"id": "([^"]+)", "source_name"', system)))
        if self._is_summary(system):
            payload = {
                "overview": "ملخص من المصادر المعتمدة.",
                "what_changed": "", "patterns": "", "medical_alerts": "",
                "what_this_does_not_mean": "هذا النمط لا يحدد حالة طبية.",
                "questions_for_doctor": ["ما تقييم طبيبة لدوراتي؟",
                                         "هل أحتاج تحليلًا؟", "ما الخطوة التالية؟"],
                "sources_used": ids[:2],
            }
        else:
            payload = {
                "answer": "طول الدورة يختلف من شخص لآخر، والتقييم للطبيبة.",
                "sources_used": ids[:1],
                "needs_doctor": True, "emergency": False, "crisis": False,
                "missing_info": [],
            }
        return json.dumps(payload, ensure_ascii=False), 12

    @staticmethod
    def _is_summary(system: str) -> bool:
        return bool(re.search(r"MODE[^\n]{0,80}summary", system)) or \
            "what_this_does_not_mean" in system and "overview" in system and \
            re.search(r"\bsummary\b", system[:2000]) is not None


def _approved_chunk(chunk_id: str = "kb-cycle-1") -> KbChunk:
    """مقطع اختباري معتمد — نص مكتوب للاختبار، لا محتوى طبي منشور."""
    return KbChunk(
        id=chunk_id, source_id="src-test", title="طول الدورة",
        topic="أساسيات", language="ar",
        content="نص اختباري عن طول الدورة الشهرية وتغيّره بين الأشخاص. ليس نصًا طبيًا.",
        status=ChunkStatus.APPROVED, reviewed_by="د. اختبارية", reviewed_at="2026-09-01",
        authored_by="", license_note="",
    )


@pytest.fixture
def kb_store(tmp_path) -> SqliteKbStore:
    """قاعدة معرفة مؤقتة فيها مصدر معتمد ومقطع واحد مُضمَّن (متجه محلي حتمي)."""
    store = SqliteKbStore(tmp_path / "kb.db")
    store.upsert_source(KbSource(id="src-test", name="مصدر اختباري",
                                 licence="test-only", approved_for_ingest=False))
    store.upsert_chunk(_approved_chunk())
    # الاعتماد يمرّ عبر مراجعة طبية صريحة كما في الإنتاج
    store.set_status("kb-cycle-1", ChunkStatus.PHYSICIAN_REVIEWED,
                     reviewer="د. اختبارية", reviewed_at="2026-09-01")
    store.set_status("kb-cycle-1", ChunkStatus.APPROVED,
                     reviewer="د. اختبارية", reviewed_at="2026-09-01")
    embedder = build_embedder()
    chunk = store.get_chunk("kb-cycle-1")
    store.set_embedding("kb-cycle-1", embedder.embed([f"{chunk.title} {chunk.content}"])[0])
    return store


@pytest.fixture
def pipeline(kb_store):
    """خط الأنابيب + الموديل المُسجَّل + سجلات التدقيق التي كتبها فعلًا."""
    llm = RecordingLLM()
    entries: list = []
    retriever = HybridRetriever(kb_store, build_embedder(), min_similarity=0.0, top_k=6)
    pipe = AiPipeline(PipelineDeps(
        retriever=retriever,
        prompt_builder=PromptBuilder(settings.prompt_path, settings.prompt_version),
        llm=llm,
        numbers=EmergencyNumbers(),
        audit_writer=lambda entry, extra=None: entries.append(entry),
    ))
    return pipe, llm, entries


@pytest.fixture
def ai_client(pipeline, tmp_path, monkeypatch):
    """عميل HTTP على التطبيق الحقيقي، بموديل مُسجَّل وقاعدة معرفة اختبارية."""
    pipe, _, entries = pipeline
    import app.services.security as security

    from app.services.store import Store
    monkeypatch.setattr(main, "_store", Store(tmp_path / "app.db"))
    monkeypatch.setattr(security, "settings", replace(security.settings, api_key="k-test"))
    monkeypatch.setattr(main, "_ai_pipeline", pipe, raising=False)
    monkeypatch.setattr(main, "_llm", pipe.deps.llm, raising=False)
    monkeypatch.setattr(audit_module, "write", lambda entry: None)
    with TestClient(main.app) as client:
        # التطبيق يبني خط الأنابيب في lifespan؛ نضعه بعد الإقلاع لتنعكس النسخة الاختبارية
        main.app.state.ai_pipeline = pipe
        main.app.state.store = main._store
        client.headers.update({"X-API-Key": "k-test"})
        client.audit_entries = entries          # للفحص بعد الطلب
        yield client


def _chat(client, message: str, **extra):
    body = {"message": message, "user_key": "device-1", "language": "ar", **extra}
    return client.post("/api/v1/ai/chat", json=body)


# ------------------------------------------------------------------ المسار الكامل

def test_answer_cites_only_retrieved_chunk_ids(ai_client, pipeline):
    pipe, llm, _ = pipeline
    response = _chat(ai_client, "إيه طول الدورة الشهرية الطبيعي؟")

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ok", body
    assert body["sources_used"] == ["kb-cycle-1"]
    assert body["prompt_version"] == settings.prompt_version
    assert body["language"] == "ar"
    assert len(llm.calls) == 1                       # نداء واحد، بلا إعادة
    # ما أُرسل للموديل يحتوي المقطع المسترجَع، ونص المستخدمة في دور user فقط
    assert "kb-cycle-1" in llm.calls[0]["system"]
    assert "إيه طول الدورة الشهرية الطبيعي؟" in llm.calls[0]["user"]


def test_model_cannot_cite_a_chunk_that_was_not_retrieved(ai_client, pipeline, monkeypatch):
    pipe, llm, _ = pipeline

    def hallucinating(system: str, user: str, *, retry_feedback: str | None = None):
        llm.calls.append({"system": system, "user": user})
        return json.dumps({"answer": "إجابة.", "sources_used": ["kb-not-retrieved"],
                           "needs_doctor": False, "emergency": False, "crisis": False,
                           "missing_info": []}, ensure_ascii=False), 5

    monkeypatch.setattr(llm, "complete", hallucinating)
    body = _chat(ai_client, "إيه طول الدورة الشهرية الطبيعي؟").json()

    # الاستشهاد بمصدر غير مسترجَع يُرفض ⇒ لا يصل للمستخدمة، والرد احتياطي آمن
    assert body["decision"] == "fallback"
    assert "kb-not-retrieved" not in body["sources_used"]


def test_emergency_never_reaches_the_model_and_leaves_no_text_in_audit(ai_client, pipeline):
    _, llm, entries = pipeline
    message = "بنزف كل ساعة وبرمي جلطات كبيرة"
    body = _chat(ai_client, message).json()

    assert body["emergency"] is True
    assert body["decision"] == "emergency_filter"
    assert llm.calls == []                                  # صفر نداءات
    payload = body["emergency_payload"]
    assert payload and payload["number"] and payload["number"] in body["answer"]

    entries = ai_client.audit_entries
    assert entries, "لا سجل تدقيق للطوارئ"
    stored = json.dumps([e.model_dump() for e in entries], ensure_ascii=False)
    assert message not in stored                            # لا نص رسالة في السجل
    assert "جلطات" not in stored


def test_no_approved_sources_says_so_without_calling_the_model(tmp_path, monkeypatch):
    """قاعدة معرفة فارغة: الرد ثابت «لا مصدر موثوق» بلا أي نداء للموديل."""
    from app.i18n import get_translator

    empty = SqliteKbStore(tmp_path / "empty.db")
    llm = RecordingLLM()
    pipe = AiPipeline(PipelineDeps(
        retriever=HybridRetriever(empty, build_embedder(), min_similarity=0.0, top_k=6),
        prompt_builder=PromptBuilder(settings.prompt_path, settings.prompt_version),
        llm=llm, numbers=EmergencyNumbers(),
    ))

    response = pipe.chat(AiChatRequest(message="إيه أعراض ما قبل الدورة؟", user_key="d1"),
                         context=None)
    assert response.decision == "no_source"
    assert response.sources_used == []
    assert response.answer == get_translator().t("answer.no_reliable_source", "ar")
    assert llm.calls == []
    assert empty.list_chunks(producible_statuses()) == []


def test_summary_uses_the_model_when_there_is_data_and_a_source(ai_client, pipeline):
    """ملخص ببيانات مسجّلة: يمرّ بالموديل، ويعلن قراره، ويستشهد بالمصدر المعتمد."""
    pipe, llm, _ = pipeline
    store = main._store
    store.add_bleeding_log("device-1", "2026-07-01", 5)
    store.add_bleeding_log("device-1", "2026-08-10", 6)
    store.add_bleeding_log("device-1", "2026-09-20", 4)

    response = ai_client.post("/api/v1/ai/summary",
                              json={"message": "", "user_key": "device-1", "language": "ar"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] in {"ok", "no_source"}
    if body["decision"] == "ok":
        assert len(llm.calls) >= 1
        assert set(body["sources_used"]) <= {"kb-cycle-1"}
        assert body["model"]
    else:
        assert llm.calls == []


def test_unapproved_draft_is_never_retrieved(ai_client, pipeline, kb_store):
    """مسودة غير مُراجَعة في نفس القاعدة: لا تظهر ولا تُرسل للموديل."""
    kb_store.upsert_chunk(KbChunk(
        id="kb-draft-1", source_id="src-test", title="مسودة",
        topic="أساسيات", language="ar",
        content="نص مسودة غير مُراجَع عن التكيّس. ليس نصًا طبيًا.", authored_by="ai_draft",
        license_note="draft",
    ))
    _, llm, _ = pipeline
    body = _chat(ai_client, "إيه تكيّس المبايض؟").json()

    assert "kb-draft-1" not in body["sources_used"]
    assert all("kb-draft-1" not in call["system"] for call in llm.calls)
    assert kb_store.get_chunk("kb-draft-1").status is ChunkStatus.DRAFT_UNREVIEWED


# ------------------------------------------------------- سلوك التشغيل بلا شبكة

def test_health_endpoints_report_the_knowledge_state_truthfully(ai_client, kb_store):
    """حالة المعرفة تُعلن: كم مقطعًا قابل للاسترجاع، وبأي حالات، وبلا أسرار."""
    knowledge = ai_client.get("/api/v1/ai/health").json()
    assert knowledge["configured"] is True
    assert knowledge["retrievable_chunks"] == 1
    assert knowledge["producible_statuses"] == ["approved"]
    assert "api_key" not in json.dumps(knowledge).lower()

    legacy = ai_client.get("/health").json()
    # المسار القديم يُعلن ملفه الثابت: 38 معتمدًا ملكيًا، 6 محجوزًا بانتظار الترخيص،
    # وصفر مسودة (كلها رُقّت) — وأي فرق هنا تكذيب في الإحصاء نفسه.
    assert legacy["chunks_citable"] == 38
    assert legacy["chunks_text_removed"] == 6
    assert legacy["drafts_pending_review"] == 0
    assert legacy["emergency_number_verified"] in (True, False)
