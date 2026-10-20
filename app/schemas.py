from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """مستوى الخطورة (فرز وليس تشخيصًا).

    تحذير مهم: الوراثة من `str` ضرورية لتمثيل JSON، لكنها تجعل `>=` و`<`
    يقارنان **أسماء** القيم أبجديًا لا ترتيب الخطورة:
    `Severity.MONITOR >= Severity.MEDICAL_REVIEW` تصبح True لأن "MONITOR"
    تأتي أبجديًا بعد "MEDICAL_REVIEW". لذلك تُعرَّف هنا دوال المقارنة
    بترتيب الخطورة الصحيح، ويُفضَّل استخدام at_least() للوضوح.
    """

    NORMAL = "NORMAL"
    MONITOR = "MONITOR"
    MEDICAL_REVIEW = "MEDICAL_REVIEW"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def at_least(self, other: "Severity") -> bool:
        """هل هذا المستوى مساوٍ أو أعلى من المستوى الآخر؟"""
        return self.rank >= _coerce_severity(other).rank

    def _other_rank(self, other: Any) -> int | None:
        if isinstance(other, Severity):
            return other.rank
        if isinstance(other, str) and other in _SEVERITY_RANK:
            return _SEVERITY_RANK[Severity(other)]
        return None

    def __lt__(self, other: Any) -> bool:  # type: ignore[override]
        rank = self._other_rank(other)
        return NotImplemented if rank is None else self.rank < rank

    def __le__(self, other: Any) -> bool:  # type: ignore[override]
        rank = self._other_rank(other)
        return NotImplemented if rank is None else self.rank <= rank

    def __gt__(self, other: Any) -> bool:  # type: ignore[override]
        rank = self._other_rank(other)
        return NotImplemented if rank is None else self.rank > rank

    def __ge__(self, other: Any) -> bool:  # type: ignore[override]
        rank = self._other_rank(other)
        return NotImplemented if rank is None else self.rank >= rank


SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.NORMAL,
    Severity.MONITOR,
    Severity.MEDICAL_REVIEW,
    Severity.URGENT,
    Severity.EMERGENCY,
)
_SEVERITY_RANK: dict[Severity, int] = {s: i for i, s in enumerate(SEVERITY_ORDER)}


def _coerce_severity(value: Severity | str) -> Severity:
    return value if isinstance(value, Severity) else Severity(value)


def max_severity_of(severities: list[Severity]) -> Severity:
    """أعلى مستوى خطورة — الترتيب من SEVERITY_ORDER لا من أسماء القيم."""
    return max(severities, key=lambda s: s.rank) if severities else Severity.NORMAL


class Finding(BaseModel):
    rule_code: str
    severity: Severity
    title: str
    evidence: list[str] = Field(default_factory=list)


class SourceChunk(BaseModel):
    id: str
    source_name: str
    section: str
    reviewed_at: str = ""
    text: str = ""
    keywords: list[str] = Field(default_factory=list)
    # حالة المعرفة: "verified" = مُراجَعة ومُوثّقة، "draft" = مسودة لم تُراجَع طبيًا.
    # المسودات لا تُستشهد بها: لا تُرسل للموديل افتراضيًا (KNOWLEDGE_INCLUDE_DRAFTS).
    status: str = "verified"
    reviewer: str = ""            # من راجع المقطع (للمُوثَّق فقط)
    drafted_at: str = ""          # تاريخ كتابة المسودة (لغير المُوثَّق)
    derived_from: list[str] = Field(default_factory=list)  # المراجع التي استُخلصت منها
    # أُزيل نص المقطع بانتظار تأكيد الترخيص: يبقى السجل للمراجعة، ولا يُسترجَع
    # أبدًا حتى لو فُعِّل تضمين المسودات (النص المُزال مكانه ملاحظة لا معلومة).
    text_removed: bool = False
    # إسناد لم يُتحقق منه (اسم جهة نُسب إليها نص بلا مراجعة موثوقة).
    attribution_unverified: bool = False
    # من كتب المقطع: "ai_draft" لمحتوى حزمة kb/ المكتوب بالذكاء الاصطناعي.
    authored_by: str = ""
    # ملاحظة الترخيص الإلزامية للمحتوى المكتوب آليًا: تمنع تقديم المراجع
    # كأنها منقولة حرفيًا من المصدر.
    license_note: str = ""


class ConditionStatus(BaseModel):
    name: str
    status: str  # "مشخّصة" | "غير متأكدة"


class CycleStat(BaseModel):
    start_date: str
    # طول النزيف بالأيام (كم يومًا استمر)، وليس طول الدورة.
    # طول الدورة = الفرق بين تواريخ البداية المتتالية.
    length_days: Optional[int] = None


class UserContext(BaseModel):
    age: Optional[int] = None
    contraception: Optional[str] = None
    cycles_recorded: int = 0
    avg_cycle_days: Optional[int] = None
    last_cycles: list[CycleStat] = Field(default_factory=list)
    # أطوال الدورات الفعلية (فروق تواريخ البداية). يملؤها الخادم من البيانات
    # المسجّلة؛ لا تُقبل من الواجهة كمصدر ثقة.
    cycle_gaps: list[int] = Field(default_factory=list)
    conditions: list[ConditionStatus] = Field(default_factory=list)
    pregnancy_status: Optional[str] = None

    def compact_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    user_context: UserContext = Field(default_factory=UserContext)
    mode: str = "chat"  # chat | summary
    language_hint: Optional[str] = None
    # معرّف جهاز تُنشئه الواجهة عشوائيًا لعزل البيانات عن بعضها. ليس مصادقة.
    user_key: Optional[str] = None
    # بلد المستخدمة: يحدّد رقم الطوارئ من جدول مُوثّق
    country_code: Optional[str] = None
    # ذاكرة المحادثة متعددة الأدوار (آخر 6 رسائل منزوعة الهوية)
    history: list[ChatTurn] = Field(default_factory=list)


class EmergencyPayload(BaseModel):
    """بيانات الطوارئ للواجهة: الرقم وحالته التحققية، بلا كلام طويل."""

    kind: str = "medical"                   # medical | crisis
    number: str = ""
    number_verified: bool = False
    country_code: Optional[str] = None
    crisis_line: str = ""
    instruction: str = ""                   # سطر واحد موجّه (نص المورد)


class ChatResponse(BaseModel):
    answer: str = ""
    sources_used: list[str] = Field(default_factory=list)
    needs_doctor: bool = False
    emergency: bool = False
    crisis: bool = False
    missing_info: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    rule_codes: list[str] = Field(default_factory=list)
    emergency_payload: Optional[EmergencyPayload] = None


class SummaryResponse(BaseModel):
    overview: str = ""
    what_changed: str = ""
    patterns: str = ""
    medical_alerts: str = ""
    what_this_does_not_mean: str = ""
    questions_for_doctor: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    rule_codes: list[str] = Field(default_factory=list)


class CycleIn(BaseModel):
    start_date: str                      # أول يوم نزيف (YYYY-MM-DD)
    length_days: Optional[int] = None


class CycleOut(BaseModel):
    id: int
    start_date: str
    length_days: Optional[int] = None
    bleeding_days: Optional[int] = None
    derived_from_logs: bool = False


class SymptomIn(BaseModel):
    log_date: str
    symptom: str
    severity: Optional[int] = Field(default=None, ge=1, le=5)   # شدة ذكرتها المستخدمة
    note: Optional[str] = None


class SymptomOut(BaseModel):
    id: int
    log_date: str
    symptom: str
    severity: Optional[int] = None
    note: Optional[str] = None


class InsightsResponse(BaseModel):
    """نتائج محرك القواعد بلا موديل — تعمل حتى بلا إنترنت أو مفتاح."""

    findings: list[Finding] = Field(default_factory=list)
    shadow_findings: list[Finding] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    cycles_recorded: int = 0
    avg_cycle_days: Optional[int] = None
    needs_doctor: bool = False
    rule_codes: list[str] = Field(default_factory=list)
    prompt_version: str = ""


class AuditEntry(BaseModel):
    user_id: Optional[str] = None
    mode: str
    prompt_version: str
    findings: list[Finding]
    sources_ids: list[str]
    used_model: bool
    emergency: bool = False
    crisis: bool = False
    needs_doctor: bool = False
    validator_retries: int = 0
    fallback: bool = False
    # سبب الرد الاحتياطي (نوع الخطأ أو أخطاء التحقق) — للتدقيق والتشغيل فقط
    llm_error: str = ""
    latency_ms: int = 0
    request_excerpt: str = ""
    response: dict[str, Any] = {}


# --------------------------------------------------------------- واجهة AI الجديدة
# أضيفت مع توسعة البريف (قاعدة المعرفة + التعريب). لا تُستخدم في /v1/chat
# القديم حتى لا يتغيّر عقد الواجهة القائمة.

class AiChatRequest(BaseModel):
    """طلب /api/v1/ai/chat — الرسالة نص، وكل قراءة الصحة تُبنى من محرك القواعد."""

    message: str
    user_context: UserContext = Field(default_factory=UserContext)
    user_key: Optional[str] = None
    country_code: Optional[str] = None
    # لغة الرد المطلوبة (ar/en). إن غابت تُحدَّد من Accept-Language أو من الرسالة.
    language: Optional[str] = None
    # إعدادات العرض — تُستخدم للأرقام والتواريخ في الردود الثابتة فقط
    digits_style: Optional[str] = None      # western | arabic_indic
    timezone: Optional[str] = None


class RetrievalMeta(BaseModel):
    """ما حدث في الاسترجاع — للتشخيص وللاتساق في الواجهة والتدقيق."""

    candidates: int = 0
    used_language: str = ""
    fell_back_to_english: bool = False
    kind: str = "ok"                        # ok | no_source | disabled


class AiChatResponse(BaseModel):
    answer: str = ""
    sources_used: list[str] = Field(default_factory=list)
    needs_doctor: bool = False
    emergency: bool = False
    crisis: bool = False
    missing_info: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    model: str = ""
    language: str = ""
    rule_codes: list[str] = Field(default_factory=list)
    # القرار المتخذ: ok | no_source | fallback | emergency_filter | summary_empty
    decision: str = ""
    emergency_payload: Optional[EmergencyPayload] = None
    retrieval: RetrievalMeta = Field(default_factory=RetrievalMeta)


class AiSummaryResponse(BaseModel):
    overview: str = ""
    what_changed: str = ""
    patterns: str = ""
    medical_alerts: str = ""
    what_this_does_not_mean: str = ""
    questions_for_doctor: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    model: str = ""
    language: str = ""
    rule_codes: list[str] = Field(default_factory=list)
    decision: str = ""
    retrieval: RetrievalMeta = Field(default_factory=RetrievalMeta)


# ------------------------------------------- حسابات المستخدمات والملف الشخصي (Stage 1)

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    migrate_user_key: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str
    migrate_user_key: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class PinSecurityRequest(BaseModel):
    pin: Optional[str] = None
    clear_pin: bool = False
    disguise_mode: Optional[bool] = None


class VerifyPinRequest(BaseModel):
    pin: str


class ProfilePayload(BaseModel):
    goal: Optional[str] = None
    age: Optional[int] = None
    age_band: Optional[str] = None
    menarche_age: Optional[int] = None
    last_period_start: Optional[str] = None
    currently_bleeding: Optional[bool] = None
    cycle_len_typical: Optional[str] = None
    cycle_len_variation: Optional[str] = None
    bleeding_len_typical: Optional[int] = None
    country_code: Optional[str] = None
    pregnancy_status: Optional[str] = None
    contraception: Optional[str] = None
    conditions: Optional[list[ConditionStatus]] = None
    medications: Optional[list[str]] = None
    meds_affecting_bleeding: Optional[list[str]] = None
    family_bleeding: Optional[str] = None
    typical_cycle_days: Optional[int] = None
    typical_bleeding_days: Optional[int] = None
    show_sensitive_questions: Optional[bool] = None
    enable_lifestyle_questions: Optional[bool] = None
    answered: Optional[list[str]] = None
    skipped: Optional[list[str]] = None
    onboarding_completed: Optional[bool] = None


class ConsentUpdateRequest(BaseModel):
    consent_type: str
    granted: bool


class DailyLogPayload(BaseModel):
    log_date: str
    log_uuid: Optional[str] = None
    flow: Optional[str] = None
    bleeding_level: Optional[str] = None
    pain_score: Optional[int] = None
    pain_blocks_life: Optional[bool] = None
    mood: Optional[str] = None
    mood_tags: Optional[list[str]] = None
    energy: Optional[int] = None
    pad_count: Optional[int] = None
    soak_hourly: Optional[bool] = None
    clots: Optional[str] = None
    dizzy: Optional[bool] = None
    bleeding_color: Optional[str] = None
    discharge: Optional[str] = None
    intermenstrual: Optional[bool] = None
    post_intercourse_bleeding: Optional[bool] = None
    intercourse: Optional[bool] = None
    contraception_used: Optional[str] = None
    ovulation_test: Optional[str] = None
    pregnancy_test: Optional[str] = None
    bbt: Optional[float] = None
    sleep_hours: Optional[float] = None
    water_cups: Optional[int] = None
    water_ml: Optional[int] = None
    exercise: Optional[str] = None
    stress: Optional[int] = None
    medications: Optional[str] = None
    note: Optional[str] = None
    flags: Optional[list[str]] = None
    answered: Optional[list[str]] = None
    skipped: Optional[list[str]] = None
    field_timestamps: Optional[dict[str, str]] = None
    symptoms: Optional[list[Any]] = None
    client_id: Optional[str] = None
    source: Optional[str] = None
    updated_at: Optional[str] = None
    today_local: Optional[str] = None


class SafetyCheckRequest(BaseModel):
    log_date: str
    flags: list[str] = Field(default_factory=list)
    country_code: Optional[str] = None
    language_hint: Optional[str] = None


class PredictionCorrectionRequest(BaseModel):
    actual_start_date: str
    length_days: Optional[int] = None


class PartnerShareCreateRequest(BaseModel):
    partner_label: str = ""
    scope: str = "next_window_only"
