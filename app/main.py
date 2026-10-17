from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import (
    AuditEntry,
    ChatRequest,
    ChatResponse,
    ConditionStatus,
    ConsentUpdateRequest,
    CycleIn,
    CycleOut,
    CycleStat,
    DailyLogPayload,
    EmergencyPayload,
    Finding,
    InsightsResponse,
    LoginRequest,
    PartnerShareCreateRequest,
    PinSecurityRequest,
    PredictionCorrectionRequest,
    ProfilePayload,
    RefreshRequest,
    RegisterRequest,
    Severity,
    SummaryResponse,
    SymptomIn,
    SymptomOut,
    UserContext,
    VerifyPinRequest,
)
from .routers.ai import router as ai_router
from .services import audit
from .services.ai_pipeline import AiPipeline, PipelineDeps
from .services.auth import AuthError, TokenClaims, verify_access_token
from .services.emergency_filter import build_fixed_reply, ensure_emergency_text, run_filter
from .services.emergency_numbers import EmergencyNumbers
from .services.export_ar import (
    ExportContext,
    FontNotFound,
    build_doctor_report_sections,
    build_report_pdf,
    cycles_to_csv,
    report_sections_from_cycles,
    symptoms_to_csv,
)
from .services.library import filter_approved_articles
from .services.llm import LLMClient
from .services.prediction import predict_next_cycle_window
from .services.prompt_builder import PromptBuilder, scrub_conversation_history
from .services.rag import KeywordRag
from .services.rules_engine import compute_all_findings, max_severity
from .services.security import api_key_header, key_fingerprint, limiter
from .services.store import Store, StoreError
from .services.validator import validate

log = logging.getLogger("cyclecare")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """تحقّق تشغيلي عند الإقلاع — أخطاء الإعداد تظهر في اللوج لا في وجه المستخدمة."""
    if _llm is None:
        log.warning(
            "GROQ_API_KEY غير مضبوط: الطلبات غير الطارئة ستُعاد إليها إجابة احتياطية "
            "حتى يُضبط المفتاح في البيئة أو في ملف .env"
        )
    info = _emergency_info(settings.country_code, _emergency_override(), settings.crisis_line)
    if not info.verified:
        log.warning(
            "رقم الطوارئ %s غير مُتحقق منه للبلد %s — يُذكر في الرد مع تنبيه صريح.",
            info.number, settings.country_code or "غير محدّد",
        )
    elif settings.emergency_number_is_default:
        log.info("EMERGENCY_NUMBER=%s (مُتحقق منه لبلد %s).", info.number, info.country_code)
    if not info.crisis_line:
        log.warning("لا يوجد خط دعم نفسي مُتحقق منه (CRISIS_LINE أو جدول البلد).")
    if not settings.api_key:
        log.warning(
            "API_KEY غير مضبوط: كل مسارات /v1 مفتوحة. مقبول للتطوير المحلي فقط."
        )
    if settings.knowledge_include_drafts and _rag.draft_chunks:
        log.warning(
            "KNOWLEDGE_INCLUDE_DRAFTS مفعّل: %d مقطعًا غير مُراجَع طبيًا سيُرسل للموديل "
            "ويصبح قابلًا للاستشهاد. للاختبار الداخلي فقط — لا تشغّليه في الإنتاج.",
            len(_rag.draft_chunks),
        )
    if not settings.cors_origins:
        log.info("CORS: نفس الأصل فقط (الوضع الافتراضي).")
    app.state.store = _store
    app.state.limiter = limiter
    app.state.ai_pipeline = _ai_pipeline
    if _ai_pipeline is not None:
        from .kb.retrieval import producible_statuses
        log.info("طبقة AI جاهزة (/api/v1/ai) — حالات قابلة للاسترجاع: %s",
                 [s.value for s in producible_statuses()])
    else:
        log.error("طبقة AI غير مُهيّأة: /api/v1/ai/* ستُرجع 503 حتى يُصلح السبب أعلاه.")
    log.info(
        "CycleCare جاهز — prompt=%s model=%s citable=%d drafts=%d db=%s rate_limit=%d/%ds",
        settings.prompt_version, settings.groq_model, len(_rag.retrievable),
        len(_rag.draft_chunks), settings.db_path,
        settings.rate_limit_requests, settings.rate_limit_window_seconds,
    )
    yield


app = FastAPI(title="CycleCare AI", version=settings.prompt_version, lifespan=lifespan)

try:
    _llm = LLMClient()
except RuntimeError:
    _llm = None  # وضع الاختبار بدون مفتاح

_prompt_builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
_rag = KeywordRag(
    settings.sources_path,
    drafts_path=settings.draft_sources_path,
    include_drafts=settings.knowledge_include_drafts,
)
_rules_glossary: dict[str, str] = json.loads(
    settings.rules_glossary_path.read_text(encoding="utf-8")
)
_store = Store(settings.db_path)


def _build_ai_pipeline() -> AiPipeline | None:
    """بناء طبقة /api/v1/ai: مخزن قاعدة المعرفة + مُضمِّن + مُسترجع.

    لا نُسقط التطبيق إن تعذّر بناء النموذج المحلي: المسار الجديد يصبح غير
    مُهيّأ (503) والمسارات القديمة تعمل. سبب الفشل يُسجَّل صراحة لأن أشهر
    سببين — نموذج تضمين غير مُنزَّل أو absent psycopg — يحتاجان تدخّلًا.
    """
    from .kb.embedding import build_embedder
    from .kb.postgres import build_store
    from .kb.retrieval import HybridRetriever

    try:
        kb_store = build_store()
    except Exception as exc:  # noqa: BLE001
        log.error("تعذّر بناء مخزن قاعدة المعرفة: %s: %s", type(exc).__name__, exc)
        return None
    try:
        embedder = build_embedder()
    except Exception as exc:  # noqa: BLE001
        log.error("تعذّر تحميل نموذج التضمين (%s): %s: %s",
                  settings.kb_embedding_backend, type(exc).__name__, exc)
        embedder = None

    if embedder is None:
        from .kb.embedding import DeterministicLocalEmbedder
        log.warning("الاسترجاع المتجهي معطّل: بحث كلمي فقط حتى يُضبط نموذج التضمين.")
        embedder = DeterministicLocalEmbedder()

    deps = PipelineDeps(
        retriever=HybridRetriever(kb_store, embedder),
        prompt_builder=_prompt_builder,
        llm=_llm,
        numbers=_emergency_numbers,
        store=_store,
        audit_writer=audit.write,
    )
    return AiPipeline(deps)


_emergency_numbers = EmergencyNumbers()

FALLBACK_ANSWER = (
    "عذراً، تعذّر توليد إجابة آمنة في هذه اللحظة. "
    "حاولي مرة أخرى بعد قليل، وإذا كان هناك عرض مقلق فالتوجّه لطبيبة هو الخطوة الأنسب."
)

# حالة إعداد دائم وليست عطلًا عارضًا: لا نطلب منها إعادة المحاولة بلا فائدة.
NOT_CONFIGURED_ANSWER = (
    "المساعد الذكي غير متاح حاليًا، فلا أستطيع الإجابة عن هذا السؤال الآن. "
    "إن كان هناك عرض مقلق، فالتوجّه لطبيبة هو الخطوة الأنسب."
)


def _emergency_override() -> str:
    """EMERGENCY_NUMBER الصريح فقط يتقدّم على جدول البلد.

    القيمة الافتراضية في الكود لا تُعدّ «ضبطًا صريحًا»، وإلا لتقدّمت دائمًا على
    جدول البلد وعادت كل الطلبات رقم بلد واحد — وهو ما يجعل اختيار البلد بلا أثر.
    """
    return "" if settings.emergency_number_is_default else settings.emergency_number


def _emergency_info(country_code: str | None, number_override: str = "",
                    crisis_override: str = ""):
    """رقم الطوارئ الفعّال: ضبط صريح > جدول البلد > رقم عام مع تنبيه."""
    return _emergency_numbers.lookup(
        country_code or settings.country_code,
        override_number=number_override,
        override_crisis_line=crisis_override,
    )


def _write_audit(entry: AuditEntry) -> None:
    """سجل التدقيق مهم، لكن فشل كتابته (قرص ممتلئ/نظام للقراءة فقط) لا يُسقط ردًّا آمنًا."""
    try:
        audit.write(entry)
    except Exception as exc:  # noqa: BLE001
        log.error("تعذّر كتابة سجل التدقيق: %s: %s", type(exc).__name__, exc)


def _extract_bearer_claims(request: Request | None) -> TokenClaims | None:
    """يقرأ ويتحقق من X-User-Token أو Authorization: Bearer <token> إن وُجد."""
    if request is None:
        return None
    token = (request.headers.get("x-user-token") or "").strip()
    if not token:
        auth_header = (request.headers.get("authorization") or "").strip()
        if not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header[7:].strip()
    if not token:
        return None
    try:
        return verify_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _resolve_user_key(user_key: str | None = None, request: Request | None = None) -> str:
    """يحدد معرّف التخزين الفعلي: حساب موثّق بـ Bearer token أو معرّف جهاز محلي."""
    claims = _extract_bearer_claims(request)
    if claims is not None:
        return claims.user_key
    clean = (user_key or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="user_key or Bearer token is required")
    return clean


def _effective_context(req: ChatRequest) -> tuple[UserContext, list[dict], dict]:
    """يدمج سياق الطلب مع البيانات المسجّلة فعلًا والملف الشخصي المحفوظ.

    البيانات المسجّلة في قاعدة البيانات هي المصدر الأدق للأرقام (عدد الدورات،
    متوسط الطول، آخر الدورات)، لأنها محسوبة من تواريخ حقيقية لا من إدخال يدوي.
    تبقى حقول الطلب كما أرسلتها الواجهة، أو تُستكمل من الملف الشخصي المحفوظ.
    """
    stats: dict[str, Any] = {}
    symptoms: list[dict] = []
    profile: dict[str, Any] = {}
    if req.user_key:
        stats = _store.cycle_stats(req.user_key)
        symptoms = _store.list_symptoms(req.user_key, limit=200, since_days=180)
        profile = _store.get_profile(req.user_key)

    base = req.user_context.model_dump()
    if base.get("age") is None and profile.get("age") is not None:
        base["age"] = profile["age"]
    if not base.get("contraception") and profile.get("contraception"):
        base["contraception"] = profile["contraception"]
    if not base.get("pregnancy_status") and profile.get("pregnancy_status"):
        base["pregnancy_status"] = profile["pregnancy_status"]
    if not base.get("conditions") and profile.get("conditions"):
        base["conditions"] = [ConditionStatus(**c) if isinstance(c, dict) else c
                              for c in profile["conditions"]]

    if stats.get("cycles_recorded"):
        base.update({
            "cycles_recorded": stats["cycles_recorded"],
            "avg_cycle_days": stats["avg_cycle_days"],
            "cycle_gaps": stats["cycle_gaps"],
            "last_cycles": [CycleStat(**c) for c in stats["last_cycles"]],
        })

    merged = UserContext(**base)
    return merged, symptoms, stats


_IDENTITY_AND_GREETING_PATTERNS_AR = (
    "انت مين",
    "انتي مين",
    "من انت",
    "من انتي",
    "مين انت",
    "مين انتي",
    "مين معايا",
    "مين معي",
    "عرفيني بنفسك",
    "عرفني بنفسك",
    "بتعملي ايه",
    "بتعمل ايه",
    "تقدري تساعديني",
    "تقدر تساعدني",
    "ايه هو التطبيق",
    "ما هو التطبيق",
    "ما هي وظيفتك",
    "مرحبا",
    "اهلا",
    "السلام عليكم",
    "صباح الخير",
    "مساء الخير",
    "ازيك",
    "عامله ايه",
    "كيف حالك",
)

_IDENTITY_AND_GREETING_PATTERNS_EN = (
    "who are you",
    "what are you",
    "what can you do",
    "how can you help",
    "hello",
    "hi there",
)


def _check_greeting_or_identity_reply(message: str, locale: str) -> str | None:
    """يردّ بتعريف ودود وآمن بالمساعد التثقيفي عندما تسأل المستخدمة «أنتِ مين؟» أو تلقي التحية."""
    from .services.arabic import normalize_arabic
    norm = normalize_arabic(message or "").strip()
    raw_low = (message or "").strip().lower()
    if not norm:
        return None
    if len(norm.split()) <= 7:
        if any(pat in norm for pat in _IDENTITY_AND_GREETING_PATTERNS_AR) or raw_low in ("hi", "hey", "hello", "هاي", "هلا"):
            if locale == "en":
                return (
                    "Hello! I am the safe educational assistant in **CycleCare (دورتي)** 🌸\n\n"
                    "Here is how I can help you:\n"
                    "• Explain menstrual cycle phases, ovulation, and common symptoms using verified medical sources (ACOG, WHO, NHS).\n"
                    "• Summarize your logged cycles and symptoms and suggest questions for your doctor.\n"
                    "• I provide **educational information only** — never a medical diagnosis, prescription, or medication dosage.\n\n"
                    "Try asking me: *\"What is a normal cycle length?\"* or *\"Why can a period be late?\"*"
                )
            return (
                "أهلاً بكِ! أنا **المساعد التثقيفي الآمن في منصّة CycleCare (دورتي)** 🌸\n\n"
                "مهمّتي مساعدتكِ في:\n"
                "• شرح مراحل الدورة الشهرية والإباضة والأعراض الشائعة بلغة عربية واضحة ومبسّطة.\n"
                "• قراءة أنماط دورتكِ وأعراضكِ المسجّلة وتجهيز ملخص وأسئلة مقترحة لزيارة الطبيبة.\n"
                "• الإجابة بالاعتماد على مراجع طبية معتمدة فقط (مثل ACOG وWHO وNHS) — **دون تقديم تشخيص طبي أو وصف أدوية أو جرعات**.\n\n"
                "يمكنكِ سؤالي مثلاً: «إيه طول الدورة الطبيعية؟» أو «ليه الدورة بتتأخر؟» أو «إمتى ألم الدورة يستدعي طبيبة؟»."
            )
        if any(pat in raw_low for pat in _IDENTITY_AND_GREETING_PATTERNS_EN):
            return (
                "Hello! I am the safe educational assistant in **CycleCare (دورتي)** 🌸\n\n"
                "Here is how I can help you:\n"
                "• Explain menstrual cycle phases, ovulation, and common symptoms using verified medical sources (ACOG, WHO, NHS).\n"
                "• Summarize your logged cycles and symptoms and suggest questions for your doctor.\n"
                "• I provide **educational information only** — never a medical diagnosis, prescription, or medication dosage.\n\n"
                "Try asking me: *\"What is a normal cycle length?\"* or *\"Why can a period be late?\"*"
            )
    return None


def _run_pipeline(req: ChatRequest) -> ChatResponse | SummaryResponse:
    started = time.monotonic()
    ctx, symptoms, _stats = _effective_context(req)
    findings: list[Finding] = compute_all_findings(ctx, symptoms)
    emergency = _emergency_info(req.country_code, _emergency_override(), settings.crisis_line)

    # 1) فلتر ما قبل الموديل: رسالة المستخدمة + نتائج القواعد
    filt = run_filter(req.message, findings)
    if filt.triggered:
        reply = build_fixed_reply(filt, emergency)
        crisis = filt.kind == "crisis"
        payload = EmergencyPayload(
            kind="crisis" if crisis else "medical",
            number=emergency.number,
            number_verified=emergency.verified,
            country_code=emergency.country_code,
            crisis_line=emergency.crisis_line if crisis else "",
            instruction=reply.split("\n")[0],
        )
        response = ChatResponse(
            answer=reply,
            sources_used=[],
            needs_doctor=True,
            emergency=True,
            crisis=crisis,
            missing_info=[],
            prompt_version=settings.prompt_version,
            rule_codes=[f.rule_code for f in findings],
            emergency_payload=payload,
        )
        _write_audit(AuditEntry(
            user_id=key_fingerprint(req.user_key or ""),
            mode=req.mode,
            prompt_version=settings.prompt_version,
            findings=findings,
            sources_ids=[],
            used_model=False,
            emergency=True,
            crisis=crisis,
            needs_doctor=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_excerpt=req.message[:300],
            response=json.loads(response.model_dump_json()),
        ))
        return response

    # 2) تحديد المعدّل — بعد فلتر الطوارئ عن قصد: لا نحجب أبدًا طلبًا
    #    تفعّل فيه الفلتر لسبب يتعلق بحصة الاستهلاك.
    identity = req.user_key or "anonymous"
    decision = limiter.check(identity)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="عدد الطلبات كبير الآن. حاولي بعد قليل.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    # 3) استرجاع المصادر
    sources = _rag.retrieve(req.message, settings.rag_top_k)
    if not sources and req.mode == "summary":
        from .i18n import get_translator, resolve_locale
        summary_q = get_translator().t("retrieval.summary_query", resolve_locale(req.language_hint))
        sources = _rag.retrieve(summary_q, settings.rag_top_k)
    allowed_ids = {c.id for c in sources}

    # 3أ) لو كانت الرسالة تحية أو سؤال تعريفي («انت مين؟» / «مرحبا» / «بتعملي ايه؟»)
    if not sources and req.mode == "chat":
        from .i18n import resolve_locale
        locale = resolve_locale(req.language_hint)
        intro_reply = _check_greeting_or_identity_reply(req.message, locale)
        if intro_reply is not None:
            rule_codes = [f.rule_code for f in findings]
            response = ChatResponse(
                answer=intro_reply,
                sources_used=[],
                needs_doctor=max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
                emergency=False,
                crisis=False,
                missing_info=[],
                prompt_version=settings.prompt_version,
                rule_codes=rule_codes,
            )
            _write_audit(AuditEntry(
                user_id=key_fingerprint(req.user_key or ""),
                mode=req.mode,
                prompt_version=settings.prompt_version,
                findings=findings,
                sources_ids=[],
                used_model=False,
                emergency=False,
                crisis=False,
                needs_doctor=bool(response.needs_doctor),
                llm_error="",
                latency_ms=int((time.monotonic() - started) * 1000),
                request_excerpt="",
                response=json.loads(response.model_dump_json()),
            ))
            return response

    # 3ب) لا مصدر معتمد ⇒ لا استدعاء للموديل. القاعدة نفسها في /api/v1/ai: بلا
    #     مصدر لا إجابة من معرفة عامة، بل نص صريح «لا أملك مصدرًا موثوقًا».
    if not sources:
        from .i18n import get_translator, resolve_locale
        locale = resolve_locale(req.language_hint)
        t = get_translator()
        answer = t.t("answer.no_reliable_source", locale)
        rule_codes = [f.rule_code for f in findings]
        if req.mode == "summary":
            # الملخص عقد مختلف: نفس الحدّ («لا مصدر معتمد») بحقول الملخص.
            response = SummaryResponse(
                overview=answer,
                what_this_does_not_mean=t.t("answer.does_not_mean", locale),
                questions_for_doctor=[t.t("answer.ask_doctor_default", locale)],
                sources_used=[],
                prompt_version=settings.prompt_version,
                rule_codes=rule_codes,
            )
        else:
            response = ChatResponse(
                answer=answer,
                sources_used=[],
                needs_doctor=max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
                emergency=False,
                crisis=False,
                missing_info=[],
                prompt_version=settings.prompt_version,
                rule_codes=rule_codes,
            )
        _write_audit(AuditEntry(
            user_id=key_fingerprint(req.user_key or ""),
            mode=req.mode,
            prompt_version=settings.prompt_version,
            findings=findings,
            sources_ids=[],
            used_model=False,
            emergency=False,
            crisis=False,
            needs_doctor=bool(getattr(response, "needs_doctor", False)),
            llm_error="no approved sources retrieved",
            latency_ms=int((time.monotonic() - started) * 1000),
            request_excerpt="",
            response=json.loads(response.model_dump_json()),
        ))
        return response

    # 4) بناء الـ prompt
    system = _prompt_builder.build(
        mode=req.mode,
        user_context=ctx,
        findings=findings,
        sources=sources,
        rules_glossary=_rules_glossary,
        current_date=date.today().isoformat(),
        emergency_number=emergency.number,
        crisis_line=emergency.crisis_line,
    )

    # 5) استدعاء الموديل + تحقق + إعادة واحدة ثم رد احتياطي
    retries = 0
    fallback = False
    llm_error = ""
    elapsed = 0
    data: dict[str, Any] | None = None
    result = None

    if _llm is None:
        # لا مفتاح؟ لا نُسقط الطلب: نفس مسار الرد الاحتياطي الآمن.
        llm_error = "GROQ_API_KEY is not set"
    else:
        # أي فشل في الموديل (شبكة، TPM، تحقق) → رد احتياطي وليس 500
        try:
            clean_history = scrub_conversation_history(
                [h.model_dump() for h in (req.history or [])]
            )
            try:
                raw, elapsed = _llm.complete(system, req.message, history=clean_history)
            except TypeError:
                raw, elapsed = _llm.complete(system, req.message)
            result = validate(raw, req.mode, allowed_ids)

            while not result.ok and retries < 1:
                retries += 1
                try:
                    raw, extra = _llm.complete(
                        system, req.message, retry_feedback=raw, history=clean_history
                    )
                except TypeError:
                    raw, extra = _llm.complete(system, req.message, retry_feedback=raw)
                elapsed += extra
                result = validate(raw, req.mode, allowed_ids)
        except Exception as exc:  # noqa: BLE001 — طبقة الأمان: لا نُسقط الطلب أبدًا
            result = None
            llm_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("فشل استدعاء الموديل: %s", llm_error)

    if result is None or not result.ok or result.data is None:
        fallback = True
        if not llm_error and result is not None:
            llm_error = ("validation failed: " + "; ".join(result.errors))[:200]
            log.warning("رفض validator مخرجات الموديل: %s", llm_error)
        answer = NOT_CONFIGURED_ANSWER if _llm is None else FALLBACK_ANSWER
        data = {
            "answer": answer,
            "sources_used": [],
            "needs_doctor": max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
            "emergency": False,
            "crisis": False,
            "missing_info": ["تعذّر التحقق من إجابة المساعد"],
        }
        if req.mode == "summary":
            data = {
                "overview": answer,
                "what_changed": "",
                "patterns": "",
                "medical_alerts": "",
                "what_this_does_not_mean": "هذا النمط لا يحدد حالة طبية.",
                "questions_for_doctor": ["ما تقييم طبيبة لدوراتي المسجّلة؟"],
                "sources_used": [],
            }
    else:
        data = result.data

    # 6) طبقة ما بعد الموديل: إن أعلن طوارئ/أزمة لم يلتقطها الفلتر المعجمي،
    #    فالرقم يجب أن يظهر في نص الرد نفسه لا في العلم وحده.
    if req.mode != "summary" and isinstance(data, dict):
        if data.get("emergency") or data.get("crisis"):
            is_crisis = bool(data.get("crisis"))
            data["answer"] = ensure_emergency_text(
                str(data.get("answer") or ""), emergency, crisis=is_crisis
            )
            data["needs_doctor"] = True
            data["emergency_payload"] = EmergencyPayload(
                kind="crisis" if is_crisis else "medical",
                number=emergency.number,
                number_verified=emergency.verified,
                country_code=emergency.country_code,
                crisis_line=emergency.crisis_line if is_crisis else "",
                instruction=str(data["answer"]).split("\n")[0],
            )

    rule_codes = [f.rule_code for f in findings]

    if req.mode == "summary":
        response = SummaryResponse(**{k: data[k] for k in SummaryResponse.model_fields
                                       if k in data},
                                   prompt_version=settings.prompt_version,
                                   rule_codes=rule_codes)
    else:
        response = ChatResponse(**{k: data[k] for k in ChatResponse.model_fields
                                   if k in data},
                                prompt_version=settings.prompt_version,
                                rule_codes=rule_codes)

    _write_audit(AuditEntry(
        user_id=key_fingerprint(req.user_key or ""),
        mode=req.mode,
        prompt_version=settings.prompt_version,
        findings=findings,
        sources_ids=list(allowed_ids),
        used_model=not fallback,
        emergency=bool(getattr(response, "emergency", False)),
        crisis=bool(getattr(response, "crisis", False)),
        needs_doctor=bool(getattr(response, "needs_doctor", False)),
        validator_retries=retries,
        fallback=fallback,
        llm_error=llm_error,
        latency_ms=int((time.monotonic() - started) * 1000),
        request_excerpt=req.message[:300],
        response=json.loads(response.model_dump_json()),
    ))
    return response


@app.middleware("http")
async def security_and_privacy_headers(request: Request, call_next):
    """ترويسات أمنية ومنع التخزين المؤقت لمسارات البيانات الصحية والملفات المتغيرة."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    path = request.url.path
    if not path.startswith("/fonts/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------- الصحة

@app.get("/health")
def health() -> dict[str, Any]:
    """حالة التشغيل دون كشف مفاتيح أو أرقام: أعلام فقط."""
    info = _emergency_info(settings.country_code, _emergency_override(), settings.crisis_line)
    return {
        "status": "ok",
        "prompt_version": settings.prompt_version,
        "model": settings.groq_model,
        "llm_configured": _llm is not None,
        "chunks_loaded": len(_rag.chunks),
        "chunks_citable": len(_rag.retrievable),
        "drafts_pending_review": len(_rag.draft_chunks),
        "chunks_text_removed": len(_rag.text_removed_chunks),
        "drafts_included": settings.knowledge_include_drafts,
        "emergency_number_is_default": settings.emergency_number_is_default,
        "emergency_number_verified": info.verified,
        "crisis_line_configured": bool(info.crisis_line),
        "auth_required": bool(settings.api_key),
        "country_code": settings.country_code,
        "tracker_enabled": True,
    }


# ------------------------------------------------------------------- المحادثة

@app.get("/v1/meta")
def meta() -> dict[str, Any]:
    """بيانات البلدان المتاحة ورقم الطوارئ الفعّال — للواجهة وللمراجعة.

    لا يكشف مفاتيح. يُظهر حالة التحقق صراحةً حتى لا يبدو الرقم العام موثوقًا.
    """
    info = _emergency_info(settings.country_code, _emergency_override(), settings.crisis_line)
    return {
        "default_country": settings.country_code,
        "emergency_number": info.number,
        "emergency_number_verified": info.verified,
        "crisis_line_configured": bool(info.crisis_line),
        "countries": [
            {
                "code": code,
                "name_ar": _emergency_numbers.lookup(code).country_name,
                "emergency": _emergency_numbers.lookup(code).number,
                "verified": _emergency_numbers.lookup(code).verified,
            }
            for code in _emergency_numbers.known_countries
        ],
        "prompt_version": settings.prompt_version,
        # إعداد اللغة للواجهة: كل ما تحتاجه لتهيئة i18next/RTL بلا قيم مكتوبة
        # في التطبيق (المصدر الوحيد هو الإعدادات/البيئة).
        "locale": {
            "default": settings.default_locale,
            "supported": settings.supported_locale_list,
            "digits_style": settings.digits_style,
            "week_start": settings.week_start,
            "week_start_index": settings.week_start_index,
            "timezone": settings.default_timezone,
            "dir": {loc: ("rtl" if loc.startswith("ar") else "ltr")
                    for loc in settings.supported_locale_list},
        },
    }


@app.post("/v1/chat", response_model=None)
def chat(req: ChatRequest, x_api_key: str | None = None,
         request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not req.message.strip():
        if req.mode == "summary":
            req = req.model_copy(update={"message": "ملخص للدورة الشهرية"})
        else:
            raise HTTPException(status_code=422, detail="message is empty")
    if request is not None and not req.language_hint:
        accept_lang = request.headers.get("accept-language")
        if accept_lang:
            req = req.model_copy(update={"language_hint": accept_lang})
    # المصادقة تُتحقق داخل المسار (لا كاعتماد عام) حتى لا يحجب خطأ المصادقة
    # رد الطوارئ: مستخدمة في خطر بمفتاح خاطئ يجب أن تصلها أرقام الطوارئ.
    from .services.security import check_api_key
    filt_preview = run_filter(req.message, [])
    if not filt_preview.triggered:
        check_api_key(x_api_key)
        claims = _extract_bearer_claims(request)
        if claims is not None:
            req = req.model_copy(update={"user_key": claims.user_key})
    else:
        try:
            claims = _extract_bearer_claims(request)
            if claims is not None:
                req = req.model_copy(update={"user_key": claims.user_key})
        except HTTPException:
            pass
    response = _run_pipeline(req)
    return json.loads(response.model_dump_json())


# ----------------------------------------- مصادقة الحسابات والملف الشخصي والأمان

@app.post("/v1/auth/register", dependencies=[Depends(api_key_header)])
def register_account(body: RegisterRequest) -> dict[str, Any]:
    try:
        user = _store.register_user(body.username, body.password, body.display_name or "")
    except StoreError as exc:
        code = 409 if str(exc) == "username_taken" else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    migrated = {"cycles_migrated": 0, "symptoms_migrated": 0}
    if body.migrate_user_key:
        migrated = _store.migrate_user_data(body.migrate_user_key, user["user_key"])

    access_token, refresh_token = _store.create_session(user["id"], user["username"])
    profile = _store.get_profile(user["user_key"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
        "profile": profile,
        "migrated": migrated,
    }


@app.post("/v1/auth/login", dependencies=[Depends(api_key_header)])
def login_account(body: LoginRequest) -> dict[str, Any]:
    try:
        user = _store.authenticate_user(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    migrated = {"cycles_migrated": 0, "symptoms_migrated": 0}
    if body.migrate_user_key:
        migrated = _store.migrate_user_data(body.migrate_user_key, user["user_key"])

    access_token, refresh_token = _store.create_session(user["id"], user["username"])
    profile = _store.get_profile(user["user_key"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
        "profile": profile,
        "migrated": migrated,
    }


@app.post("/v1/auth/refresh", dependencies=[Depends(api_key_header)])
def refresh_session(body: RefreshRequest) -> dict[str, Any]:
    try:
        user, access_token, refresh_token = _store.rotate_refresh_token(body.refresh_token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
    }


@app.post("/v1/auth/logout", dependencies=[Depends(api_key_header)])
def logout_session(body: RefreshRequest) -> dict[str, Any]:
    revoked = _store.revoke_refresh_token(body.refresh_token)
    return {"logged_out": True, "revoked": revoked}


@app.get("/v1/auth/me", dependencies=[Depends(api_key_header)])
def current_account(request: Request) -> dict[str, Any]:
    claims = _extract_bearer_claims(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    user = _store.get_user_by_id(claims.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user_not_found")
    return {
        "user": user,
        "profile": _store.get_profile(user["user_key"]),
        "consents": _store.get_consents(user["user_key"]),
    }


@app.post("/v1/auth/pin", dependencies=[Depends(api_key_header)])
def update_pin_and_disguise(body: PinSecurityRequest, request: Request) -> dict[str, Any]:
    claims = _extract_bearer_claims(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    try:
        user = _store.set_user_security(
            claims.user_id,
            pin=body.pin,
            clear_pin=body.clear_pin,
            disguise_mode=body.disguise_mode,
        )
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"user": user}


@app.post("/v1/auth/verify-pin", dependencies=[Depends(api_key_header)])
def verify_pin(body: VerifyPinRequest, request: Request) -> dict[str, Any]:
    claims = _extract_bearer_claims(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    ok = _store.verify_user_pin(claims.user_id, body.pin)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid_pin")
    return {"verified": True}


@app.get("/v1/profile", dependencies=[Depends(api_key_header)])
def get_profile(user_key: str | None = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    return _store.get_profile(ukey)


@app.put("/v1/profile", dependencies=[Depends(api_key_header)])
def save_profile(
    payload: ProfilePayload,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    claims = _extract_bearer_claims(request)
    ukey = claims.user_key if claims is not None else _resolve_user_key(user_key, request)
    user_id = claims.user_id if claims is not None else None
    try:
        return _store.upsert_profile(
            ukey,
            payload.model_dump(exclude_unset=True),
            user_id=user_id,
        )
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/consents", dependencies=[Depends(api_key_header)])
def list_consents(user_key: str | None = None, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    return _store.get_consents(ukey)


@app.put("/v1/consents", dependencies=[Depends(api_key_header)])
def update_consent(
    payload: ConsentUpdateRequest,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ukey = _resolve_user_key(user_key, request)
    try:
        return _store.set_consent(ukey, payload.consent_type, payload.granted)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------- تتبّع الدورات والأعراض

@app.get("/v1/cycles", response_model=list[CycleOut], dependencies=[Depends(api_key_header)])
def list_cycles(user_key: str | None = None, request: Request = None) -> list[dict]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    return _store.list_cycles(ukey)


@app.post("/v1/cycles", response_model=CycleOut, dependencies=[Depends(api_key_header)])
def add_cycle(cycle: CycleIn, user_key: str | None = None, request: Request = None) -> dict:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    try:
        return _store.add_cycle(ukey, cycle.start_date, cycle.length_days)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/cycles/{cycle_id}", dependencies=[Depends(api_key_header)])
def delete_cycle(cycle_id: int, user_key: str | None = None, request: Request = None) -> dict:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    if not _store.delete_cycle(ukey, cycle_id):
        raise HTTPException(status_code=404, detail="الدورة غير موجودة")
    return {"deleted": cycle_id}


@app.get("/v1/symptoms", response_model=list[SymptomOut], dependencies=[Depends(api_key_header)])
def list_symptoms(
    user_key: str | None = None,
    since_days: int | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> list[dict]:
    ukey = _resolve_user_key(user_key, request)
    return _store.list_symptoms(ukey, since_days=since_days)


@app.post("/v1/symptoms", response_model=SymptomOut, dependencies=[Depends(api_key_header)])
def add_symptom(
    symptom: SymptomIn,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    ukey = _resolve_user_key(user_key, request)
    try:
        return _store.add_symptom(ukey, symptom.log_date, symptom.symptom,
                                  symptom.severity, symptom.note)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/symptoms/{symptom_id}", dependencies=[Depends(api_key_header)])
def delete_symptom(symptom_id: int, user_key: str | None = None, request: Request = None) -> dict:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    if not _store.delete_symptom(ukey, symptom_id):
        raise HTTPException(status_code=404, detail="السجل غير موجود")
    return {"deleted": symptom_id}


@app.delete("/v1/data", dependencies=[Depends(api_key_header)])
def delete_all_data(user_key: str | None = None, request: Request = None) -> dict:  # type: ignore[assignment]
    """حق الحذف: مسح كل بيانات هذه المستخدمة من قاعدة البيانات."""
    ukey = _resolve_user_key(user_key, request)
    return _store.delete_all(ukey)


@app.get("/v1/insights", response_model=InsightsResponse, dependencies=[Depends(api_key_header)])
def insights(user_key: str | None = None, request: Request = None) -> InsightsResponse:  # type: ignore[assignment]
    """«الرؤى الطبية» محليًا بلا موديل: نتائج محرك القواعد + شرحها.

    تعمل بالكامل بلا إنترنت وبلا مفتاح — وهذا مقصود: التحليل الأساسي للتتبّع
    يجب ألا يتوقف على توفّر مزوّد خارجي.
    """
    ukey = _resolve_user_key(user_key, request)
    stats = _store.cycle_stats(ukey)
    symptoms = _store.list_symptoms(ukey, limit=200, since_days=180)
    ctx = UserContext(
        cycles_recorded=stats["cycles_recorded"],
        avg_cycle_days=stats["avg_cycle_days"],
        cycle_gaps=stats["cycle_gaps"],
        last_cycles=stats["last_cycles"],
    )
    findings = compute_all_findings(ctx, symptoms)
    return InsightsResponse(
        findings=findings,
        glossary={f.rule_code: _rules_glossary[f.rule_code]
                  for f in findings if f.rule_code in _rules_glossary},
        cycles_recorded=stats["cycles_recorded"],
        avg_cycle_days=stats["avg_cycle_days"],
        needs_doctor=max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
        rule_codes=[f.rule_code for f in findings],
        prompt_version=settings.prompt_version,
    )


# ------------------------------------ التنبؤ بنطاق، السجل اليومي، المكتبة، والشريك

@app.get("/v1/predictions", dependencies=[Depends(api_key_header)])
def get_predictions(user_key: str | None = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    cycles = _store.list_cycles(ukey, limit=24)
    return predict_next_cycle_window(cycles).to_dict()


@app.post("/v1/predictions/correct", dependencies=[Depends(api_key_header)])
def correct_prediction_start(
    body: PredictionCorrectionRequest,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ukey = _resolve_user_key(user_key, request)
    try:
        corrected = _store.correct_cycle_start(ukey, body.actual_start_date, body.length_days)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cycles = _store.list_cycles(ukey, limit=24)
    return {
        "cycle": corrected,
        "prediction": predict_next_cycle_window(cycles).to_dict(),
    }


@app.get("/v1/daily-logs", dependencies=[Depends(api_key_header)])
def list_daily_logs(user_key: str | None = None, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    return _store.list_daily_logs(ukey)


@app.put("/v1/daily-logs", dependencies=[Depends(api_key_header)])
def save_daily_log(
    body: DailyLogPayload,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ukey = _resolve_user_key(user_key, request)
    try:
        return _store.upsert_daily_log(ukey, body.log_date, body.model_dump(exclude_unset=True))
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/daily-logs/copy-yesterday", dependencies=[Depends(api_key_header)])
def copy_yesterday_log(
    target_date: str | None = None,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ukey = _resolve_user_key(user_key, request)
    try:
        return _store.copy_yesterday_log(ukey, target_date)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/library", dependencies=[Depends(api_key_header)])
def get_library_articles(
    q: str = "",
    topic: str = "",
    life_mode: str = "",
) -> dict[str, Any]:
    """المكتبة التثقيفية: تعرض فقط المقالات المعتمدة (`approved`) التي تملك مراجِعة وتاريخ مراجعة."""
    raw_items: list[dict[str, Any]] = []
    for chunk in _rag.chunks:
        raw_items.append(
            {
                "id": chunk.id,
                "title": chunk.section,
                "topic": chunk.section,
                "life_mode": "all",
                "body": chunk.text,
                "status": "approved" if chunk.status == "verified" and not chunk.text_removed else chunk.status,
                "reviewed_by": chunk.reviewer,
                "reviewed_at": chunk.reviewed_at,
                "source_name": chunk.source_name,
                "text_removed": chunk.text_removed,
            }
        )
    articles = filter_approved_articles(
        raw_items,
        query=q,
        topic=topic,
        life_mode=life_mode,
        allow_draft=False,
    )
    return {"count": len(articles), "articles": articles}


@app.get("/v1/partner-share", dependencies=[Depends(api_key_header)])
def list_partner_shares(user_key: str | None = None, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    return _store.list_partner_shares(ukey)


@app.post("/v1/partner-share", dependencies=[Depends(api_key_header)])
def create_partner_share(
    body: PartnerShareCreateRequest,
    user_key: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    ukey = _resolve_user_key(user_key, request)
    return _store.create_partner_share(ukey, body.partner_label, body.scope)


@app.delete("/v1/partner-share/{share_id}", dependencies=[Depends(api_key_header)])
def revoke_partner_share(share_id: int, user_key: str | None = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    ukey = _resolve_user_key(user_key, request)
    if not _store.revoke_partner_share(ukey, share_id):
        raise HTTPException(status_code=404, detail="share_not_found")
    return {"revoked": share_id}


@app.get("/v1/partner-share/view/{share_token}")
def view_partner_share(share_token: str) -> dict[str, Any]:
    """عرض محدود بموافقة للشريك: يعرض نافذة التوقع فقط ولا يكشف أي ملاحظات أو أعراض خاصة."""
    share = _store.resolve_partner_share(share_token)
    if share is None:
        raise HTTPException(status_code=404, detail="share_revoked_or_invalid")
    cycles = _store.list_cycles(share["user_key"], limit=24)
    pred = predict_next_cycle_window(cycles)
    return {
        "partner_label": share["partner_label"],
        "scope": share["scope"],
        "prediction_status": pred.status,
        "window_start": pred.window_start,
        "window_end": pred.window_end,
        "confidence": pred.confidence,
    }


@app.get("/v1/export/full.json", dependencies=[Depends(api_key_header)])
def export_full_json(user_key: str | None = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    """نسخة تصدير كاملة بضغطة واحدة لجميع بيانات المستخدمة (متوافقة مع تكاملات الصحة)."""
    ukey = _resolve_user_key(user_key, request)
    cycles = _store.list_cycles(ukey, limit=500)
    symptoms = _store.list_symptoms(ukey, limit=1000)
    daily_logs = _store.list_daily_logs(ukey, limit=500)
    profile = _store.get_profile(ukey)
    consents = _store.get_consents(ukey)
    prediction = predict_next_cycle_window(cycles).to_dict()
    return {
        "schema_version": "1.0",
        "exported_at": date.today().isoformat(),
        "profile": profile,
        "cycles": cycles,
        "daily_logs": daily_logs,
        "symptoms": symptoms,
        "prediction": prediction,
        "consents": consents,
    }


def _export_context(request: Request, locale: str | None,
                    digits_style: str | None) -> ExportContext:
    from .i18n import resolve_locale
    resolved_locale = resolve_locale(locale or request.headers.get("accept-language"))
    return ExportContext.from_request(resolved_locale, digits_style=digits_style)


@app.get("/v1/export/cycles.csv", dependencies=[Depends(api_key_header)])
def export_cycles_csv(user_key: str | None = None, request: Request = None,  # type: ignore[assignment]
                      locale: str | None = None,
                      digits_style: str | None = None) -> Response:
    ukey = _resolve_user_key(user_key, request)
    ctx = _export_context(request, locale, digits_style)
    raw_cycles = _store.list_cycles(ukey, limit=200)
    ordered = sorted(raw_cycles, key=lambda c: c["start_date"])
    enriched: list[dict[str, Any]] = []
    for idx, c in enumerate(ordered):
        cycle_len = None
        if idx > 0:
            delta = (date.fromisoformat(c["start_date"])
                     - date.fromisoformat(ordered[idx - 1]["start_date"])).days
            if 1 <= delta <= 400:
                cycle_len = delta
        enriched.append({**c, "cycle_length": cycle_len})
    payload = cycles_to_csv(reversed(enriched), ctx)
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cycles.csv"'},
    )


@app.get("/v1/export/symptoms.csv", dependencies=[Depends(api_key_header)])
def export_symptoms_csv(user_key: str | None = None, request: Request = None,  # type: ignore[assignment]
                        locale: str | None = None,
                        digits_style: str | None = None) -> Response:
    ukey = _resolve_user_key(user_key, request)
    ctx = _export_context(request, locale, digits_style)
    rows = _store.list_symptoms(ukey, limit=200)
    payload = symptoms_to_csv(rows, ctx)
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="symptoms.csv"'},
    )


@app.get("/v1/export/report.pdf", dependencies=[Depends(api_key_header)])
def export_report_pdf(user_key: str | None = None, request: Request = None,  # type: ignore[assignment]
                      locale: str | None = None,
                      digits_style: str | None = None) -> Response:
    from .i18n import get_translator
    ukey = _resolve_user_key(user_key, request)
    ctx = _export_context(request, locale, digits_style)
    t = get_translator()
    cycles = _store.list_cycles(ukey, limit=24)
    symptoms = _store.list_symptoms(ukey, limit=100, since_days=180)
    stats = _store.cycle_stats(ukey)
    u_ctx = UserContext(
        cycles_recorded=stats["cycles_recorded"],
        avg_cycle_days=stats["avg_cycle_days"],
        cycle_gaps=stats["cycle_gaps"],
        last_cycles=stats["last_cycles"],
    )
    findings = compute_all_findings(u_ctx, symptoms)
    sections = build_doctor_report_sections(cycles, symptoms, findings, ctx)
    try:
        pdf_bytes = build_report_pdf(
            sections,
            ctx,
            title=t.t("app.name", ctx.locale),
            footer=t.t("app.not_a_diagnosis", ctx.locale),
        )
    except FontNotFound as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cyclecare-report.pdf"'},
    )


_ai_pipeline = _build_ai_pipeline()

# مسارات الطبقة الجديدة (قاعدة المعرفة + التعريب). تُسجَّل قبل static mount
# لأن Starlette يطابق بحسب ترتيب التسجيل.
app.include_router(ai_router)

# الواجهة تُخدم من نفس التطبيق، لذا لا نحتاج CORS افتراضيًا.
# فتح "*" كان يسمح لأي موقع باستخدام الـ API (بمفتاح الخادم ودون مصادقة).
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ملفات الترجمة تُخدم من نفس الأصل حتى تقرأها الواجهة بلا طلبات عبر أصل آخر.
if Path(settings.locales_path).exists():
    app.mount("/locales", StaticFiles(directory=str(settings.locales_path)),
              name="locales")

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
