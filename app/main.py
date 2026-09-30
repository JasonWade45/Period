from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import (
    AuditEntry,
    ChatRequest,
    ChatResponse,
    CycleIn,
    CycleOut,
    CycleStat,
    Finding,
    InsightsResponse,
    Severity,
    SummaryResponse,
    SymptomIn,
    SymptomOut,
    UserContext,
)
from .services import audit
from .services.emergency_filter import build_fixed_reply, ensure_emergency_text, run_filter
from .services.emergency_numbers import EmergencyNumbers
from .services.llm import LLMClient
from .services.prompt_builder import PromptBuilder
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
    if not settings.cors_origins:
        log.info("CORS: نفس الأصل فقط (الوضع الافتراضي).")
    log.info(
        "CycleCare جاهز — prompt=%s model=%s chunks=%d db=%s rate_limit=%d/%ds",
        settings.prompt_version, settings.groq_model, len(_rag.chunks),
        settings.db_path, settings.rate_limit_requests, settings.rate_limit_window_seconds,
    )
    yield


app = FastAPI(title="CycleCare AI", version=settings.prompt_version, lifespan=lifespan)

try:
    _llm = LLMClient()
except RuntimeError:
    _llm = None  # وضع الاختبار بدون مفتاح

_prompt_builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
_rag = KeywordRag(settings.sources_path)
_rules_glossary: dict[str, str] = json.loads(
    settings.rules_glossary_path.read_text(encoding="utf-8")
)
_store = Store(settings.db_path)
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


def _effective_context(req: ChatRequest) -> tuple[UserContext, list[dict], dict]:
    """يدمج سياق الطلب مع البيانات المسجّلة فعلًا.

    البيانات المسجّلة في قاعدة البيانات هي المصدر الأدق للأرقام (عدد الدورات،
    متوسط الطول، آخر الدورات)، لأنها محسوبة من تواريخ حقيقية لا من إدخال يدوي.
    تبقى حقول الطلب (العمر، وسيلة المنع، الأمراض، الحمل) كما أرسلتها الواجهة.
    """
    stats: dict[str, Any] = {}
    symptoms: list[dict] = []
    if req.user_key:
        stats = _store.cycle_stats(req.user_key)
        symptoms = _store.list_symptoms(req.user_key, limit=200, since_days=180)

    if not stats.get("cycles_recorded"):
        return req.user_context, symptoms, stats

    # نبني سياقًا جديدًا بالتحقق الكامل من الأنواع؛ model_copy(update=…) لا
    # يتحقق من الصحة فتبقى القواميس قواميس ويقع محرك القواعد لاحقًا.
    merged = UserContext(
        **{
            **req.user_context.model_dump(),
            "cycles_recorded": stats["cycles_recorded"],
            "avg_cycle_days": stats["avg_cycle_days"],
            "last_cycles": [CycleStat(**c) for c in stats["last_cycles"]],
        }
    )
    return merged, symptoms, stats


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
        response = ChatResponse(
            answer=reply,
            sources_used=[],
            needs_doctor=True,
            emergency=True,
            crisis=crisis,
            missing_info=[],
            prompt_version=settings.prompt_version,
            rule_codes=[f.rule_code for f in findings],
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
    allowed_ids = {c.id for c in sources}

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
            raw, elapsed = _llm.complete(system, req.message)
            result = validate(raw, req.mode, allowed_ids)

            while not result.ok and retries < 1:
                retries += 1
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
            data["answer"] = ensure_emergency_text(
                str(data.get("answer") or ""), emergency, crisis=bool(data.get("crisis"))
            )
            data["needs_doctor"] = True

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
    }


@app.post("/v1/chat", response_model=None)
def chat(req: ChatRequest, x_api_key: str | None = None,
         request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="message is empty")
    # المصادقة تُتحقق داخل المسار (لا كاعتماد عام) حتى لا يحجب خطأ المصادقة
    # رد الطوارئ: مستخدمة في خطر بمفتاح خاطئ يجب أن تصلها أرقام الطوارئ.
    from .services.security import check_api_key
    filt_preview = run_filter(req.message, [])
    if not filt_preview.triggered:
        check_api_key(x_api_key)
    response = _run_pipeline(req)
    return json.loads(response.model_dump_json())


# ------------------------------------------------------- تتبّع الدورات والأعراض

@app.get("/v1/cycles", response_model=list[CycleOut], dependencies=[Depends(api_key_header)])
def list_cycles(user_key: str) -> list[dict]:
    return _store.list_cycles(user_key)


@app.post("/v1/cycles", response_model=CycleOut, dependencies=[Depends(api_key_header)])
def add_cycle(cycle: CycleIn, user_key: str) -> dict:
    try:
        return _store.add_cycle(user_key, cycle.start_date, cycle.length_days)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/cycles/{cycle_id}", dependencies=[Depends(api_key_header)])
def delete_cycle(cycle_id: int, user_key: str) -> dict:
    if not _store.delete_cycle(user_key, cycle_id):
        raise HTTPException(status_code=404, detail="الدورة غير موجودة")
    return {"deleted": cycle_id}


@app.get("/v1/symptoms", response_model=list[SymptomOut], dependencies=[Depends(api_key_header)])
def list_symptoms(user_key: str, since_days: int | None = None) -> list[dict]:
    return _store.list_symptoms(user_key, since_days=since_days)


@app.post("/v1/symptoms", response_model=SymptomOut, dependencies=[Depends(api_key_header)])
def add_symptom(symptom: SymptomIn, user_key: str) -> dict:
    try:
        return _store.add_symptom(user_key, symptom.log_date, symptom.symptom,
                                  symptom.severity, symptom.note)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/symptoms/{symptom_id}", dependencies=[Depends(api_key_header)])
def delete_symptom(symptom_id: int, user_key: str) -> dict:
    if not _store.delete_symptom(user_key, symptom_id):
        raise HTTPException(status_code=404, detail="السجل غير موجود")
    return {"deleted": symptom_id}


@app.delete("/v1/data", dependencies=[Depends(api_key_header)])
def delete_all_data(user_key: str) -> dict:
    """حق الحذف: مسح كل بيانات هذه المستخدمة من قاعدة البيانات."""
    return _store.delete_all(user_key)


@app.get("/v1/insights", response_model=InsightsResponse, dependencies=[Depends(api_key_header)])
def insights(user_key: str) -> InsightsResponse:
    """«الرؤى الطبية» محليًا بلا موديل: نتائج محرك القواعد + شرحها.

    تعمل بالكامل بلا إنترنت وبلا مفتاح — وهذا مقصود: التحليل الأساسي للتتبّع
    يجب ألا يتوقف على توفّر مزوّد خارجي.
    """
    stats = _store.cycle_stats(user_key)
    symptoms = _store.list_symptoms(user_key, limit=200, since_days=180)
    ctx = UserContext(
        cycles_recorded=stats["cycles_recorded"],
        avg_cycle_days=stats["avg_cycle_days"],
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


# الواجهة تُخدم من نفس التطبيق، لذا لا نحتاج CORS افتراضيًا.
# فتح "*" كان يسمح لأي موقع باستخدام الـ API (بمفتاح الخادم ودون مصادقة).
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
