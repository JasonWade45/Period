from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import (
    ConsentIn,
    ConsentOut,
    CycleIn,
    CycleOut,
    HealthProfileIn,
    HealthProfileOut,
    InsightsResponse,
    Severity,
    SymptomIn,
    SymptomOut,
    UserContext,
)
from .routers.ai import router as ai_router
from .services import audit
from .services.ai_pipeline import AiPipeline, PipelineDeps
from .services.emergency_numbers import EmergencyNumbers
from .services.llm import LLMClient
from .services.prompt_builder import PromptBuilder
from .services.rag import KeywordRag
from .services.rules_engine import compute_all_findings, max_severity
from .services.security import api_key_header, key_fingerprint, limiter
from .services.store import Store, StoreError

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
    try:
        purged = audit.purge_old(settings.audit_retention_days)
        if purged:
            log.info("تنظيف سجل التدقيق: أُزيل %d سجلًا أقدم من %d يومًا.",
                     purged, settings.audit_retention_days)
    except Exception as exc:  # noqa: BLE001 — تنظيف السجل لا يوقف الإقلاع
        log.warning("تعذّر تنظيف سجل التدقيق: %s: %s", type(exc).__name__, exc)
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
        rules_glossary=_rules_glossary,
    )
    return AiPipeline(deps)


_emergency_numbers = EmergencyNumbers()

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


# ------------------------------------------------------- تتبّع الدورات والأعراض

@app.get("/v1/cycles", response_model=list[CycleOut], dependencies=[Depends(api_key_header)])
def list_cycles(user_key: str) -> list[dict]:
    return _store.list_bleeding_logs(user_key)


@app.post("/v1/cycles", response_model=CycleOut, dependencies=[Depends(api_key_header)])
def add_cycle(cycle: CycleIn, user_key: str) -> dict:
    try:
        return _store.add_bleeding_log(user_key, cycle.start_date, cycle.bleeding_days)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/cycles/{cycle_id}", dependencies=[Depends(api_key_header)])
def delete_cycle(cycle_id: int, user_key: str) -> dict:
    if not _store.delete_bleeding_log(user_key, cycle_id):
        raise HTTPException(status_code=404, detail="سجل النزيف غير موجود")
    return {"deleted": cycle_id}


@app.get("/v1/symptoms", response_model=list[SymptomOut], dependencies=[Depends(api_key_header)])
def list_symptoms(user_key: str, since_days: int | None = None) -> list[dict]:
    return _store.list_symptom_logs(user_key, since_days=since_days)


@app.post("/v1/symptoms", response_model=SymptomOut, dependencies=[Depends(api_key_header)])
def add_symptom(symptom: SymptomIn, user_key: str) -> dict:
    try:
        return _store.add_symptom_log(user_key, symptom.log_date, symptom.symptom,
                                      symptom.severity, symptom.note)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/symptoms/{symptom_id}", dependencies=[Depends(api_key_header)])
def delete_symptom(symptom_id: int, user_key: str) -> dict:
    if not _store.delete_symptom_log(user_key, symptom_id):
        raise HTTPException(status_code=404, detail="السجل غير موجود")
    return {"deleted": symptom_id}


# ---------------------------------------------------------- ملف المستخدم

_EMPTY_PROFILE: dict[str, Any] = {"conditions": [], "updated_at": ""}


@app.get("/v1/profile", response_model=HealthProfileOut,
         dependencies=[Depends(api_key_header)])
def get_profile(user_key: str) -> dict:
    """ملف المستخدم: لغة/بلد/إعدادات عرض + عمر وحالة. غياب = ملف فارغ (200)."""
    return _store.get_health_profile(user_key) or dict(_EMPTY_PROFILE)


@app.put("/v1/profile", response_model=HealthProfileOut,
         dependencies=[Depends(api_key_header)])
def put_profile(profile: HealthProfileIn, user_key: str) -> dict:
    """استبدال كامل للملف (PUT): ما لا يأتي في الطلب يُمسح — القديم لا يُدمج."""
    try:
        return _store.upsert_health_profile(user_key, profile.model_dump(mode="json"))
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------- الموافقات

@app.get("/v1/consents", response_model=list[ConsentOut],
         dependencies=[Depends(api_key_header)])
def list_consents(user_key: str) -> list[dict]:
    return _store.list_consents(user_key)


@app.post("/v1/consents", response_model=ConsentOut, dependencies=[Depends(api_key_header)])
def set_consent(consent: ConsentIn, user_key: str) -> dict:
    """تسجيل موافقة/رفض نشاط واحد — يستبدل قرار سابق لنفس النشاط."""
    try:
        return _store.set_consent(user_key, consent.consent_key.value, consent.granted,
                                  consent.version)
    except StoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------- حق الحذف

@app.delete("/v1/data", dependencies=[Depends(api_key_header)])
def delete_all_data(user_key: str) -> dict:
    """حذف بيانات التطبيق لهذه المستخدمة من قاعدة البيانات (الدورات، الأعراض، الملف، الموافقات).

    سجل التدقيق (audit/) يبقى — لحذفه معًا راجع DELETE /v1/account.
    """
    return _store.delete_all(user_key)


@app.delete("/v1/account", dependencies=[Depends(api_key_header)])
def delete_account(user_key: str) -> dict:
    """حذف الحساب: كل بيانات التطبيق + سجل التدقيق لهذه البصمة.

    التدقيق يخزّن `user_id = key_fingerprint(user_key)` — نعيد حساب البصمة هنا ثم
    نمسح المطابق (لا يمكن عكس البصمة). الحذف لا يُسقط الخدمة ولا يحتاج مفتاحًا
    سليمًا: مسار الطوارئ يبقى مفتوحًا عمدًا.
    """
    counts = _store.delete_all(user_key)
    purged = audit.purge_user(key_fingerprint(user_key)) if user_key else 0
    return {**counts, "audit_entries_deleted": purged}


@app.get("/v1/insights", response_model=InsightsResponse, dependencies=[Depends(api_key_header)])
def insights(user_key: str) -> InsightsResponse:
    """«الرؤى الطبية» محليًا بلا موديل: نتائج محرك القواعد + شرحها.

    تعمل بالكامل بلا إنترنت وبلا مفتاح — وهذا مقصود: التحليل الأساسي للتتبّع
    يجب ألا يتوقف على توفّر مزوّد خارجي.
    """
    stats = _store.cycle_stats(user_key)
    symptoms = _store.list_symptom_logs(user_key, limit=200, since_days=180)
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
