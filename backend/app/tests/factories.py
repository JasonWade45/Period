"""Helpers for building engine inputs in tests."""

from datetime import date, timedelta

from app.services.cycle_engine import PeriodRecord

START = date(2026, 1, 1)


def periods_from_lengths(lengths: list[int], start: date = START, period_days: int = 5, close_last: bool = True):
    """Build periods whose consecutive starts are `lengths` apart.

    Returns (periods, last_start). len(periods) == len(lengths) + 1.
    """
    periods = []
    d = start
    for n in lengths:
        periods.append(PeriodRecord(d, d + timedelta(days=period_days - 1)))
        d += timedelta(days=n)
    periods.append(PeriodRecord(d, d + timedelta(days=period_days - 1) if close_last else None))
    return periods, d
