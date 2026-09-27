"""Persist Rules Engine output as medical_findings rows.

* Findings are upserted per rule code; unchanged findings keep their status
  (so an acknowledged alert is not re-notified).
* A change in severity resolves the old row and creates a new one (auditable).
* Findings that no longer apply are marked resolved — never deleted.
* Severity is only ever written here, from the engine. Clients cannot set it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import FindingStatus, Severity
from app.models import MedicalFinding, User
from app.services.health_data import UserHealthData, load_user_health_data
from app.services.medical_rules import Evaluation, Finding

OPEN_STATUSES = (FindingStatus.ACTIVE.value, FindingStatus.ACKNOWLEDGED.value)


def _apply(row: MedicalFinding, f: Finding, language: str) -> None:
    row.rule_code = f.rule_code
    row.rule_version = f.rule_version
    row.content_version = f.content_version
    row.severity = f.severity.value
    row.title = f.title
    row.description = f.summary
    row.recommended_action = f.recommended_action
    row.is_emergency = f.is_emergency
    row.language = language
    row.evidence = {
        **f.evidence,
        "_why": f.why,
        "_disclaimer": f.disclaimer,
        "_context_notes": f.context_notes,
        "_source": f.source,
        "_source_url": f.source_url,
        "_variant": f.variant,
    }


def sync_findings(db: Session, user: User, evaluation: Evaluation, language: str) -> list[MedicalFinding]:
    current: dict[str, Finding] = {f.rule_code: f for f in evaluation.findings}
    if evaluation.alert is not None and evaluation.alert.rule_code == "MR-010":
        current["MR-010"] = evaluation.alert

    existing = db.scalars(select(MedicalFinding).where(MedicalFinding.user_id == user.id, MedicalFinding.status.in_(OPEN_STATUSES))).all()
    now = datetime.now(UTC)
    kept: dict[str, MedicalFinding] = {}
    for row in existing:
        new = current.get(row.rule_code)
        if new is None or new.severity.value != row.severity or row.rule_code in kept:
            row.status = FindingStatus.RESOLVED.value
            row.resolved_at = now
            continue
        # Same rule, same severity: refresh text/evidence, keep status (acknowledgement persists).
        _apply(row, new, language)
        kept[row.rule_code] = row

    for code, f in current.items():
        if code not in kept:
            row = MedicalFinding(user_id=user.id, status=FindingStatus.ACTIVE.value)
            _apply(row, f, language)
            db.add(row)
            kept[code] = row
    db.flush()
    return sorted(kept.values(), key=lambda r: -Severity(r.severity).rank)


def evaluate_and_sync(db: Session, user: User, data: UserHealthData | None = None) -> tuple[UserHealthData, Evaluation]:
    """Run the rules engine on fresh data and persist findings. Caller commits."""
    data = data or load_user_health_data(db, user)
    evaluation = data.evaluate()
    sync_findings(db, user, evaluation, data.language)
    return data, evaluation
