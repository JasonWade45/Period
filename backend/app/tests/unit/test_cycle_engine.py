from datetime import date, timedelta

from app.domain import FlowLevel
from app.services.cycle_engine import (
    BleedingEntry,
    PeriodRecord,
    build_cycle_views,
    cycle_day,
    cycle_lengths,
    validate_no_overlap,
)


def test_cycle_length_is_start_to_next_start():
    # PRD §6 example: Jan 1 → Jan 30 = 29 days
    periods = [PeriodRecord(date(2026, 1, 1), date(2026, 1, 6)), PeriodRecord(date(2026, 1, 30))]
    assert cycle_lengths(periods) == [29]


def test_cycle_length_ignores_last_bleeding_day():
    a = [PeriodRecord(date(2026, 1, 1), date(2026, 1, 3)), PeriodRecord(date(2026, 1, 30))]
    b = [PeriodRecord(date(2026, 1, 1), date(2026, 1, 9)), PeriodRecord(date(2026, 1, 30))]
    assert cycle_lengths(a) == cycle_lengths(b) == [29]


def test_cycle_lengths_sorted_regardless_of_input_order():
    periods = [PeriodRecord(date(2026, 3, 5)), PeriodRecord(date(2026, 1, 1)), PeriodRecord(date(2026, 1, 30))]
    assert cycle_lengths(periods) == [29, 34]


def test_cycle_day_one_is_first_bleeding_day():
    assert cycle_day(date(2026, 9, 1), date(2026, 9, 1)) == 1
    assert cycle_day(date(2026, 9, 1), date(2026, 9, 24)) == 24


def test_open_period_end_derived_from_contiguous_logs():
    start = date(2026, 5, 1)
    logs = [BleedingEntry(start + timedelta(days=i), FlowLevel.MEDIUM) for i in range(6)]
    logs.append(BleedingEntry(start + timedelta(days=15), FlowLevel.SPOTTING))  # not contiguous
    views = build_cycle_views([PeriodRecord(start)], logs)
    assert views[0].end == start + timedelta(days=5)
    assert views[0].period_days == 6
    assert views[0].end_is_explicit is False


def test_open_period_without_logs_has_unknown_duration():
    views = build_cycle_views([PeriodRecord(date(2026, 5, 1))], [])
    assert views[0].period_days is None


def test_overlap_validation():
    assert validate_no_overlap([PeriodRecord(date(2026, 1, 1), date(2026, 1, 10)), PeriodRecord(date(2026, 1, 8))])
    assert validate_no_overlap([PeriodRecord(date(2026, 1, 1), date(2026, 1, 5)), PeriodRecord(date(2026, 1, 20))]) is None
