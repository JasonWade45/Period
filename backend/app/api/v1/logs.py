"""Bleeding, symptom, medication and pregnancy-test logs."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, get_owned, medical_snapshot
from app.models import BleedingLog, Cycle, Medication, PregnancyTest, SymptomLog
from app.schemas.tracking import (
    BleedingIn,
    BleedingOut,
    BleedingPatch,
    MedicationIn,
    MedicationOut,
    MutationResult,
    PregnancyTestIn,
    PregnancyTestOut,
    SymptomIn,
    SymptomOut,
    SymptomPatch,
)

bleeding_router = APIRouter(prefix="/bleeding", tags=["bleeding"])
symptoms_router = APIRouter(prefix="/symptoms", tags=["symptoms"])
medications_router = APIRouter(prefix="/medications", tags=["medications"])
pregnancy_router = APIRouter(prefix="/pregnancy-tests", tags=["pregnancy"])


def _values(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [x.value if hasattr(x, "value") else x for x in v]
        else:
            out[k] = v.value if hasattr(v, "value") else v
    return out


def _range(q, col, from_: date | None, to: date | None):
    if from_:
        q = q.where(col >= from_)
    if to:
        q = q.where(col <= to)
    return q


def _cycle_for(db, user_id, day: date) -> uuid.UUID | None:
    return db.scalar(select(Cycle.id).where(Cycle.user_id == user_id, Cycle.start_date <= day).order_by(Cycle.start_date.desc()).limit(1))


# ---------------------------------------------------------------- bleeding


@bleeding_router.get("", response_model=list[BleedingOut])
def list_bleeding(
    user: CurrentUser, db: DB, from_: date | None = Query(None, alias="from"), to: date | None = None, limit: int = Query(200, le=1000)
):
    q = _range(select(BleedingLog).where(BleedingLog.user_id == user.id), BleedingLog.log_date, from_, to)
    return db.scalars(q.order_by(BleedingLog.log_date.desc()).limit(limit)).all()


@bleeding_router.post("", response_model=MutationResult[BleedingOut], status_code=status.HTTP_201_CREATED)
def create_bleeding(body: BleedingIn, user: CurrentUser, db: DB):
    row = BleedingLog(user_id=user.id, cycle_id=_cycle_for(db, user.id, body.log_date), **_values(body.model_dump()))
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A bleeding log already exists for this date — update it instead") from None
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=BleedingOut.model_validate(row), medical=snap)


@bleeding_router.patch("/{log_id}", response_model=MutationResult[BleedingOut])
def patch_bleeding(log_id: uuid.UUID, body: BleedingPatch, user: CurrentUser, db: DB):
    row = get_owned(db, BleedingLog, log_id, user)
    data = _values(body.model_dump(exclude_unset=True))
    if "flow_level" in data and data["flow_level"] is None:
        raise HTTPException(status_code=422, detail="flow_level cannot be null")
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=BleedingOut.model_validate(row), medical=snap)


@bleeding_router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bleeding(log_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    db.delete(get_owned(db, BleedingLog, log_id, user))
    db.flush()
    medical_snapshot(db, user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- symptoms


@symptoms_router.get("", response_model=list[SymptomOut])
def list_symptoms(
    user: CurrentUser, db: DB, from_: date | None = Query(None, alias="from"), to: date | None = None, limit: int = Query(200, le=1000)
):
    q = _range(select(SymptomLog).where(SymptomLog.user_id == user.id), SymptomLog.log_date, from_, to)
    return db.scalars(q.order_by(SymptomLog.log_date.desc(), SymptomLog.created_at.desc()).limit(limit)).all()


@symptoms_router.post("", response_model=MutationResult[SymptomOut], status_code=status.HTTP_201_CREATED)
def create_symptom(body: SymptomIn, user: CurrentUser, db: DB):
    row = SymptomLog(user_id=user.id, **_values(body.model_dump()))
    db.add(row)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=SymptomOut.model_validate(row), medical=snap)


@symptoms_router.patch("/{log_id}", response_model=MutationResult[SymptomOut])
def patch_symptom(log_id: uuid.UUID, body: SymptomPatch, user: CurrentUser, db: DB):
    row = get_owned(db, SymptomLog, log_id, user)
    for k, v in _values(body.model_dump(exclude_unset=True)).items():
        if k in ("symptoms", "pain_location") and v is None:
            v = []
        setattr(row, k, v)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=SymptomOut.model_validate(row), medical=snap)


@symptoms_router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_symptom(log_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    db.delete(get_owned(db, SymptomLog, log_id, user))
    db.flush()
    medical_snapshot(db, user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- medications


@medications_router.get("", response_model=list[MedicationOut])
def list_medications(user: CurrentUser, db: DB):
    return db.scalars(select(Medication).where(Medication.user_id == user.id).order_by(Medication.created_at.desc())).all()


@medications_router.post("", response_model=MedicationOut, status_code=status.HTTP_201_CREATED)
def create_medication(body: MedicationIn, user: CurrentUser, db: DB):
    row = Medication(user_id=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@medications_router.patch("/{med_id}", response_model=MedicationOut)
def patch_medication(med_id: uuid.UUID, body: MedicationIn, user: CurrentUser, db: DB):
    row = get_owned(db, Medication, med_id, user)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@medications_router.delete("/{med_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(med_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    db.delete(get_owned(db, Medication, med_id, user))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- pregnancy tests


@pregnancy_router.get("", response_model=list[PregnancyTestOut])
def list_tests(user: CurrentUser, db: DB):
    return db.scalars(select(PregnancyTest).where(PregnancyTest.user_id == user.id).order_by(PregnancyTest.test_date.desc())).all()


@pregnancy_router.post("", response_model=MutationResult[PregnancyTestOut], status_code=status.HTTP_201_CREATED)
def create_test(body: PregnancyTestIn, user: CurrentUser, db: DB):
    row = PregnancyTest(user_id=user.id, **_values(body.model_dump()))
    db.add(row)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    return MutationResult(data=PregnancyTestOut.model_validate(row), medical=snap)


@pregnancy_router.delete("/{test_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test(test_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    db.delete(get_owned(db, PregnancyTest, test_id, user))
    db.flush()
    medical_snapshot(db, user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
