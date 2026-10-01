"""مساعدات اختبار خط الأنابيب: مُسترجع بمصدر ثابت + بناة pipeline قابلة للتحكم.

لماذا مُسترجع بديل: اختبارات HTTP لا تهمّها جودة الاسترجاع بل عقود المسار
(الطوارئ، المصادقة، الحدّ، التدقيق). الاعتماد على قاعدة معرفة حقيقية يجعل
كل اختبار عبارة عن اختبار استرجاع أيضًا، ويكسر عند تغيّر بيانات الشحن.
اختبارات الاسترجاع نفسها تعيش في test_ai_integration / test_ai_pipeline
بمخزن SQLite مؤقت حقيقي.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.kb.retrieval import RetrievalResult
from app.kb.schemas import RetrievedChunk
from app.services.ai_pipeline import AiPipeline, PipelineDeps
from app.services.emergency_numbers import EmergencyNumbers
from app.services.prompt_builder import PromptBuilder


class StubRetriever:
    """مُسترجع ثابت: يُرجع المقاطع المُعطاة مهما كان الاستعلام (بلا شبكة)."""

    def __init__(self, chunks: list[RetrievedChunk] | None = None):
        self.chunks: list[RetrievedChunk] = list(chunks or [])

    def retrieve(self, query: str, *, language: str | None = None,
                 top_k: int | None = None) -> RetrievalResult:
        if not self.chunks:
            return RetrievalResult([], used_language=language or "",
                                   reason="لا مقاطع في الاختبار")
        return RetrievalResult(list(self.chunks), used_language=language or "")


def sample_chunks() -> list[RetrievedChunk]:
    """مقاطع اختبار بمعرّفات ثابتة (نفس أسماء fixtures المصادر القديمة)."""
    return [
        RetrievedChunk(
            id="test-cycle-length", source_id="test-source",
            title="قسم اختباري", topic="الدورة", language="ar",
            content="نص اختباري عن طول الدورة وأعراض ما قبل الدورة. ليس نصًا طبيًا.",
        ),
        RetrievedChunk(
            id="test-heavy-bleeding", source_id="test-source",
            title="قسم اختباري", topic="النزيف", language="ar",
            content="نص اختباري عن النزيف الغزير. ليس نصًا طبيًا.",
        ),
    ]


def load_rules_glossary() -> dict[str, str]:
    import json

    path = Path(settings.rules_glossary_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_pipeline(llm=None, *, chunks: list[RetrievedChunk] | None = None,
                   use_stubs: bool = True, audit_writer=None,
                   rules_glossary: dict | None = None) -> AiPipeline:
    """pipeline اختباري: بلا شبكة وبلا كتابة تدقيق إلا إذا مُنحت دالة صريحة.

    - `llm=None` (الافتراضي): مسار آمن بلا استدعاء مزوّد.
    - `use_stubs=False, chunks=None`: مُسترجع بديل فارغ (لاختبار «لا مصدر»).
    """
    retriever = StubRetriever(sample_chunks() if chunks is None and use_stubs else chunks)
    return AiPipeline(PipelineDeps(
        retriever=retriever,
        prompt_builder=PromptBuilder(settings.prompt_path, settings.prompt_version),
        llm=llm,
        numbers=EmergencyNumbers(),
        audit_writer=audit_writer,
        rules_glossary=rules_glossary if rules_glossary is not None
        else load_rules_glossary(),
    ))


def install_safe_pipeline(app_state) -> AiPipeline:
    """يضع فوق حالة التطبيق pipeline بلا مفتاح موديل وبلا تدقيق — للاختبار الآمن."""
    pipe = build_pipeline(llm=None, audit_writer=None)
    app_state.ai_pipeline = pipe
    return pipe
