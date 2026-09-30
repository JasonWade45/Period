from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    NORMAL = "NORMAL"
    MONITOR = "MONITOR"
    MEDICAL_REVIEW = "MEDICAL_REVIEW"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"


class Finding(BaseModel):
    rule_code: str
    severity: Severity
    title: str
    evidence: list[str] = Field(default_factory=list)


class SourceChunk(BaseModel):
    id: str
    source_name: str
    section: str
    reviewed_at: str
    text: str = ""
    keywords: list[str] = Field(default_factory=list)


class ConditionStatus(BaseModel):
    name: str
    status: str  # "مشخّصة" | "غير متأكدة"


class CycleStat(BaseModel):
    start_date: str
    length_days: Optional[int] = None


class UserContext(BaseModel):
    age: Optional[int] = None
    contraception: Optional[str] = None
    cycles_recorded: int = 0
    avg_cycle_days: Optional[int] = None
    last_cycles: list[CycleStat] = Field(default_factory=list)
    conditions: list[ConditionStatus] = Field(default_factory=list)
    pregnancy_status: Optional[str] = None

    def compact_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class ChatRequest(BaseModel):
    message: str
    user_context: UserContext = Field(default_factory=UserContext)
    mode: str = "chat"  # chat | summary
    language_hint: Optional[str] = None


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
