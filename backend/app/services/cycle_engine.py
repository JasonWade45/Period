"""Pure cycle math. No database access, fully deterministic.

Definitions (PRD §6):
* Cycle Day 1 = first day of menstrual bleeding.
* Cycle length = next_period_start - current_period_start.
  It is never computed from the last bleeding day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain import ClotSize, FlowLevel

# Bleeding logs separated by at most this many days still belong to the same run.
CONTIGUITY_GAP_DAYS = 1


@dataclass(frozen=True)
class PeriodRecord:
    start: date
    end: date | None = None


@dataclass(frozen=True)
class BleedingEntry:
    day: date
    flow: FlowLevel
    frequent_changes: bool = False
    double_protection: bool = False
    flooding: bool = False
    leakage: bool = False
    night_changes: bool = False
    affects_daily_life: bool = False
    clot_size: ClotSize | None = None
    product_changes: int | None = None

    @property
    def is_bleeding(self) -> bool:
        return self.flow != FlowLevel.NONE


@dataclass(frozen=True)
class SymptomEntry:
    day: date
    pain_level: int | None = None
    affects_daily_activity: bool = False
    symptoms: frozenset[str] = field(default_factory=frozenset)
    pain_location: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CycleView:
    """One period plus the cycle it starts."""

    index: int
    start: date
    end: date | None  # effective end (explicit, or derived from contiguous logs)
    end_is_explicit: bool
    period_days: int | None
    cycle_length: int | None  # None for the current (open) cycle
    next_start: date | None


def sort_periods(periods: list[PeriodRecord]) -> list[PeriodRecord]:
    return sorted(periods, key=lambda p: p.start)


def cycle_lengths(periods: list[PeriodRecord]) -> list[int]:
    """Lengths of completed cycles, oldest → newest."""
    starts = [p.start for p in sort_periods(periods)]
    return [(b - a).days for a, b in zip(starts, starts[1:], strict=False)]


def cycle_day(last_period_start: date, today: date) -> int:
    return (today - last_period_start).days + 1


def _contiguous_bleeding_end(start: date, bleeding_days: set[date], limit: date | None) -> date | None:
    """Last day of the bleeding run that begins on/right after `start`."""
    if not bleeding_days:
        return None
    # The run must begin within the gap tolerance of the period start.
    first = next((start + timedelta(days=i) for i in range(CONTIGUITY_GAP_DAYS + 1) if start + timedelta(days=i) in bleeding_days), None)
    if first is None:
        return None
    last = first
    cursor = first
    while True:
        nxt = None
        for step in range(1, CONTIGUITY_GAP_DAYS + 2):
            candidate = cursor + timedelta(days=step)
            if limit is not None and candidate >= limit:
                break
            if candidate in bleeding_days:
                nxt = candidate
                break
        if nxt is None:
            return last
        last = cursor = nxt


def build_cycle_views(periods: list[PeriodRecord], bleeding: list[BleedingEntry] | None = None) -> list[CycleView]:
    """Combine periods (and optionally bleeding logs) into per-cycle views."""
    ordered = sort_periods(periods)
    bleeding_days = {b.day for b in (bleeding or []) if b.is_bleeding}
    views: list[CycleView] = []
    for i, p in enumerate(ordered):
        next_start = ordered[i + 1].start if i + 1 < len(ordered) else None
        if p.end is not None:
            end, explicit = p.end, True
        else:
            end, explicit = _contiguous_bleeding_end(p.start, bleeding_days, next_start), False
        period_days = (end - p.start).days + 1 if end else None
        views.append(
            CycleView(
                index=i + 1,
                start=p.start,
                end=end,
                end_is_explicit=explicit,
                period_days=period_days,
                cycle_length=(next_start - p.start).days if next_start else None,
                next_start=next_start,
            )
        )
    return views


def period_windows(views: list[CycleView], today: date) -> list[tuple[date, date]]:
    """Date ranges considered 'during a period' for intermenstrual detection.

    For an open period without logs we only count the start day, so that bleeding
    logged later is not silently absorbed into an unknown-length period.
    """
    windows = []
    for v in views:
        end = v.end or v.start
        windows.append((v.start, min(end, today) if end >= v.start else v.start))
    return windows


def validate_no_overlap(periods: list[PeriodRecord]) -> str | None:
    """Return an error message if periods overlap, else None."""
    ordered = sort_periods(periods)
    for a, b in zip(ordered, ordered[1:], strict=False):
        if a.start == b.start:
            return f"Two periods start on {a.start.isoformat()}."
        if a.end is not None and a.end >= b.start:
            return f"Period starting {a.start.isoformat()} overlaps the period starting {b.start.isoformat()}."
    return None
