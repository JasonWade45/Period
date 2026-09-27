"""Next-period prediction (PRD §15).

Not `last_period + 28`. Uses the user's own history:
* median of up to the last 6 valid cycles (robust to one odd cycle),
* a small shift toward the last 3 cycles when they consistently trend one way,
* a window whose width grows with historical variability,
* a confidence level that drops when cycles vary a lot.

Never manufactures a prediction without any data. Predictions are always
labelled as estimates.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from app.domain import UNPREDICTABLE_BLEEDING_CONTRACEPTION, value_of
from app.services.cycle_engine import PeriodRecord, sort_periods

WINDOW = 6
TREND_WINDOW = 3
TREND_MIN_SHIFT_DAYS = 3
MIN_WINDOW_HALF_WIDTH = 2
MAX_WINDOW_HALF_WIDTH = 10
DEFAULT_HALF_WIDTH_LIMITED = 5
DEFAULT_PERIOD_LENGTH = 5
MIN_PLAUSIBLE_CYCLE = 10
OUTLIER_FACTOR = 1.8  # cycle > 1.8× median is likely a missed log / missed period
OUTLIER_MIN_DAYS = 60

REASONS = {
    "no_periods": {
        "en": "No periods recorded yet. We need a few cycles before predictions become useful.",
        "ar": "لم يتم تسجيل أي دورة بعد. نحتاج إلى بضع دورات قبل أن تصبح التوقعات مفيدة.",
    },
    "insufficient_history": {
        "en": "Only one period recorded. Log your next period start to enable an estimate.",
        "ar": "تم تسجيل دورة واحدة فقط. سجّلي بداية الدورة القادمة لنتمكن من تقديم تقدير.",
    },
    "user_reported": {
        "en": "Based only on the average cycle length you reported — not yet on recorded cycles.",
        "ar": "مبني فقط على متوسط طول الدورة الذي أدخلتِه، وليس على دورات مسجلة بعد.",
    },
    "limited_history": {
        "en": "Based on fewer than 3 recorded cycles, so this estimate is uncertain.",
        "ar": "مبني على أقل من 3 دورات مسجلة، لذلك هذا التقدير غير مؤكد.",
    },
    "high_variability": {
        "en": "Your recorded cycles vary considerably, so the estimated window is wide.",
        "ar": "دوراتك المسجلة تختلف في طولها بشكل ملحوظ، لذلك نافذة التوقع واسعة.",
    },
    "moderate_variability": {
        "en": "Your recorded cycles vary somewhat.",
        "ar": "دوراتك المسجلة تختلف في طولها إلى حد ما.",
    },
    "consistent_history": {
        "en": "Your recorded cycles have been fairly consistent.",
        "ar": "دوراتك المسجلة كانت متقاربة نسبيًا.",
    },
    "hormonal_contraception": {
        "en": "Some hormonal contraception can make bleeding irregular or stop it, so calendar predictions are less reliable.",
        "ar": "بعض وسائل منع الحمل الهرمونية قد تجعل النزيف غير منتظم أو توقفه، لذلك التوقعات أقل دقة.",
    },
    "contraception_changed": {
        "en": "Cycles recorded before your contraception change were excluded.",
        "ar": "تم استبعاد الدورات المسجلة قبل تغيير وسيلة منع الحمل.",
    },
    "outliers_excluded": {
        "en": "Some unusually long gaps were excluded — they may be missed logs or missed periods.",
        "ar": "تم استبعاد بعض الفترات الطويلة بشكل غير معتاد، فقد تكون دورات لم تُسجل أو دورات لم تحدث.",
    },
}


@dataclass
class Prediction:
    available: bool
    estimated_start: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    estimated_end: date | None = None
    estimated_cycle_length: int | None = None
    confidence: str | None = None  # high | medium | low
    basis: str | None = None  # history | limited_history | user_reported
    status: str | None = None  # upcoming | in_window | late
    days_late: int | None = None
    current_cycle_day: int | None = None
    cycles_used: int = 0
    reason_codes: list[str] = field(default_factory=list)
    is_estimate: bool = True

    def to_dict(self, language: str = "en") -> dict:
        d = asdict(self)
        for k in ("estimated_start", "window_start", "window_end", "estimated_end"):
            d[k] = d[k].isoformat() if d[k] else None
        lang = language if language in ("ar", "en") else "en"
        d["reasons"] = [REASONS[c][lang] for c in self.reason_codes]
        d["reason"] = d["reasons"][0] if d["reasons"] else None
        return d


def _valid_lengths(periods: list[PeriodRecord], contraception_started_on: date | None) -> tuple[list[int], list[str]]:
    ordered = sort_periods(periods)
    reasons: list[str] = []
    pairs = list(zip(ordered, ordered[1:], strict=False))
    if contraception_started_on:
        kept = [(a, b) for a, b in pairs if a.start >= contraception_started_on]
        if len(kept) < len(pairs):
            reasons.append("contraception_changed")
        pairs = kept
    lengths = [(b.start - a.start).days for a, b in pairs]
    lengths = [n for n in lengths if n >= MIN_PLAUSIBLE_CYCLE]
    if len(lengths) >= 3:
        provisional = statistics.median(lengths)
        filtered = [n for n in lengths if not (n > OUTLIER_FACTOR * provisional and n >= OUTLIER_MIN_DAYS)]
        if len(filtered) < len(lengths):
            reasons.append("outliers_excluded")
        lengths = filtered
    return lengths, reasons


def predict_next_period(
    periods: list[PeriodRecord],
    today: date,
    *,
    period_durations: list[int] | None = None,
    typical_cycle_length: int | None = None,
    typical_period_length: int | None = None,
    contraception: str | None = None,
    contraception_started_on: date | None = None,
) -> Prediction:
    ordered = sort_periods(periods)
    if not ordered:
        return Prediction(available=False, reason_codes=["no_periods"])

    last_start = ordered[-1].start
    current_day = (today - last_start).days + 1
    lengths, reasons = _valid_lengths(ordered, contraception_started_on)
    recent = lengths[-WINDOW:]

    if not recent:
        if not typical_cycle_length:
            return Prediction(available=False, current_cycle_day=current_day, reason_codes=["insufficient_history", *reasons])
        estimate, half_width, confidence, basis = typical_cycle_length, DEFAULT_HALF_WIDTH_LIMITED, "low", "user_reported"
        reasons.insert(0, "user_reported")
    elif len(recent) < 3:
        estimate = round(statistics.median(recent))
        half_width, confidence, basis = DEFAULT_HALF_WIDTH_LIMITED, "low", "limited_history"
        if len(recent) == 2:
            half_width = max(DEFAULT_HALF_WIDTH_LIMITED, math.ceil(abs(recent[1] - recent[0]) / 2))
        reasons.insert(0, "limited_history")
    else:
        base = statistics.median(recent)
        last3 = recent[-TREND_WINDOW:]
        if len(recent) >= WINDOW and (
            all(n - base >= TREND_MIN_SHIFT_DAYS for n in last3) or all(base - n >= TREND_MIN_SHIFT_DAYS for n in last3)
        ):
            base = (base + statistics.median(last3)) / 2
        estimate = round(base)
        sd = statistics.stdev(recent)
        half_width = min(MAX_WINDOW_HALF_WIDTH, max(MIN_WINDOW_HALF_WIDTH, math.ceil(sd)))
        basis = "history"
        if len(recent) >= WINDOW and sd <= 3:
            confidence = "high"
            reasons.insert(0, "consistent_history")
        elif sd <= 6:
            confidence = "medium"
            reasons.insert(0, "consistent_history" if sd <= 3 else "moderate_variability")
        else:
            confidence = "low"
            reasons.insert(0, "high_variability")

    if value_of(contraception) in UNPREDICTABLE_BLEEDING_CONTRACEPTION:
        confidence = "low"
        reasons.append("hormonal_contraception")

    period_len = round(statistics.median(period_durations)) if period_durations else (typical_period_length or DEFAULT_PERIOD_LENGTH)
    est_start = last_start + timedelta(days=estimate)
    w_start = est_start - timedelta(days=half_width)
    w_end = est_start + timedelta(days=half_width)

    if today > w_end:
        status, days_late = "late", (today - est_start).days
    elif today >= w_start:
        status, days_late = "in_window", None
    else:
        status, days_late = "upcoming", None

    return Prediction(
        available=True,
        estimated_start=est_start,
        window_start=w_start,
        window_end=w_end,
        estimated_end=est_start + timedelta(days=period_len - 1),
        estimated_cycle_length=estimate,
        confidence=confidence,
        basis=basis,
        status=status,
        days_late=days_late,
        current_cycle_day=current_day,
        cycles_used=len(recent),
        reason_codes=reasons,
    )
