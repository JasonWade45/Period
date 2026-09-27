from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FindingOut(BaseModel):
    """Read-only. There is intentionally no endpoint that accepts a severity."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_code: str
    rule_version: str
    content_version: str
    severity: str
    title: str
    description: str
    recommended_action: str
    is_emergency: bool
    evidence: dict[str, Any] | None
    language: str
    status: str
    detected_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class SafetyAlert(BaseModel):
    severity: str
    title: str
    body: str
    is_emergency: bool


class ChatOut(BaseModel):
    conversation_id: UUID
    reply: str
    language: Literal["ar", "en"]
    ai_generated: bool
    model: str | None
    fallback_reason: str | None
    safety_alert: SafetyAlert | None
    overall_severity: str
    context_topics: list[str]


class SummaryIn(BaseModel):
    period: Literal["3_cycles", "6_cycles", "12_cycles"] = "6_cycles"


class SummaryOut(BaseModel):
    summary: str
    ai_generated: bool
    model: str | None
    fallback_reason: str | None
    findings: list[dict[str, Any]]
    questions_for_doctor: list[str]
    safety_alert: SafetyAlert | None
    overall_severity: str
    language: Literal["ar", "en"]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: str
    content: str
    model: str | None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
