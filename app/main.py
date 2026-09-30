from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import (
    AuditEntry,
    ChatRequest,
    ChatResponse,
    Finding,
    Severity,
    SummaryResponse,
)
from .services import audit
from .services.emergency_filter import build_fixed_reply, run_filter
from .services.llm import LLMClient
from .services.prompt_builder import PromptBuilder
from .services.rag import KeywordRag
from .services.rules_engine import compute_findings, max_severity
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
    if settings.emergency_number_is_default:
        log.warning(
            "EMERGENCY_NUMBER لا يزال على القيمة الافتراضية (%s). "
            "تحقّقي من رقم الطوارئ الصحيح لبلد المستخدمة قبل الإطلاق.",
            settings.emergency_number,
        )
    if not settings.crisis_line:
        log.warning("CRISIS_LINE غير مضبوط: ردود الأزمات لن تتضمّن خط دعم محلي.")
    if not settings.cors_origins:
        log.info("CORS: نفس الأصل فقط (الوضع الافتراضي).")
    log.info(
        "CycleCare جاهز — prompt=%s model=%s chunks=%d rag_top_k=%d",
        settings.prompt_version, settings.groq_model, len(_rag.chunks), settings.rag_top_k,
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

FALLBACK_ANSWER = (
    "عذراً، تعذّر توليد إجابة آمنة في هذه اللحظة. "
    "حاولي مرة أخرى بعد قليل، وإذا كان هناك عرض مقلق فالتوجّه لطبيبة هو الخطوة الأنسب."
)

# حالة إعداد دائم وليست عطلًا عارضًا: لا نطلب منها إعادة المحاولة بلا فائدة.
NOT_CONFIGURED_ANSWER = (
    "المساعد الذكي غير متاح حاليًا، فلا أستطيع الإجابة عن هذا السؤال الآن. "
    "إن كان هناك عرض مقلق، فالتوجّه لطبيبة هو الخطوة الأنسب."
)


def _write_audit(entry: AuditEntry) -> None:
    """سجل التدقيق مهم، لكن فشل كتابته (قرص ممتلئ/نظام للقراءة فقط) لا يُسقط ردًّا آمنًا."""
    try:
        audit.write(entry)
    except Exception as exc:  # noqa: BLE001
        log.error("تعذّر كتابة سجل التدقيق: %s: %s", type(exc).__name__, exc)


def _run_pipeline(req: ChatRequest) -> ChatResponse | SummaryResponse:
    started = time.monotonic()
    findings: list[Finding] = compute_findings(req.user_context)

    # 1) فلتر ما قبل الموديل: رسالة المستخدم + نتائج القواعد
    filt = run_filter(req.message, findings)
    if filt.triggered:
        reply = build_fixed_reply(filt, settings.emergency_number, settings.crisis_line)
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

    # 2) استرجاع المصادر
    sources = _rag.retrieve(req.message, settings.rag_top_k)
    allowed_ids = {c.id for c in sources}

    # 3) بناء الـ prompt
    system = _prompt_builder.build(
        mode=req.mode,
        user_context=req.user_context,
        findings=findings,
        sources=sources,
        rules_glossary=_rules_glossary,
        current_date=date.today().isoformat(),
        emergency_number=settings.emergency_number,
        crisis_line=settings.crisis_line,
    )

    # 4) استدعاء الموديل + تحقق + إعادة واحدة ثم رد احتياطي
    retries = 0
    fallback = False
    llm_error = ""
    raw, elapsed = "", 0
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
            "needs_doctor": max_severity(findings) >= Severity.MEDICAL_REVIEW,
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


@app.get("/health")
def health() -> dict[str, Any]:
    """حالة التشغيل دون كشف مفاتيح أو أرقام: أعلام فقط."""
    return {
        "status": "ok",
        "prompt_version": settings.prompt_version,
        "model": settings.groq_model,
        "llm_configured": _llm is not None,
        "chunks_loaded": len(_rag.chunks),
        "emergency_number_is_default": settings.emergency_number_is_default,
        "crisis_line_configured": bool(settings.crisis_line),
    }


@app.post("/v1/chat", response_model=None)
def chat(req: ChatRequest) -> dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="message is empty")
    response = _run_pipeline(req)
    return json.loads(response.model_dump_json())


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
