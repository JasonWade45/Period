"""Analytics, predictions, dashboard and medical findings (read side)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, get_owned
from app.domain import FindingStatus, Severity
from app.models import MedicalFinding
from app.schemas.medical_ai import FindingOut
from app.services.findings_service import OPEN_STATUSES, evaluate_and_sync
from app.services.health_data import load_user_health_data
from app.services.medical_rules import load_content, load_rules_config

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])
predictions_router = APIRouter(prefix="/predictions", tags=["predictions"])
medical_router = APIRouter(prefix="/medical", tags=["medical"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------- analytics


@analytics_router.get("/overview")
def overview(user: CurrentUser, db: DB):
    data = load_user_health_data(db, user)
    stats = data.cycle_stats()
    periods = data.period_stats()
    return {
        "cycle_count": stats.cycle_count,
        "average_cycle_length": stats.mean,
        "median_cycle_length": stats.median,
        "min_cycle_length": stats.min,
        "max_cycle_length": stats.max,
        "std_dev": stats.std_dev,
        "recent_3_average": stats.recent_3_mean,
        "recent_6_average": stats.recent_6_mean,
        "recent_12_average": stats.recent_12_mean,
        "average_period_duration": periods["mean_duration"],
        "max_period_duration": periods["max_duration"],
        "average_peak_flow": periods["average_peak_flow"],
        "variability": stats.variability,
        "pattern": stats.pattern,
    }


@analytics_router.get("/cycles")
def cycles_history(user: CurrentUser, db: DB):
    data = load_user_health_data(db, user)
    return {"cycles": data.history(), "stats": data.cycle_stats().to_dict()}


@analytics_router.get("/symptoms")
def symptoms(user: CurrentUser, db: DB):
    return load_user_health_data(db, user).symptom_frequency()


@analytics_router.get("/bleeding")
def bleeding(user: CurrentUser, db: DB):
    data = load_user_health_data(db, user)
    return {
        **data.period_stats(),
        "per_cycle": [{k: r[k] for k in ("index", "start", "period_days", "end_is_explicit", "peak_flow")} for r in data.history()],
    }


# ---------------------------------------------------------------- predictions


@predictions_router.get("/next-period")
def next_period(user: CurrentUser, db: DB):
    data = load_user_health_data(db, user)
    return data.predict().to_dict(data.language)


# ---------------------------------------------------------------- dashboard


@dashboard_router.get("")
def dashboard(user: CurrentUser, db: DB):
    """Everything the Home screen needs in one call (PRD §11)."""
    data, evaluation = evaluate_and_sync(db, user)
    db.commit()
    stats = data.cycle_stats()
    prediction = data.predict()
    return {
        "today": data.today.isoformat(),
        "display_name": user.display_name,
        "has_data": bool(data.periods),
        "cycle_day": prediction.current_cycle_day,
        "prediction": prediction.to_dict(data.language),
        "pattern": stats.pattern,
        "variability": stats.variability,
        "last_cycle_lengths": data.lengths[-6:],
        "overall_severity": evaluation.overall_severity.value,
        "severity_label": load_content()["severity_labels"][data.language][evaluation.overall_severity.value],
        "alert": evaluation.alert.to_dict() if evaluation.alert else None,
        "requires_immediate_attention": evaluation.requires_immediate_attention,
        "data_gaps": evaluation.data_gaps,
    }


# ---------------------------------------------------------------- medical findings


@medical_router.post("/evaluate")
def evaluate(user: CurrentUser, db: DB):
    """Run all deterministic rules now and persist findings."""
    _, evaluation = evaluate_and_sync(db, user)
    db.commit()
    return evaluation.to_dict()


@medical_router.get("/findings", response_model=list[FindingOut])
def list_findings(
    user: CurrentUser, db: DB, status: Literal["active", "acknowledged", "resolved", "all"] = "all", limit: int = Query(100, le=500)
):
    q = select(MedicalFinding).where(MedicalFinding.user_id == user.id)
    if status != "all":
        q = q.where(MedicalFinding.status == status)
    return db.scalars(q.order_by(MedicalFinding.detected_at.desc()).limit(limit)).all()


@medical_router.get("/findings/active", response_model=list[FindingOut])
def active_findings(user: CurrentUser, db: DB):
    evaluate_and_sync(db, user)
    db.commit()
    rows = db.scalars(select(MedicalFinding).where(MedicalFinding.user_id == user.id, MedicalFinding.status.in_(OPEN_STATUSES))).all()
    return sorted(rows, key=lambda r: -Severity(r.severity).rank)


@medical_router.post("/findings/{finding_id}/acknowledge", response_model=FindingOut)
def acknowledge(finding_id: uuid.UUID, user: CurrentUser, db: DB):
    """The only client-side change allowed. Severity is never client-modifiable."""
    row = get_owned(db, MedicalFinding, finding_id, user)
    if row.status == FindingStatus.RESOLVED.value:
        raise HTTPException(status_code=409, detail="Finding is already resolved")
    row.status = FindingStatus.ACKNOWLEDGED.value
    row.acknowledged_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


@medical_router.get("/rules")
def rules_config(_: CurrentUser):
    """Transparency: the active ruleset (thresholds, versions, sources)."""
    return load_rules_config()
