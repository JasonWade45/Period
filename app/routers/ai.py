"""مسارات `/api/v1/ai/*` — خط أنابيب المحادثة الوحيد.

- الاسترجاع من قاعدة المعرفة (مقاطع معتمدة فقط + RRF).
- رد «لا أملك مصدرًا موثوقًا» عند فراغ الاسترجاع، بلا استدعاء للموديل.
- لغة الرد من الطلب/Accept-Language، ورسائل الأخطاء بأكواد مستقرة + نص مترجم.
- تخزين prompt_version وmodel وchunk_ids في سجل التدقيق (بلا نص الرسالة).

السلامة أولًا في الطبقتين: المصادقة وتحديد المعدّل يتجاوزان الرسائل التي
يفعّل فيها الفلتر — مستخدمة في خطر لا يُحجب عنها رقم الطوارئ بسبب مفتاح
خاطئ ولا بسبب حصة استهلاك.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import settings
from ..i18n import get_translator, resolve_locale
from ..schemas import AiChatRequest, AiChatResponse, AiSummaryResponse, UserContext
from ..services.ai_pipeline import AiPipeline, PipelineDeps, resolve_request_language
from ..services.auth import account_for_request
from ..services.emergency_filter import run_filter
from ..services.rules_engine import compute_all_findings
from ..services.security import check_api_key
from ..services.store import StoreError

log = logging.getLogger("cyclecare.ai.router")

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


def _pipeline(request: Request) -> AiPipeline:
    pipeline = getattr(request.app.state, "ai_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="AI pipeline is not configured")
    return pipeline


def _with_session_identity(request: Request, req: AiChatRequest) -> AiChatRequest:
    """كوكي الجلسة يتغلّب على `user_key` المُرسل في الطلب.

    حساب مُوثَّق يستخدم هويته في السياق والتدقيق وتحديد المعدّل مهما أرسل
    العميل (لا انتحال حساب آخر). بلا كوكي يبقى مفتاح الجهاز كما هو.
    """
    account = account_for_request(request)
    return req.model_copy(update={"user_key": account}) if account else req


def _effective_context(request: Request, req: AiChatRequest):
    """يضيف إحصاءات الدورات المسجّلة إلى السياق المُرسل من الواجهة.

    البيانات المسجّلة تسبق ما تدّعيه الواجهة: `user_context.cycle_gaps` القادم
    من العميل لا يُعتمد عليه في القواعد، بل يُحسب من السجلات.
    """
    store = getattr(request.app.state, "store", None)
    if store is None or not req.user_key:
        return req.user_context, []
    try:
        stats = store.cycle_stats(req.user_key)
        symptoms = store.list_symptom_logs(req.user_key, limit=200, since_days=180)
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


def _apply_profile(request: Request, req: AiChatRequest) -> AiChatRequest:
    """يملأ فراغات الطلب من ملف المستخدم المخزّن (لغة، بلد، سياق صحي).

    لا يتجاوز ما أرسله العميل صراحةً: الحقل المُرسل يبقى كما هو، والملف
    يأتي فقط لما هو غائب (لغة الرد، بلد رقم الطوارئ، عمر/وقاية/حالات).
    """
    store = getattr(request.app.state, "store", None)
    if store is None or not req.user_key:
        return req
    try:
        profile = store.get_health_profile(req.user_key)
    except StoreError:
        return req
    if not profile:
        return req

    overlay: dict[str, Any] = {}
    if req.country_code is None and profile.get("country_code"):
        overlay["country_code"] = profile["country_code"]
    if req.language is None and profile.get("locale"):
        overlay["language"] = profile["locale"]

    ctx = req.user_context
    ctx_updates: dict[str, Any] = {}
    if ctx.age is None and profile.get("age") is not None:
        ctx_updates["age"] = profile["age"]
    if ctx.contraception is None and profile.get("contraception"):
        ctx_updates["contraception"] = profile["contraception"]
    if ctx.pregnancy_status is None and profile.get("pregnancy_status"):
        ctx_updates["pregnancy_status"] = profile["pregnancy_status"]
    if not ctx.conditions and profile.get("conditions"):
        ctx_updates["conditions"] = profile["conditions"]
    if ctx_updates:
        overlay["user_context"] = UserContext.model_validate(
            {**ctx.model_dump(), **ctx_updates})
    return req.model_copy(update=overlay) if overlay else req


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


def _enforce_rate_limit(request: Request, req: AiChatRequest,
                        context: UserContext, symptoms: list[dict]) -> None:
    """حدّ المعدّل — بعد فلتر الطوارئ (رسالة + نتائج القواعد) عن قصد.

    نفس قاعدة المسار القديم: لا يُحجب طلبٌ تفعّل فيه الفلتر بسبب حصة
    الاستهلاك، والمكسب الأمني من الحدّ لا يستحق مخاطرة بسلامة مستخدمة.
    """
    try:
        findings = compute_all_findings(context, symptoms)
        if run_filter(req.message, findings).triggered:
            return
    except Exception:  # noqa: BLE001 — فشل المعاينة لا يُسقط الحد ولا الرد
        pass
    identity = req.user_key or "anonymous"
    limiter = getattr(request.app.state, "limiter", None)
    decision = limiter.check(identity) if limiter else None
    if decision is not None and not decision.allowed:
        exc = _error(request, "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS)
        exc.headers = {"Retry-After": str(decision.retry_after_seconds)}
        raise exc


@router.post("/chat", response_model=AiChatResponse)
def ai_chat(req: AiChatRequest, request: Request,
            x_api_key: Optional[str] = Header(default=None)) -> AiChatResponse:
    if not req.message.strip():
        raise _error(request, "empty_message", status.HTTP_422_UNPROCESSABLE_ENTITY)

    req = _with_session_identity(request, req)
    req = _apply_profile(request, req)
    _authenticate(request, req, x_api_key)

    context, symptoms = _effective_context(request, req)
    _enforce_rate_limit(request, req, context, symptoms)

    language = resolve_request_language(req, request.headers.get("accept-language"))
    if req.language is None:
        req = req.model_copy(update={"language": language})

    pipeline = _pipeline(request)
    return pipeline.chat(req, accept_language=request.headers.get("accept-language"),
                         context=context, symptoms=symptoms)


@router.post("/summary", response_model=AiSummaryResponse)
def ai_summary(req: AiChatRequest, request: Request,
               x_api_key: Optional[str] = Header(default=None)) -> AiSummaryResponse:
    """ملخّص الدورات المسجّلة. رسالة الملخص (إن جاءت) تمرّ بفلتر الطوارئ، ونتائج
    محرك القواعد تمرّ به أيضًا — ثم المصادقة والحدّ بمنطق واحد."""
    req = _with_session_identity(request, req)
    req = _apply_profile(request, req)
    _authenticate(request, req, x_api_key)

    context, symptoms = _effective_context(request, req)
    _enforce_rate_limit(request, req, context, symptoms)

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
    statuses = producible_statuses()
    try:
        store = pipeline.deps.retriever.store
        chunks = store.list_chunks(statuses)
    except AttributeError:
        # مُسترجع بلا مخزن (بديل اختباري): مُهيّأ لكن بلا عدّد مقاطع قابل للاستعلام
        return {"configured": True,
                "kb_backend": type(pipeline.deps.retriever).__name__,
                "retrievable": 0}
    except Exception as exc:  # noqa: BLE001 — فحص صحي لا يُسقط الخدمة
        return {"configured": True, "kb_error": type(exc).__name__, "retrievable": 0}
    return {
        "configured": True,
        "kb_backend": type(store).__name__,
        "retrievable_chunks": len(chunks),
        "producible_statuses": [s.value for s in statuses],
        "kb_allow_draft": settings.kb_allow_draft,
        "embedding_model": settings.kb_embedding_model,
        "embedding_backend": settings.kb_embedding_backend,
        "prompt_version": settings.prompt_version,
        "languages": settings.supported_locale_list,
    }
