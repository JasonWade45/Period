"""initial schema

Creates all CycleCare tables. UUIDs use built-in gen_random_uuid() (PostgreSQL 13+),
so the uuid-ossp extension is not required.

Revision ID: 0001
Revises:
Create Date: 2026-09-27 03:46:09.384051
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("timezone", sa.Text(), server_default="Africa/Cairo", nullable=False),
        sa.Column("language", sa.Text(), server_default="ar", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("language IN ('ar', 'en')", name="ck_users_language"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_conversations_user_id"), "ai_conversations", ["user_id"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_table(
        "cycles",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), server_default="user", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("duration_days IS NULL OR duration_days >= 1", name="ck_cycles_duration_positive"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_cycles_end_after_start"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "start_date", name="uq_cycles_user_start"),
    )
    op.create_index(op.f("ix_cycles_user_id"), "cycles", ["user_id"], unique=False)
    op.create_table(
        "health_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("contraception_type", sa.Text(), nullable=True),
        sa.Column("contraception_started_on", sa.Date(), nullable=True),
        sa.Column("breastfeeding", sa.Boolean(), nullable=True),
        sa.Column("pregnancy_possibility", sa.Text(), nullable=True),
        sa.Column("typical_cycle_length", sa.Integer(), nullable=True),
        sa.Column("typical_period_length", sa.Integer(), nullable=True),
        sa.Column("known_pcos", sa.Boolean(), nullable=True),
        sa.Column("known_endometriosis", sa.Boolean(), nullable=True),
        sa.Column("known_thyroid_condition", sa.Boolean(), nullable=True),
        sa.Column("known_bleeding_disorder", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "contraception_type IS NULL OR contraception_type IN ('none', 'combined_pill', 'progestogen_only_pill', 'iud', 'hormonal_ius', 'implant', 'injection', 'patch', 'other', 'prefer_not_to_say')",
            name="ck_hp_contraception",
        ),
        sa.CheckConstraint(
            "pregnancy_possibility IS NULL OR pregnancy_possibility IN ('no', 'yes', 'unsure', 'confirmed', 'prefer_not_to_say')",
            name="ck_hp_pregnancy",
        ),
        sa.CheckConstraint("typical_cycle_length IS NULL OR typical_cycle_length BETWEEN 10 AND 120", name="ck_hp_cycle_len"),
        sa.CheckConstraint("typical_period_length IS NULL OR typical_period_length BETWEEN 1 AND 20", name="ck_hp_period_len"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "medical_findings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("rule_code", sa.String(length=16), nullable=False),
        sa.Column("rule_version", sa.String(length=16), nullable=False),
        sa.Column("content_version", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("is_emergency", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("language", sa.Text(), server_default="ar", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("severity IN ('NORMAL', 'MONITOR', 'MEDICAL_REVIEW', 'URGENT', 'EMERGENCY')", name="ck_findings_severity"),
        sa.CheckConstraint("status IN ('active', 'acknowledged', 'resolved')", name="ck_findings_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_findings_user_status", "medical_findings", ["user_id", "status"], unique=False)
    op.create_index(op.f("ix_medical_findings_user_id"), "medical_findings", ["user_id"], unique=False)
    op.create_table(
        "medications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dosage", sa.Text(), nullable=True),
        sa.Column("frequency", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_med_dates"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_medications_user_id"), "medications", ["user_id"], unique=False)
    op.create_table(
        "pregnancy_tests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("test_date", sa.Date(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("result IN ('positive', 'negative', 'invalid', 'unknown')", name="ck_pregnancy_result"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pregnancy_tests_user_id"), "pregnancy_tests", ["user_id"], unique=False)
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"], unique=False)
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "reminders",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("reminder_type", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'sent', 'cancelled', 'failed')", name="ck_reminders_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reminders_user_id"), "reminders", ["user_id"], unique=False)
    op.create_table(
        "symptom_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("pain_level", sa.Integer(), nullable=True),
        sa.Column("pain_location", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("affects_daily_activity", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("symptoms", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("energy_level", sa.Integer(), nullable=True),
        sa.Column("sleep_quality", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("energy_level IS NULL OR energy_level BETWEEN 0 AND 10", name="ck_symptom_energy"),
        sa.CheckConstraint("pain_level IS NULL OR pain_level BETWEEN 0 AND 10", name="ck_symptom_pain"),
        sa.CheckConstraint("sleep_quality IS NULL OR sleep_quality BETWEEN 0 AND 10", name="ck_symptom_sleep"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_symptom_logs_user_date", "symptom_logs", ["user_id", "log_date"], unique=False)
    op.create_index(op.f("ix_symptom_logs_user_id"), "symptom_logs", ["user_id"], unique=False)
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("period_reminders", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("symptom_reminders", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("weekly_summary", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("medical_alerts", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reminder_time", sa.Time(), server_default=sa.text("'20:00'"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "ai_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_ai_messages_role"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_messages_conversation_id"), "ai_messages", ["conversation_id"], unique=False)
    op.create_table(
        "bleeding_logs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("cycle_id", sa.UUID(), nullable=True),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("flow_level", sa.Text(), nullable=False),
        sa.Column("product_type", sa.Text(), nullable=True),
        sa.Column("product_changes", sa.Integer(), nullable=True),
        sa.Column("frequent_changes", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("double_protection", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("flooding", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("leakage", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("night_changes", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("affects_daily_life", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("clot_size", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("clot_size IS NULL OR clot_size IN ('none', 'small', 'large')", name="ck_bleeding_clot"),
        sa.CheckConstraint("flow_level IN ('none', 'spotting', 'light', 'medium', 'heavy', 'very_heavy')", name="ck_bleeding_flow"),
        sa.CheckConstraint(
            "product_type IS NULL OR product_type IN ('pad', 'tampon', 'menstrual_cup', 'period_underwear', 'other')",
            name="ck_bleeding_product",
        ),
        sa.CheckConstraint("product_changes IS NULL OR product_changes BETWEEN 0 AND 100", name="ck_bleeding_changes"),
        sa.ForeignKeyConstraint(["cycle_id"], ["cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "log_date", name="uq_bleeding_user_date"),
    )
    op.create_index(op.f("ix_bleeding_logs_user_id"), "bleeding_logs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bleeding_logs_user_id"), table_name="bleeding_logs")
    op.drop_table("bleeding_logs")
    op.drop_index(op.f("ix_ai_messages_conversation_id"), table_name="ai_messages")
    op.drop_table("ai_messages")
    op.drop_table("user_settings")
    op.drop_index(op.f("ix_symptom_logs_user_id"), table_name="symptom_logs")
    op.drop_index("ix_symptom_logs_user_date", table_name="symptom_logs")
    op.drop_table("symptom_logs")
    op.drop_index(op.f("ix_reminders_user_id"), table_name="reminders")
    op.drop_table("reminders")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_pregnancy_tests_user_id"), table_name="pregnancy_tests")
    op.drop_table("pregnancy_tests")
    op.drop_index(op.f("ix_medications_user_id"), table_name="medications")
    op.drop_table("medications")
    op.drop_index(op.f("ix_medical_findings_user_id"), table_name="medical_findings")
    op.drop_index("ix_findings_user_status", table_name="medical_findings")
    op.drop_table("medical_findings")
    op.drop_table("health_profiles")
    op.drop_index(op.f("ix_cycles_user_id"), table_name="cycles")
    op.drop_table("cycles")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_ai_conversations_user_id"), table_name="ai_conversations")
    op.drop_table("ai_conversations")
    op.drop_table("users")
