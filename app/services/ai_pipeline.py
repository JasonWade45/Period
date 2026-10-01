"""خط أنابيب `/api/v1/ai/*`: قواعد ← فلتر طوارئ ← استرجاع ← موديل ← تحقق.

الترتيب غير قابل للتفاوض (من البريف):
    authenticate → emergency filter → findings (Rules Engine) → retrieval
    → LLM (JSON) → validation → retry once → safe fallback

قاعدتان تحكمان هذا الملف:
1. **لا نص طبي من الموديل بلا مصدر معتمد.** الاسترجاع الفارغ يعني ردًّا ثابتًا
   «لا أملك مصدرًا موثوقًا»، بلا استدعاء للموديل أصلًا — لا إجابة من معرفة عامة.
2. **الطوارئ لا تصل إلى الموديل ولا إلى السجل بمحتوى الرسالة.** الرد ثابت من
   ملف الموارد، والتدقيق يخزّن نوع المطابقة وطول الرسالة لا نصّها (لا PII).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..config import settings
from ..i18n import get_translator, resolve_locale
from ..kb.retrieval import HybridRetriever
from ..kb.schemas import RetrievedChunk
from ..schemas import (
    AiChatRequest,
    AiChatResponse,
    AiSummaryResponse,
    AuditEntry,
    EmergencyPayload,
    Finding,
    RetrievalMeta,
    SourceChunk,
    Severity,
    UserContext,
)
from .arabic import has_arabic
from .emergency_filter import (
    FilterResult,
    build_fixed_reply,
    ensure_emergency_text,
    run_filter,
)
from .emergency_numbers import EmergencyNumbers
from .prompt_builder import PromptBuilder
from .referral import ensure_referral_notice
from .rules_engine import compute_all_findings, max_severity
from .security import key_fingerprint
from .validator import validate

log = logging.getLogger("cyclecare.ai")


@dataclass
class PipelineDeps:
    """اعتماديات صريحة — بلا حالة عالمية، فيسهل اختبار كل خطوة وحدها."""

    retriever: HybridRetriever
    prompt_builder: PromptBuilder
    llm: Any                                  # LLMClient أو None (بلا مفتاح)
    numbers: EmergencyNumbers
    store: Any = None                         # Store للتتبّع (اختياري)
    audit_writer: Any = None                  # دالة كتابة التدقيق (اختيارية)
    clock: Any = None                         # دالة تُرجع تاريخ اليوم (للاستقرار)
    # مسرد القواعد (rules_glossary.json) — يدخل البرومبت لتفسير أكواد النتائج
    rules_glossary: dict = field(default_factory=dict)


def resolve_request_language(req: AiChatRequest, accept_language: str | None) -> str:
    """لغة الرد: طلب صريح ← Accept-Language ← لغة الرسالة ← الافتراضية.

    لا نُجيب بالإنجليزية سؤالًا عربيًا (ولا العكس) لأن المصادر نفسها قد تختلف
    باللغة، ومطابقة اللغة جزء من معيار القبول.
    """
    if req.language:
        return resolve_locale(req.language)
    if accept_language:
        return resolve_locale(accept_language)
    if req.message.strip() and not has_arabic(req.message):
        return "en"
    return resolve_locale(None)


class AiPipeline:
    def __init__(self, deps: PipelineDeps):
        self.deps = deps

    # ------------------------------------------------------------------ مصادر
    def _to_source_chunks(self, chunks: list[RetrievedChunk]) -> list[SourceChunk]:
        """تحويل مقاطع قاعدة المعرفة إلى شكل البرومبت.

        الحالة تُنقل كـ`verified` لأن الاسترجاع لا يمرّر إلا المعتمد (أو المسودة
        التجريبية عند KB_ALLOW_DRAFT)، و`reviewer` يُملأ من سجلّ المقطع.
        """
        out: list[SourceChunk] = []
        for chunk in chunks:
            out.append(SourceChunk(
                id=chunk.id,
                source_name=chunk.source_id,
                section=chunk.title or chunk.topic,
                text=chunk.content,
                status="verified",
            ))
        return out

    # ------------------------------------------------------------------ طوارئ
    def _info(self, req: AiChatRequest):
        """رقم الطوارئ للبلد. الإعداد الصريح EMERGENCY_NUMBER يتقدّم على الجدول،
        أما القيمة الافتراضية في الكود فلا تتقدّم (وإلا صار اختيار البلد بلا أثر)."""
        override = "" if settings.emergency_number_is_default else settings.emergency_number
        return self.deps.numbers.lookup(
            req.country_code or settings.emergency_country,
            override_number=override, override_crisis_line=settings.crisis_line)

    def _emergency_response(self, req: AiChatRequest, findings: list[Finding],
                            kind: str, matched: str, language: str,
                            started: float, code: str = "emergency_filter",
                            mode: str = "chat") -> AiChatResponse:
        info = self._info(req)
        result = FilterResult(kind=kind, matched=matched, source="message")
        answer = build_fixed_reply(result, info, locale=language)

        crisis = kind == "crisis"
        payload = EmergencyPayload(
            kind=kind, number=info.number, number_verified=info.verified,
            country_code=info.country_code,
            crisis_line=info.crisis_line if crisis else "",
            instruction=answer.split("\n")[0],
        )
        response = AiChatResponse(
            answer=answer, sources_used=[], needs_doctor=True, emergency=True,
            crisis=crisis, missing_info=[], prompt_version=settings.prompt_version,
            model="", language=language,
            rule_codes=[f.rule_code for f in findings],
            decision="emergency_filter",
            emergency_payload=payload,
            retrieval=RetrievalMeta(kind="disabled"),
        )
        self._audit(req, findings, sources_ids=[], used_model=False, emergency=True,
                    crisis=crisis, needs_doctor=True, language=language,
                    started=started, code=code, mode=mode,
                    response=response.model_dump())
        return response

    # ----------------------------------------------------------------- تتبّع
    def _audit(self, req: AiChatRequest, findings: list[Finding], *,
               sources_ids: list[str], used_model: bool, emergency: bool, crisis: bool,
               needs_doctor: bool, language: str, started: float, code: str,
               mode: str = "chat", model: str = "", retries: int = 0,
               fallback: bool = False, llm_error: str = "",
               response: dict[str, Any] | None = None) -> None:
        """كتابة تدقيق بلا PII: لا نص رسالة، فقط بصمة وطول ونوع المطابقة."""
        if self.deps.audit_writer is None:
            return
        entry = AuditEntry(
            user_id=key_fingerprint(req.user_key or ""),
            mode=mode, prompt_version=settings.prompt_version,
            findings=findings, sources_ids=list(sources_ids), used_model=used_model,
            emergency=emergency, crisis=crisis, needs_doctor=needs_doctor,
            validator_retries=retries, fallback=fallback, llm_error=llm_error,
            latency_ms=int((time.monotonic() - started) * 1000),
            response=response or {},
        )
        extra = {"language": language, "model": model, "decision": code,
                 "message_len": len(req.message)}
        try:
            self.deps.audit_writer(entry, extra)
        except Exception as exc:  # noqa: BLE001 — التدقيق لا يُسقط الطلب
            log.warning("فشل كتابة سجل التدقيق: %s", type(exc).__name__)

    # -------------------------------------------------------------- رد ثابت
    def _no_source_response(self, req: AiChatRequest, findings: list[Finding],
                            language: str, started: float,
                            retriever_reason: str) -> AiChatResponse:
        t = get_translator()
        answer = t.t("answer.no_reliable_source", language)
        response = AiChatResponse(
            answer=answer, sources_used=[],
            needs_doctor=max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
            emergency=False, crisis=False, missing_info=[],
            prompt_version=settings.prompt_version, model="", language=language,
            rule_codes=[f.rule_code for f in findings], decision="no_source",
            retrieval=RetrievalMeta(kind="no_source", used_language=language),
        )
        self._audit(req, findings, sources_ids=[], used_model=False, emergency=False,
                    crisis=False, needs_doctor=response.needs_doctor, language=language,
                    started=started, code="no_source",
                    llm_error=retriever_reason[:200])
        return response

    # ------------------------------------------------------------------ الطلب
    def chat(self, req: AiChatRequest, *, accept_language: str | None = None,
             context: UserContext | None = None,
             symptoms: list[dict] | None = None) -> AiChatResponse:
        started = time.monotonic()
        language = resolve_request_language(req, accept_language)
        ctx = context or req.user_context
        findings = compute_all_findings(ctx, symptoms or [])

        # (1) فلتر الطوارئ قبل أي استرجاع أو استدعاء للموديل
        filt = run_filter(req.message, findings)
        if filt.triggered:
            return self._emergency_response(req, findings, filt.kind or "medical",
                                            filt.matched, language, started)

        # (2) الاسترجاع — لا مصدر معتمد يعني لا استدعاء للموديل
        retrieval = self.deps.retriever.retrieve(req.message, language=language)
        if not retrieval.chunks:
            return self._no_source_response(req, findings, language, started,
                                            retrieval.reason)

        sources = self._to_source_chunks(retrieval.chunks)
        allowed_ids = {c.id for c in retrieval.chunks}

        # (3) البرومبت + الموديل + التحقق (محاولة إعادة واحدة)
        info = self._info(req)
        system = self.deps.prompt_builder.build(
            mode="chat", user_context=ctx, findings=findings, sources=sources,
            rules_glossary=self.deps.rules_glossary, current_date=self._today(),
            emergency_number=info.number, crisis_line=info.crisis_line,
        )

        retries = 0
        fallback = False
        llm_error = ""
        model = ""
        data: dict[str, Any] | None = None

        if self.deps.llm is None:
            llm_error = "LLM is not configured"
            fallback = True
        else:
            try:
                raw, _elapsed = self.deps.llm.complete(system, req.message)
                model = getattr(settings, "groq_model", "")
                result = validate(raw, "chat", allowed_ids)
                while not result.ok and retries < 1:
                    retries += 1
                    raw, _extra = self.deps.llm.complete(system, req.message,
                                                         retry_feedback=raw)
                    result = validate(raw, "chat", allowed_ids)
                if result.ok and result.data is not None:
                    data = result.data
                else:
                    fallback = True
                    llm_error = ("validation failed: "
                                 + "; ".join(getattr(result, "errors", [])))[:200]
            except Exception as exc:  # noqa: BLE001 — لا نُسقط الطلب أبدًا
                fallback = True
                llm_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("فشل استدعاء الموديل: %s", llm_error)

        if data is None:
            t = get_translator()
            answer = (t.t("answer.not_configured", language) if self.deps.llm is None
                      else t.t("answer.fallback", language))
            response = AiChatResponse(
                answer=answer, sources_used=[],
                needs_doctor=max_severity(findings).at_least(Severity.MEDICAL_REVIEW),
                emergency=False, crisis=False,
                missing_info=[t.t("answer.not_verified", language)],
                prompt_version=settings.prompt_version, model=model, language=language,
                rule_codes=[f.rule_code for f in findings], decision="fallback",
                retrieval=RetrievalMeta(candidates=len(retrieval.chunks),
                                        used_language=retrieval.used_language,
                                        fell_back_to_english=retrieval.fell_back_to_english),
            )
            self._audit(req, findings, sources_ids=sorted(allowed_ids),
                        used_model=False, emergency=False, crisis=False,
                        needs_doctor=response.needs_doctor, language=language,
                        started=started, code="fallback", model=model, retries=retries,
                        fallback=True, llm_error=llm_error,
                        response=response.model_dump())
            return response

        # (4) طبقة ما بعد الموديل: إن أعلن طوارئ لم يلتقطها الفلتر، يجب أن يظهر
        #     الرقم والتعليمات في النص نفسه، ويُعاد payload الطوارئ للواجهة.
        emergency = bool(data.get("emergency"))
        crisis = bool(data.get("crisis"))
        if emergency or crisis:
            data["answer"] = ensure_emergency_text(str(data.get("answer") or ""), info,
                                                   crisis=crisis)
            data["needs_doctor"] = True
        else:
            # سطر الاستشارة يفرضه الخادم لا التزام الموديل بالبرومبت (قرار المالك).
            data["answer"] = ensure_referral_notice(str(data.get("answer") or ""), language)

        used = [sid for sid in data.get("sources_used", []) if sid in allowed_ids]
        payload = None
        if emergency or crisis:
            payload = EmergencyPayload(
                kind="crisis" if crisis else "medical", number=info.number,
                number_verified=info.verified, country_code=info.country_code,
                crisis_line=info.crisis_line if crisis else "",
                instruction=data["answer"].split("\n")[0],
            )

        response = AiChatResponse(
            answer=str(data.get("answer") or ""), sources_used=used,
            needs_doctor=bool(data.get("needs_doctor")) or emergency or crisis,
            emergency=emergency, crisis=crisis,
            missing_info=[str(x) for x in data.get("missing_info", [])],
            prompt_version=settings.prompt_version, model=model, language=language,
            rule_codes=[f.rule_code for f in findings], decision="ok",
            emergency_payload=payload,
            retrieval=RetrievalMeta(candidates=len(retrieval.chunks),
                                    used_language=retrieval.used_language,
                                    fell_back_to_english=retrieval.fell_back_to_english),
        )
        self._audit(req, findings, sources_ids=used, used_model=True, emergency=emergency,
                    crisis=crisis, needs_doctor=response.needs_doctor, language=language,
                    started=started, code="ok", model=model, retries=retries,
                    response=response.model_dump())
        return response

    # ------------------------------------------------------------------- الملخّص
    def summary(self, req: AiChatRequest, *, accept_language: str | None = None,
                context: UserContext | None = None,
                symptoms: list[dict] | None = None) -> AiSummaryResponse:
        """ملخّص الدورات المسجّلة — نفس المسار، بعقد مختلف.

        رسالة الملخص اختيارية (الواجهة ترسل نص الاختصار): الفلتر يفحصها كما
        في المسار العادي، ونتائج محرك القواعد تمرّ بالفلتر أيضًا — فإن تفعّل
        أيٌّ منهما فالرد ثابت بلا موديل.
        """
        started = time.monotonic()
        language = resolve_request_language(req, accept_language)
        ctx = context or req.user_context
        findings = compute_all_findings(ctx, symptoms or [])

        filt = run_filter(req.message, findings)
        if filt.triggered:
            fixed = self._emergency_response(req, findings, filt.kind or "medical",
                                             filt.matched, language, started,
                                             code="emergency_findings", mode="summary")
            return AiSummaryResponse(
                overview=fixed.answer, medical_alerts=fixed.answer,
                what_this_does_not_mean=get_translator().t("answer.does_not_mean", language),
                questions_for_doctor=[get_translator().t("answer.ask_doctor_default", language)],
                sources_used=[], prompt_version=settings.prompt_version,
                language=language, rule_codes=fixed.rule_codes,
                decision="emergency_filter",
                retrieval=RetrievalMeta(kind="disabled"),
            )

        # الاسترجاع يبنى على مواضيع نتائج محرك القواعد لا على رسالة حرة.
        # «بيانات غير كافية» ليست موضوعًا: لا تُستخدم كاستعلام وإلا فشل الاسترجاع
        # دائمًا لمستخدمة جديدة، وهو أسوأ موضع للتراجع فيه.
        t = get_translator()
        topics = [f.title for f in findings if f.rule_code != "INSUFFICIENT_DATA"]
        query = " ".join(topics) or t.t("retrieval.summary_query", language)
        retrieval = self.deps.retriever.retrieve(query, language=language)
        if not retrieval.chunks:
            empty = AiSummaryResponse(
                overview=t.t("answer.no_reliable_source", language),
                what_this_does_not_mean=t.t("answer.does_not_mean", language),
                questions_for_doctor=[t.t("answer.ask_doctor_default", language)],
                sources_used=[], prompt_version=settings.prompt_version,
                language=language, rule_codes=[f.rule_code for f in findings],
                decision="no_source",
                retrieval=RetrievalMeta(kind="no_source", used_language=language),
            )
            self._audit(req, findings, sources_ids=[], used_model=False, emergency=False,
                        crisis=False, needs_doctor=empty.overview != "",
                        language=language, started=started, code="no_source_summary",
                        mode="summary", llm_error=retrieval.reason[:200],
                        response=empty.model_dump())
            return empty

        sources = self._to_source_chunks(retrieval.chunks)
        allowed_ids = {c.id for c in retrieval.chunks}
        info = self._info(req)
        system = self.deps.prompt_builder.build(
            mode="summary", user_context=ctx, findings=findings, sources=sources,
            rules_glossary=self.deps.rules_glossary, current_date=self._today(),
            emergency_number=info.number, crisis_line=info.crisis_line,
        )

        model = ""
        data: dict[str, Any] | None = None
        retries = 0
        llm_error = ""
        if self.deps.llm is None:
            llm_error = "LLM is not configured"
        else:
            try:
                raw, _elapsed = self.deps.llm.complete(system, req.message or "summary")
                model = getattr(settings, "groq_model", "")
                result = validate(raw, "summary", allowed_ids)
                while not result.ok and retries < 1:
                    retries += 1
                    raw, _extra = self.deps.llm.complete(system, req.message or "summary",
                                                         retry_feedback=raw)
                    result = validate(raw, "summary", allowed_ids)
                if result.ok and result.data is not None:
                    data = result.data
                else:
                    llm_error = ("validation failed: "
                                 + "; ".join(getattr(result, "errors", [])))[:200]
            except Exception as exc:  # noqa: BLE001
                llm_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("فشل استدعاء الموديل (ملخص): %s", llm_error)

        if data is None:
            answer = (t.t("answer.not_configured", language) if self.deps.llm is None
                      else t.t("answer.fallback", language))
            response = AiSummaryResponse(
                overview=answer,
                what_this_does_not_mean=t.t("answer.does_not_mean", language),
                questions_for_doctor=[t.t("answer.ask_doctor_default", language)],
                sources_used=[], prompt_version=settings.prompt_version, model=model,
                language=language, rule_codes=[f.rule_code for f in findings],
                decision="fallback",
                retrieval=RetrievalMeta(candidates=len(retrieval.chunks),
                                        used_language=retrieval.used_language,
                                        fell_back_to_english=retrieval.fell_back_to_english),
            )
            self._audit(req, findings, sources_ids=sorted(allowed_ids), used_model=False,
                        emergency=False, crisis=False, needs_doctor=False,
                        language=language, started=started, code="fallback_summary",
                        mode="summary", model=model, retries=retries, fallback=True,
                        llm_error=llm_error, response=response.model_dump())
            return response

        used = [sid for sid in data.get("sources_used", []) if sid in allowed_ids]
        response = AiSummaryResponse(
            overview=str(data.get("overview") or ""),
            what_changed=str(data.get("what_changed") or ""),
            patterns=str(data.get("patterns") or ""),
            medical_alerts=str(data.get("medical_alerts") or ""),
            what_this_does_not_mean=str(data.get("what_this_does_not_mean") or ""),
            questions_for_doctor=[str(q) for q in data.get("questions_for_doctor", [])],
            sources_used=used, prompt_version=settings.prompt_version, model=model,
            language=language, rule_codes=[f.rule_code for f in findings],
            decision="ok",
            retrieval=RetrievalMeta(candidates=len(retrieval.chunks),
                                    used_language=retrieval.used_language,
                                    fell_back_to_english=retrieval.fell_back_to_english),
        )
        self._audit(req, findings, sources_ids=used, used_model=True, emergency=False,
                    crisis=False, needs_doctor=False, language=language, started=started,
                    code="ok_summary", mode="summary", model=model, retries=retries,
                    response=response.model_dump())
        return response

    def _today(self) -> str:
        return (self.deps.clock() if self.deps.clock else date.today()).isoformat()


__all__ = ["AiPipeline", "PipelineDeps", "resolve_request_language"]
