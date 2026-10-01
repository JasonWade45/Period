"""مسارات `/api/v1/ai/*` — الطبقة الجديدة فوق قاعدة المعرفة.

الفرق عن `/v1/chat` القديم:
- الاسترجاع من قاعدة المعرفة (مقاطع معتمدة فقط + RRF) لا من ملف مصادر ثابت.
- رد «لا أملك مصدرًا موثوقًا» عند فراغ الاسترجاع، بلا استدعاء للموديل.
- لغة الرد من الطلب/Accept-Language، ورسائل الأخطاء بأكواد مستقرة + نص مترجم.
- تخزين prompt_version وmodel وchunk_ids في سجل التدقيق (بلا نص الرسالة).

المصادقة تُتحقق داخل كل مسار — لا كاعتماد عام — حتى لا يحجب خطأ مفتاح خاطئ
ردَّ طوارئ (نفس قاعدة `/v1/chat`: سلامة المستخدمة أولًا).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import settings
from ..i18n import get_translator, resolve_locale
from ..schemas import AiChatRequest, AiChatResponse, AiSummaryResponse
from ..services.ai_pipeline import AiPipeline, PipelineDeps, resolve_request_language
from ..services.emergency_filter import run_filter
from ..services.security import check_api_key
from ..services.store import StoreError

log = logging.getLogger("cyclecare.ai.router")

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


def _pipeline(request: Request) -> AiPipeline:
    pipeline = getattr(request.app.state, "ai_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="AI pipeline is not configured")
    return pipeline


def _effective_context(request: Request, req: AiChatRequest):
    """يضيف إحصاءات الدورات المسجّلة إلى السياق (كما في المسار القديم).

    البيانات المسجّلة تسبق ما تدّعيه الواجهة: `user_context.cycle_gaps` القادم
    من العميل لا يُعتمد عليه في القواعد، بل يُحسب من السجلات.
    """
    store = getattr(request.app.state, "store", None)
    if store is None or not req.user_key:
        return req.user_context, []
    try:
        stats = store.cycle_stats(req.user_key)
        symptoms = store.list_symptoms(req.user_key, limit=200, since_days=180)
    except StoreError:
        return req.user_context, []

    if not stats.get("cycles_recorded"):
        return req.user_context, symptoms

    from ..schemas import CycleStat, UserContext

    merged = UserContext(**{
        **req.user_context.model_dump(),
        "cycles_recorded": stats["cycles_recorded"],
        "avg_cycle_days": stats["avg_cycle_days"],
        "cycle_gaps": stats["cycle_gaps"],
        "last_cycles": [CycleStat(**c) for c in stats["last_cycles"]],
    })
    return merged, symptoms


def _with_profile_name(request: Request, req: AiChatRequest) -> AiChatRequest:
    """الاسم: ما أرسلته الواجهة، وإلا المحفوظ على الخادم لهذا الجهاز.

    إن وصل اسم جديد مع `user_key` حُفظ، فيعرفها المساعد في أي طلب لاحق (وفي أي
    واجهة أخرى بنفس المفتاح) دون أن تُعيد كتابته.
    """
    store = getattr(request.app.state, "store", None)
    if store is None or not req.user_key:
        return req
    try:
        if req.user_name:
            if store.get_profile(req.user_key).get("display_name") != req.user_name:
                store.set_profile(req.user_key, req.user_name)
            return req
        saved = store.get_profile(req.user_key).get("display_name", "")
    except StoreError:
        return req
    return req.model_copy(update={"user_name": saved}) if saved else req


def _error(request: Request, code: str, status_code: int) -> HTTPException:
    """خطأ بكود مستقر + نص مترجم من ملف الموارد (لا نص إنجليزي مكتوب هنا)."""
    locale = resolve_locale(request.headers.get("accept-language"))
    return HTTPException(status_code=status_code,
                         detail={"code": code,
                                 "message": get_translator().error(code, locale)})


def _authenticate(request: Request, req: AiChatRequest, x_api_key: Optional[str]) -> None:
    """المصادقة — إلا إن كان الفلتر سيتدخّل بالطوارئ، فسلامة المستخدمة أولًا."""
    try:
        preview = run_filter(req.message, [])
    except Exception:  # noqa: BLE001 — لا نُسقط الطلب بسبب معاينة الفلتر
        preview = None
    if preview is not None and preview.triggered:
        return
    try:
        check_api_key(x_api_key)
    except HTTPException as exc:
        code = "auth_missing_key" if exc.status_code == 401 else "auth_invalid_key"
        raise _error(request, code, exc.status_code) from exc


@router.post("/chat", response_model=AiChatResponse)
def ai_chat(req: AiChatRequest, request: Request,
            x_api_key: Optional[str] = Header(default=None)) -> AiChatResponse:
    if not req.message.strip():
        raise _error(request, "empty_message", status.HTTP_422_UNPROCESSABLE_ENTITY)

    _authenticate(request, req, x_api_key)

    # حدّ المعدّل: بعد الفلتر عن قصد (نفس قاعدة المسار القديم)
    identity = req.user_key or "anonymous"
    limiter = getattr(request.app.state, "limiter", None)
    decision = limiter.check(identity) if limiter else None
    if decision is not None and not decision.allowed:
        exc = _error(request, "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS)
        exc.headers = {"Retry-After": str(decision.retry_after_seconds)}
        raise exc

    context, symptoms = _effective_context(request, req)
    language = resolve_request_language(req, request.headers.get("accept-language"))
    if req.language is None:
        req = req.model_copy(update={"language": language})

    req = _with_profile_name(request, req)
    pipeline = _pipeline(request)
    return pipeline.chat(req, accept_language=request.headers.get("accept-language"),
                         context=context, symptoms=symptoms)


@router.post("/summary", response_model=AiSummaryResponse)
def ai_summary(req: AiChatRequest, request: Request,
               x_api_key: Optional[str] = Header(default=None)) -> AiSummaryResponse:
    """ملخّص الدورات المسجّلة. لا يمرّ بفلتر رسالة (لا رسالة)، لكن نتائج محرك
    القواعد تمرّ بالفلتر كما هي."""
    _authenticate(request, req, x_api_key)

    identity = req.user_key or "anonymous"
    limiter = getattr(request.app.state, "limiter", None)
    decision = limiter.check(identity) if limiter else None
    if decision is not None and not decision.allowed:
        exc = _error(request, "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS)
        exc.headers = {"Retry-After": str(decision.retry_after_seconds)}
        raise exc

    context, symptoms = _effective_context(request, req)
    req = _with_profile_name(request, req)
    pipeline = _pipeline(request)
    return pipeline.summary(req, accept_language=request.headers.get("accept-language"),
                            context=context, symptoms=symptoms)


@router.get("/health", response_model=dict)
def ai_health(request: Request) -> dict[str, Any]:
    """حالة الطبقة: قاعدة المعرفة، النموذج، عدد المقاطع القابلة للاسترجاع."""
    from ..kb.retrieval import producible_statuses

    pipeline: Optional[AiPipeline] = getattr(request.app.state, "ai_pipeline", None)
    if pipeline is None:
        return {"configured": False}
    store = pipeline.deps.retriever.store
    statuses = producible_statuses()
    try:
        chunks = store.list_chunks(statuses)
    except Exception as exc:  # noqa: BLE001 — فحص صحي لا يُسقط الخدمة
        return {"configured": True, "kb_error": type(exc).__name__, "retrievable": 0}
    return {
        "configured": True,
        "kb_backend": type(store).__name__,
        "retrievable_chunks": len(chunks),
        "producible_statuses": [s.value for s in statuses],
        "kb_allow_draft": settings.kb_allow_draft,
        "knowledge_mode": "reviewed_and_unreviewed" if settings.kb_allow_draft else "reviewed_only",
        "unreviewed_chunks": sum(1 for c in chunks if c.status.value != "approved"),
        "llm_configured": pipeline.deps.llm is not None,
        "extractive_fallback": pipeline.deps.extractive_fallback,
        "embedding_model": settings.kb_embedding_model,
        "embedding_backend": settings.kb_embedding_backend,
        "prompt_version": settings.prompt_version,
        "languages": settings.supported_locale_list,
    }
