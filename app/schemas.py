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


class ChatRequest(BaseModel):
    message: str
    user_context: UserContext = Field(default_factory=UserContext)
    mode: str = "chat"  # chat | summary
    language_hint: Optional[str] = None
    # معرّف جهاز تُنشئه الواجهة عشوائيًا لعزل البيانات عن بعضها. ليس مصادقة.
    user_key: Optional[str] = None
    # بلد المستخدمة: يحدّد رقم الطوارئ من جدول مُوثّق
    country_code: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str = ""
    sources_used: list[str] = Field(default_factory=list)
    needs_doctor: bool = False
    emergency: bool = False
    crisis: bool = False
    missing_info: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    rule_codes: list[str] = Field(default_factory=list)


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
