"""Deterministic cycle / period / symptom statistics."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date

from app.domain import FlowLevel, pain_category
from app.services.cycle_engine import BleedingEntry, CycleView, SymptomEntry

# Range (max-min) of recent cycle lengths used to label variability.
# FIGO describes a shortest-to-longest variation of ≤7–9 days as regular.
LOW_VARIABILITY_MAX_RANGE = 7
HIGH_VARIABILITY_MIN_RANGE = 20
VARIABILITY_WINDOW = 6
MIN_CYCLES_FOR_PATTERN = 3


def median(values: list[int] | list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _round(v: float | None, nd: int = 1) -> float | None:
    return round(v, nd) if v is not None else None


def _recent_mean(values: list[int], n: int) -> float | None:
    if len(values) < n:
        return None
    return _round(statistics.fmean(values[-n:]))


@dataclass
class CycleStats:
    cycle_count: int
    mean: float | None
    median: float | None
    min: int | None
    max: int | None
    std_dev: float | None
    recent_3_mean: float | None
    recent_6_mean: float | None
    recent_12_mean: float | None
    variability: str  # low | moderate | high | insufficient_data
    pattern: str  # regular | variable | insufficient_data
    recent_range: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def classify_variability(lengths: list[int]) -> tuple[str, str, int | None]:
    recent = lengths[-VARIABILITY_WINDOW:]
    if len(recent) < MIN_CYCLES_FOR_PATTERN:
        return "insufficient_data", "insufficient_data", None
    spread = max(recent) - min(recent)
    if spread <= LOW_VARIABILITY_MAX_RANGE:
        return "low", "regular", spread
    if spread >= HIGH_VARIABILITY_MIN_RANGE:
        return "high", "variable", spread
    return "moderate", "variable", spread


def cycle_stats(lengths: list[int]) -> CycleStats:
    variability, pattern, spread = classify_variability(lengths)
    return CycleStats(
        cycle_count=len(lengths),
        mean=_round(statistics.fmean(lengths)) if lengths else None,
        median=median(lengths),
        min=min(lengths) if lengths else None,
        max=max(lengths) if lengths else None,
        std_dev=_round(statistics.stdev(lengths)) if len(lengths) >= 2 else None,
        recent_3_mean=_recent_mean(lengths, 3),
        recent_6_mean=_recent_mean(lengths, 6),
        recent_12_mean=_recent_mean(lengths, 12),
        variability=variability,
        pattern=pattern,
        recent_range=spread,
    )


def _entries_in(view: CycleView, entries, today: date):
    """Entries whose day falls in [cycle start, next cycle start)."""
    end = view.next_start or date.max
    return [e for e in entries if view.start <= e.day < end and e.day <= today]


def period_stats(views: list[CycleView], bleeding: list[BleedingEntry], today: date) -> dict:
    durations = [v.period_days for v in views if v.period_days]
    max_flow_per_period = []
    for v in views:
        period_end = v.end or v.start
        flows = [b.flow.score for b in bleeding if v.start <= b.day <= period_end and b.is_bleeding]
        if flows:
            max_flow_per_period.append(max(flows))
    avg_flow = statistics.fmean(max_flow_per_period) if max_flow_per_period else None
    return {
        "periods_with_duration": len(durations),
        "mean_duration": _round(statistics.fmean(durations)) if durations else None,
        "median_duration": median(durations),
        "max_duration": max(durations) if durations else None,
        "min_duration": min(durations) if durations else None,
        "average_peak_flow_score": _round(avg_flow),
        "average_peak_flow": _score_to_flow(avg_flow),
    }


def _score_to_flow(score: float | None) -> str | None:
    if score is None:
        return None
    nearest = min(FlowLevel, key=lambda f: abs(f.score - score))
    return nearest.value


def symptom_frequency(views: list[CycleView], symptoms: list[SymptomEntry], today: date) -> dict:
    """How many cycles each symptom appeared in (e.g. 'headache: 5/8 cycles')."""
    counted = [v for v in views if v.start <= today]
    per_symptom: Counter[str] = Counter()
    severe_pain_cycles = 0
    for v in counted:
        entries = _entries_in(v, symptoms, today)
        seen = set()
        for e in entries:
            seen.update(e.symptoms)
        per_symptom.update(seen)
        if any(pain_category(e.pain_level) == "severe" for e in entries):
            severe_pain_cycles += 1
    total = len(counted)
    return {
        "cycles_counted": total,
        "symptoms": [{"symptom": s, "cycles": n, "total_cycles": total, "label": f"{n}/{total}"} for s, n in per_symptom.most_common()],
        "severe_pain_cycles": severe_pain_cycles,
    }


def cycle_history(views: list[CycleView], bleeding: list[BleedingEntry], symptoms: list[SymptomEntry], today: date) -> list[dict]:
    """Table rows for PRD §13 (Cycle | Start | End | Length | Bleeding | Pain)."""
    rows = []
    for v in views:
        period_end = v.end or v.start
        flows = [b.flow for b in bleeding if v.start <= b.day <= period_end and b.is_bleeding]
        pains = [e.pain_level for e in _entries_in(v, symptoms, today) if e.pain_level is not None]
        rows.append(
            {
                "index": v.index,
                "start": v.start.isoformat(),
                "end": v.end.isoformat() if v.end else None,
                "end_is_explicit": v.end_is_explicit,
                "period_days": v.period_days,
                "cycle_length": v.cycle_length,
                "is_current": v.next_start is None,
                "peak_flow": max(flows, key=lambda f: f.score).value if flows else None,
                "max_pain_level": max(pains) if pains else None,
                "max_pain": pain_category(max(pains)) if pains else None,
            }
        )
    return rows
