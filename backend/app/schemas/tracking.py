from datetime import date, datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain import (
    ClotSize,
    Contraception,
    FlowLevel,
    PainLocation,
    PregnancyPossibility,
    PregnancyTestResult,
    ProductType,
    Symptom,
)

T = TypeVar("T")

MAX_BACKDATE_YEARS = 5


def _not_future(d: date | None, today: date | None = None) -> None:
    # Allow +1 day to tolerate client/server timezone differences.
    if d is not None and (d - (today or date.today())).days > 1:
        raise ValueError("Date cannot be in the future")


class MedicalSnapshot(BaseModel):
    """Returned with every health-data write so urgent findings surface immediately (PRD P4)."""

    overall_severity: str
    requires_immediate_attention: bool
    alert: dict[str, Any] | None


class MutationResult(BaseModel, Generic[T]):
    data: T
    medical: MedicalSnapshot


# ---------------------------------------------------------------- health profile


class HealthProfileIn(BaseModel):
    contraception_type: Contraception | None = None
    contraception_started_on: date | None = None
    breastfeeding: bool | None = None
    pregnancy_possibility: PregnancyPossibility | None = None
    typical_cycle_length: int | None = Field(default=None, ge=10, le=120)
    typical_period_length: int | None = Field(default=None, ge=1, le=20)
    known_pcos: bool | None = None
    known_endometriosis: bool | None = None
    known_thyroid_condition: bool | None = None
    known_bleeding_disorder: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dates(self):
        _not_future(self.contraception_started_on)
        return self


class HealthProfileOut(HealthProfileIn):
    model_config = ConfigDict(from_attributes=True)
    updated_at: datetime | None = None


# ---------------------------------------------------------------- cycles (periods)


class CycleIn(BaseModel):
    start_date: date
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        _not_future(self.start_date)
        _not_future(self.end_date)
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.end_date and (self.end_date - self.start_date).days > 60:
            raise ValueError("A single period cannot be longer than 60 days — please check the dates")
        return self


class CyclePatch(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class CycleComplete(BaseModel):
    end_date: date


class CycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    start_date: date
    end_date: date | None
    duration_days: int | None
    source: str
    notes: str | None
    cycle_length: int | None = None  # derived: days until next period start
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- bleeding


class BleedingIn(BaseModel):
    log_date: date
    flow_level: FlowLevel
    product_type: ProductType | None = None
    product_changes: int | None = Field(default=None, ge=0, le=100)
    frequent_changes: bool = Field(default=False, description="Changing product every 1–2 hours")
    double_protection: bool = Field(default=False, description="Using two products together")
    flooding: bool = False
    leakage: bool = Field(default=False, description="Bled through clothes or bedding")
    night_changes: bool = False
    affects_daily_life: bool = False
    clot_size: ClotSize | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        _not_future(self.log_date)
        return self


class BleedingPatch(BaseModel):
    flow_level: FlowLevel | None = None
    product_type: ProductType | None = None
    product_changes: int | None = Field(default=None, ge=0, le=100)
    frequent_changes: bool | None = None
    double_protection: bool | None = None
    flooding: bool | None = None
    leakage: bool | None = None
    night_changes: bool | None = None
    affects_daily_life: bool | None = None
    clot_size: ClotSize | None = None
    notes: str | None = Field(default=None, max_length=2000)


class BleedingOut(BleedingIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cycle_id: UUID | None
    created_at: datetime


# ---------------------------------------------------------------- symptoms


class SymptomIn(BaseModel):
    log_date: date
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_location: list[PainLocation] = Field(default_factory=list, max_length=10)
    affects_daily_activity: bool = False
    symptoms: list[Symptom] = Field(default_factory=list, max_length=40)
    mood: str | None = Field(default=None, max_length=50)
    energy_level: int | None = Field(default=None, ge=0, le=10)
    sleep_quality: int | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        _not_future(self.log_date)
        self.symptoms = list(dict.fromkeys(self.symptoms))
        self.pain_location = list(dict.fromkeys(self.pain_location))
        return self


class SymptomPatch(BaseModel):
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_location: list[PainLocation] | None = None
    affects_daily_activity: bool | None = None
    symptoms: list[Symptom] | None = None
    mood: str | None = Field(default=None, max_length=50)
    energy_level: int | None = Field(default=None, ge=0, le=10)
    sleep_quality: int | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=2000)


class SymptomOut(SymptomIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


# ---------------------------------------------------------------- medications & pregnancy tests


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dosage: str | None = Field(default=None, max_length=120)
    frequency: str | None = Field(default=None, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class MedicationOut(MedicationIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class PregnancyTestIn(BaseModel):
    test_date: date
    result: PregnancyTestResult
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        _not_future(self.test_date)
        return self


class PregnancyTestOut(PregnancyTestIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
