"""Load a user's records from the DB and normalize them for the pure engines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import ClotSize, FlowLevel, PregnancyTestResult
from app.models import BleedingLog, Cycle, HealthProfile, PregnancyTest, SymptomLog, User
from app.services import analytics_engine, prediction_engine
from app.services.cycle_engine import BleedingEntry, CycleView, PeriodRecord, SymptomEntry, build_cycle_views
from app.services.medical_rules import Evaluation, MedicalRulesEngine, PregnancyTestEntry, RulesInput


def user_today(user: User) -> date:
    """'Today' in the user's own timezone (a period logged at 1am Cairo time is still that day)."""
    try:
        tz = ZoneInfo(user.timezone or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def age_on(dob: date | None, today: date) -> int | None:
    if not dob:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


@dataclass
class UserHealthData:
    user: User
    profile: HealthProfile | None
    today: date
    periods: list[PeriodRecord]
    bleeding: list[BleedingEntry]
    symptoms: list[SymptomEntry]
    pregnancy_tests: list[PregnancyTestEntry]

    @property
    def language(self) -> str:
        return self.user.language or "ar"

    @property
    def age(self) -> int | None:
        return age_on(self.user.date_of_birth, self.today)

    @cached_property
    def views(self) -> list[CycleView]:
        return build_cycle_views([p for p in self.periods if p.start <= self.today], [b for b in self.bleeding if b.day <= self.today])

    @cached_property
    def lengths(self) -> list[int]:
        return [v.cycle_length for v in self.views if v.cycle_length is not None]

    def rules_input(self) -> RulesInput:
        p = self.profile
        return RulesInput(
            today=self.today,
            periods=self.periods,
            bleeding=self.bleeding,
            symptoms=self.symptoms,
            pregnancy_tests=self.pregnancy_tests,
            age=self.age,
            contraception=p.contraception_type if p else None,
            breastfeeding=p.breastfeeding if p else None,
            pregnancy_possibility=p.pregnancy_possibility if p else None,
            known_bleeding_disorder=p.known_bleeding_disorder if p else None,
            typical_cycle_length=p.typical_cycle_length if p else None,
        )

    def evaluate(self, language: str | None = None) -> Evaluation:
        return MedicalRulesEngine(language=language or self.language).evaluate(self.rules_input())

    def predict(self) -> prediction_engine.Prediction:
        p = self.profile
        durations = [v.period_days for v in self.views if v.period_days and v.end_is_explicit][-6:]
        return prediction_engine.predict_next_period(
            self.periods,
            self.today,
            period_durations=durations or None,
            typical_cycle_length=p.typical_cycle_length if p else None,
            typical_period_length=p.typical_period_length if p else None,
            contraception=p.contraception_type if p else None,
            contraception_started_on=p.contraception_started_on if p else None,
        )

    def cycle_stats(self) -> analytics_engine.CycleStats:
        return analytics_engine.cycle_stats(self.lengths)

    def period_stats(self) -> dict:
        return analytics_engine.period_stats(self.views, self.bleeding, self.today)

    def symptom_frequency(self) -> dict:
        return analytics_engine.symptom_frequency(self.views, self.symptoms, self.today)

    def history(self) -> list[dict]:
        return analytics_engine.cycle_history(self.views, self.bleeding, self.symptoms, self.today)


def load_user_health_data(db: Session, user: User) -> UserHealthData:
    uid = user.id
    cycles = db.scalars(select(Cycle).where(Cycle.user_id == uid).order_by(Cycle.start_date)).all()
    bleeding = db.scalars(select(BleedingLog).where(BleedingLog.user_id == uid).order_by(BleedingLog.log_date)).all()
    symptoms = db.scalars(select(SymptomLog).where(SymptomLog.user_id == uid).order_by(SymptomLog.log_date)).all()
    tests = db.scalars(select(PregnancyTest).where(PregnancyTest.user_id == uid).order_by(PregnancyTest.test_date)).all()
    profile = db.get(HealthProfile, uid)
    return UserHealthData(
        user=user,
        profile=profile,
        today=user_today(user),
        periods=[PeriodRecord(c.start_date, c.end_date) for c in cycles],
        bleeding=[
            BleedingEntry(
                day=b.log_date,
                flow=FlowLevel(b.flow_level),
                frequent_changes=b.frequent_changes,
                double_protection=b.double_protection,
                flooding=b.flooding,
                leakage=b.leakage,
                night_changes=b.night_changes,
                affects_daily_life=b.affects_daily_life,
                clot_size=ClotSize(b.clot_size) if b.clot_size else None,
                product_changes=b.product_changes,
            )
            for b in bleeding
        ],
        symptoms=[
            SymptomEntry(
                day=s.log_date,
                pain_level=s.pain_level,
                affects_daily_activity=s.affects_daily_activity,
                symptoms=frozenset(s.symptoms or []),
                pain_location=frozenset(s.pain_location or []),
            )
            for s in symptoms
        ],
        pregnancy_tests=[PregnancyTestEntry(t.test_date, PregnancyTestResult(t.result)) for t in tests],
    )
