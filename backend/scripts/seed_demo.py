"""Seed a demo account with realistic, variable cycles (matches the PRD dashboard example).

    python -m scripts.seed_demo

Demo login: demo@cyclecare.app / demo-password-123 (Arabic UI). Local development only.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select

from app.core.database import get_db
from app.core.security import hash_password
from app.models import BleedingLog, Cycle, HealthProfile, SymptomLog, User, UserSettings
from app.services.findings_service import evaluate_and_sync
from app.services.health_data import user_today

DEMO_EMAIL = "demo@cyclecare.app"
DEMO_PASSWORD = "demo-password-123"  # noqa: S105 - local demo only
LENGTHS = [29, 34, 41, 27, 38, 31]  # PRD §11 example
FLOWS = ["medium", "heavy", "heavy", "medium", "light"]


def main() -> None:
    db = next(get_db())
    existing = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if existing:
        db.execute(delete(User).where(User.id == existing.id))
        db.commit()

    user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), display_name="Demo", timezone="Africa/Cairo", language="ar")
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id))
    db.add(HealthProfile(user_id=user.id, contraception_type="none", pregnancy_possibility="no", breastfeeding=False))

    today = user_today(user)
    start = today - timedelta(days=sum(LENGTHS) + 23)  # → today is cycle day 24
    d = start
    for i, n in enumerate([*LENGTHS, None]):
        period_days = 5 if i % 3 else 6
        db.add(Cycle(user_id=user.id, start_date=d, end_date=d + timedelta(days=period_days - 1), duration_days=period_days))
        for k in range(period_days):
            db.add(BleedingLog(user_id=user.id, log_date=d + timedelta(days=k), flow_level=FLOWS[min(k, len(FLOWS) - 1)]))
        db.add(SymptomLog(user_id=user.id, log_date=d, pain_level=6 if i % 2 else 4, symptoms=["bloating", "fatigue"], pain_location=["lower_abdomen"]))
        db.add(SymptomLog(user_id=user.id, log_date=d + timedelta(days=1), pain_level=5, symptoms=["headache"]))
        if n:
            d += timedelta(days=n)
    db.flush()
    _, ev = evaluate_and_sync(db, user)
    db.commit()
    print(f"Seeded {DEMO_EMAIL} / {DEMO_PASSWORD} — overall severity: {ev.overall_severity.value}")


if __name__ == "__main__":
    main()
