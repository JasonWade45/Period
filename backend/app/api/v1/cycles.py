from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, get_owned, medical_snapshot
from app.domain import FlowLevel, Symptom
from app.models import BleedingLog, Cycle, SymptomLog
from app.schemas.tracking import CycleComplete, CycleIn, CycleOut, CyclePatch, MutationResult
from app.services.cycle_engine import PeriodRecord, validate_no_overlap

router = APIRouter(prefix="/cycles", tags=["cycles"])


class CycleStartIn(CycleIn):
    """'Start Period' (PRD §7). Optional quick fields create same-day logs in one request."""

    flow_level: FlowLevel | None = None
    pain_level: int | None = Field(default=None, ge=0, le=10)
    symptoms: list[Symptom] = Field(default_factory=list, max_length=40)


def _duration(c: Cycle) -> int | None:
    return (c.end_date - c.start_date).days + 1 if c.end_date else None


def _check_overlap(db, user_id, candidate_id: uuid.UUID | None, start: date, end: date | None) -> None:
    others = db.scalars(select(Cycle).where(Cycle.user_id == user_id)).all()
    periods = [PeriodRecord(c.start_date, c.end_date) for c in others if c.id != candidate_id]
    periods.append(PeriodRecord(start, end))
    err = validate_no_overlap(periods)
    if err:
        raise HTTPException(status_code=409, detail=err)


def _with_lengths(db, user_id, rows: list[Cycle]) -> list[CycleOut]:
    starts = sorted(db.scalars(select(Cycle.start_date).where(Cycle.user_id == user_id)).all())
    nxt = {a: (b - a).days for a, b in zip(starts, starts[1:], strict=False)}
    return [CycleOut.model_validate(c).model_copy(update={"cycle_length": nxt.get(c.start_date)}) for c in rows]


@router.get("", response_model=list[CycleOut])
def list_cycles(
    user: CurrentUser,
    db: DB,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    q = select(Cycle).where(Cycle.user_id == user.id)
    if from_:
        q = q.where(Cycle.start_date >= from_)
    if to:
        q = q.where(Cycle.start_date <= to)
    rows = db.scalars(q.order_by(Cycle.start_date.desc()).limit(limit).offset(offset)).all()
    return _with_lengths(db, user.id, list(rows))


@router.get("/{cycle_id}", response_model=CycleOut)
def get_cycle(cycle_id: uuid.UUID, user: CurrentUser, db: DB):
    return _with_lengths(db, user.id, [get_owned(db, Cycle, cycle_id, user)])[0]


@router.post("", response_model=MutationResult[CycleOut], status_code=status.HTTP_201_CREATED)
def create_cycle(body: CycleStartIn, user: CurrentUser, db: DB):
    _check_overlap(db, user.id, None, body.start_date, body.end_date)
    c = Cycle(user_id=user.id, start_date=body.start_date, end_date=body.end_date, notes=body.notes)
    c.duration_days = _duration(c)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A period already starts on this date") from None

    if body.flow_level is not None:
        existing = db.scalar(select(BleedingLog).where(BleedingLog.user_id == user.id, BleedingLog.log_date == body.start_date))
        if existing:
            existing.flow_level, existing.cycle_id = body.flow_level.value, c.id
        else:
            db.add(BleedingLog(user_id=user.id, cycle_id=c.id, log_date=body.start_date, flow_level=body.flow_level.value))
    if body.pain_level is not None or body.symptoms:
        db.add(SymptomLog(user_id=user.id, log_date=body.start_date, pain_level=body.pain_level, symptoms=[s.value for s in body.symptoms]))
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=_with_lengths(db, user.id, [c])[0], medical=snap)


@router.patch("/{cycle_id}", response_model=MutationResult[CycleOut])
def patch_cycle(cycle_id: uuid.UUID, body: CyclePatch, user: CurrentUser, db: DB):
    c = get_owned(db, Cycle, cycle_id, user)
    data = body.model_dump(exclude_unset=True)
    start = data.get("start_date", c.start_date)
    end = data.get("end_date", c.end_date)
    if start is None:
        raise HTTPException(status_code=422, detail="start_date cannot be null")
    try:
        CycleIn(start_date=start, end_date=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _check_overlap(db, user.id, c.id, start, end)
    for k, v in data.items():
        setattr(c, k, v)
    c.duration_days = _duration(c)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=_with_lengths(db, user.id, [c])[0], medical=snap)


@router.post("/{cycle_id}/complete", response_model=MutationResult[CycleOut])
def complete_cycle(cycle_id: uuid.UUID, body: CycleComplete, user: CurrentUser, db: DB):
    """Mark the period as ended ('End Period')."""
    return patch_cycle(cycle_id, CyclePatch(end_date=body.end_date), user, db)


@router.delete("/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cycle(cycle_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    c = get_owned(db, Cycle, cycle_id, user)
    db.delete(c)
    db.flush()
    medical_snapshot(db, user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
