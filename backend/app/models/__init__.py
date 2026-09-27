"""SQLAlchemy ORM models (PostgreSQL).

UUID primary keys use PostgreSQL's built-in gen_random_uuid() (PG13+), so the
uuid-ossp extension is not required.
"""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain import (
    ClotSize,
    Contraception,
    FindingStatus,
    FlowLevel,
    Language,
    PregnancyPossibility,
    PregnancyTestResult,
    ProductType,
    Severity,
)


def _in(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("language", Language.values()), name="ck_users_language"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Africa/Cairo")
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="ar")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    settings: Mapped["UserSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )
    health_profile: Mapped["HealthProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    period_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    symptom_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    weekly_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    medical_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    reminder_time: Mapped[time | None] = mapped_column(Time, server_default=text("'20:00'"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    user: Mapped[User] = relationship(back_populates="settings")


class HealthProfile(Base):
    """User-provided context. Known conditions are self-reported, never app-generated."""

    __tablename__ = "health_profiles"
    __table_args__ = (
        CheckConstraint(f"contraception_type IS NULL OR {_in('contraception_type', Contraception.values())}", name="ck_hp_contraception"),
        CheckConstraint(
            f"pregnancy_possibility IS NULL OR {_in('pregnancy_possibility', PregnancyPossibility.values())}", name="ck_hp_pregnancy"
        ),
        CheckConstraint("typical_cycle_length IS NULL OR typical_cycle_length BETWEEN 10 AND 120", name="ck_hp_cycle_len"),
        CheckConstraint("typical_period_length IS NULL OR typical_period_length BETWEEN 1 AND 20", name="ck_hp_period_len"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    contraception_type: Mapped[str | None] = mapped_column(Text)
    contraception_started_on: Mapped[date | None] = mapped_column(Date)
    breastfeeding: Mapped[bool | None] = mapped_column(Boolean)
    pregnancy_possibility: Mapped[str | None] = mapped_column(Text)
    typical_cycle_length: Mapped[int | None] = mapped_column(Integer)
    typical_period_length: Mapped[int | None] = mapped_column(Integer)
    known_pcos: Mapped[bool | None] = mapped_column(Boolean)
    known_endometriosis: Mapped[bool | None] = mapped_column(Boolean)
    known_thyroid_condition: Mapped[bool | None] = mapped_column(Boolean)
    known_bleeding_disorder: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    user: Mapped[User] = relationship(back_populates="health_profile")


class Cycle(Base):
    """A menstrual period. Cycle length is derived from consecutive start dates."""

    __tablename__ = "cycles"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_cycles_end_after_start"),
        CheckConstraint("duration_days IS NULL OR duration_days >= 1", name="ck_cycles_duration_positive"),
        UniqueConstraint("user_id", "start_date", name="uq_cycles_user_start"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class BleedingLog(Base):
    __tablename__ = "bleeding_logs"
    __table_args__ = (
        CheckConstraint(_in("flow_level", FlowLevel.values()), name="ck_bleeding_flow"),
        CheckConstraint(f"product_type IS NULL OR {_in('product_type', ProductType.values())}", name="ck_bleeding_product"),
        CheckConstraint(f"clot_size IS NULL OR {_in('clot_size', ClotSize.values())}", name="ck_bleeding_clot"),
        CheckConstraint("product_changes IS NULL OR product_changes BETWEEN 0 AND 100", name="ck_bleeding_changes"),
        UniqueConstraint("user_id", "log_date", name="uq_bleeding_user_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="SET NULL"))
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    flow_level: Mapped[str] = mapped_column(Text, nullable=False)
    product_type: Mapped[str | None] = mapped_column(Text)
    product_changes: Mapped[int | None] = mapped_column(Integer)
    # Heavy-bleeding indicators (NHS)
    frequent_changes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))  # every 1–2 hours
    double_protection: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))  # 2 products together
    flooding: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))  # sudden gush
    leakage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))  # through clothes/bedding
    night_changes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    affects_daily_life: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    clot_size: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class SymptomLog(Base):
    __tablename__ = "symptom_logs"
    __table_args__ = (
        CheckConstraint("pain_level IS NULL OR pain_level BETWEEN 0 AND 10", name="ck_symptom_pain"),
        CheckConstraint("energy_level IS NULL OR energy_level BETWEEN 0 AND 10", name="ck_symptom_energy"),
        CheckConstraint("sleep_quality IS NULL OR sleep_quality BETWEEN 0 AND 10", name="ck_symptom_sleep"),
        Index("ix_symptom_logs_user_date", "user_id", "log_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    pain_level: Mapped[int | None] = mapped_column(Integer)
    pain_location: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    affects_daily_activity: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    symptoms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    mood: Mapped[str | None] = mapped_column(Text)
    energy_level: Mapped[int | None] = mapped_column(Integer)
    sleep_quality: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Medication(Base):
    __tablename__ = "medications"
    __table_args__ = (CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_med_dates"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    dosage: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class PregnancyTest(Base):
    __tablename__ = "pregnancy_tests"
    __table_args__ = (CheckConstraint(_in("result", PregnancyTestResult.values()), name="ck_pregnancy_result"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    test_date: Mapped[date] = mapped_column(Date, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class MedicalFinding(Base):
    """Output of the deterministic Medical Rules Engine. Severity is server-owned."""

    __tablename__ = "medical_findings"
    __table_args__ = (
        CheckConstraint(_in("severity", Severity.values()), name="ck_findings_severity"),
        CheckConstraint(_in("status", FindingStatus.values()), name="ck_findings_status"),
        Index("ix_findings_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    rule_code: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_version: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="ar")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = _updated_at()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    messages: Mapped[list["AIMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True, order_by="AIMessage.created_at"
    )


class AIMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_ai_messages_role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    conversation: Mapped[AIConversation] = relationship(back_populates="messages")


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (CheckConstraint("status IN ('pending', 'sent', 'cancelled', 'failed')", name="ck_reminders_status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    reminder_type: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = _created_at()


class RefreshToken(Base):
    """Rotating refresh tokens. Reuse of a rotated token revokes the whole family."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Security audit trail. Never stores passwords, tokens, API keys or medical notes."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str | None] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = _created_at()


__all__ = [
    "AIConversation",
    "AIMessage",
    "AuditLog",
    "BleedingLog",
    "Cycle",
    "HealthProfile",
    "MedicalFinding",
    "Medication",
    "PregnancyTest",
    "RefreshToken",
    "Reminder",
    "SymptomLog",
    "User",
    "UserSettings",
]
