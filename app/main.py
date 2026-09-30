from __future__ import annotations

import json
import time
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

app = FastAPI(title="CycleCare AI", version=settings.prompt_version)

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
        audit.write(AuditEntry(
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
    raw, elapsed = "", 0
    data: dict[str, Any] | None = None

    if _llm is None:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is not set")

    # أي فشل في الموديل (شبكة، TPM، تحقق) → رد احتياطي وليس 500
    try:
        raw, elapsed = _llm.complete(system, req.message)
        result = validate(raw, req.mode, allowed_ids)

        while not result.ok and retries < 1:
            retries += 1
            raw, extra = _llm.complete(system, req.message, retry_feedback=raw)
            elapsed += extra
            result = validate(raw, req.mode, allowed_ids)
    except Exception:  # noqa: BLE001 — طبقة الأمان: لا نُسقط الطلب أبدًا
        result = None

    if result is None or not result.ok or result.data is None:
        fallback = True
        data = {
            "answer": FALLBACK_ANSWER,
            "sources_used": [],
            "needs_doctor": max_severity(findings) >= Severity.MEDICAL_REVIEW,
            "emergency": False,
            "crisis": False,
            "missing_info": ["تعذّر التحقق من إجابة المساعد"],
        }
        if req.mode == "summary":
            data = {
                "overview": FALLBACK_ANSWER,
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

    audit.write(AuditEntry(
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
        latency_ms=int((time.monotonic() - started) * 1000),
        request_excerpt=req.message[:300],
        response=json.loads(response.model_dump_json()),
    ))
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "prompt_version": settings.prompt_version,
        "model": settings.groq_model,
        "llm_configured": _llm is not None,
        "chunks_loaded": len(_rag.chunks),
    }


@app.post("/v1/chat", response_model=None)
def chat(req: ChatRequest) -> dict[str, Any]:
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="message is empty")
    response = _run_pipeline(req)
    return json.loads(response.model_dump_json())


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
