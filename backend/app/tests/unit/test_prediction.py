"""PRD §62 — prediction testing."""

from datetime import date, timedelta

from app.services.cycle_engine import PeriodRecord
from app.services.prediction_engine import predict_next_period
from app.tests.factories import periods_from_lengths


def test_new_user_zero_history_is_unavailable():
    p = predict_next_period([], date(2026, 9, 27))
    assert p.available is False
    assert p.estimated_start is None
    assert "no_periods" in p.reason_codes


def test_one_period_without_reported_length_is_unavailable():
    p = predict_next_period([PeriodRecord(date(2026, 9, 1))], date(2026, 9, 27))
    assert p.available is False
    assert p.current_cycle_day == 27


def test_one_period_with_user_reported_length_is_low_confidence():
    p = predict_next_period([PeriodRecord(date(2026, 9, 1))], date(2026, 9, 10), typical_cycle_length=30)
    assert p.available and p.confidence == "low" and p.basis == "user_reported"
    assert p.estimated_start == date(2026, 10, 1)


def test_regular_cycles_high_confidence():
    periods, last = periods_from_lengths([28, 29, 28, 30, 29, 28])
    p = predict_next_period(periods, last + timedelta(days=5))
    assert p.available
    assert p.confidence == "high"
    assert p.estimated_cycle_length in (28, 29)
    assert p.window_end - p.window_start <= timedelta(days=6)
    assert p.is_estimate is True


def test_uses_median_not_28_default():
    periods, last = periods_from_lengths([35, 34, 35, 36, 35, 34])
    p = predict_next_period(periods, last + timedelta(days=5))
    assert p.estimated_cycle_length == 35


def test_irregular_cycles_low_confidence_wide_window():
    periods, last = periods_from_lengths([26, 42, 30, 38, 27, 41])
    p = predict_next_period(periods, last + timedelta(days=5))
    assert p.confidence == "low"
    assert "high_variability" in p.reason_codes
    assert (p.window_end - p.window_start).days >= 10


def test_missing_cycle_outlier_excluded():
    # A 58-day gap in otherwise ~29-day cycles is likely a missed log.
    periods, last = periods_from_lengths([29, 28, 58, 29, 30, 29])
    p = predict_next_period(periods, last + timedelta(days=3))
    assert "outliers_excluded" in p.reason_codes or p.estimated_cycle_length <= 30
    assert p.estimated_cycle_length <= 30


def test_contraception_change_excludes_older_cycles():
    periods, last = periods_from_lengths([40, 41, 39, 28, 28])
    change = periods[3].start  # cycles from here on are after the change
    p = predict_next_period(periods, last + timedelta(days=3), contraception_started_on=change)
    assert "contraception_changed" in p.reason_codes
    assert p.cycles_used == 2
    assert p.estimated_cycle_length == 28
    assert p.confidence == "low"


def test_unpredictable_contraception_caps_confidence():
    periods, last = periods_from_lengths([28, 28, 28, 28, 28, 28])
    p = predict_next_period(periods, last + timedelta(days=3), contraception="hormonal_ius")
    assert p.confidence == "low"
    assert "hormonal_contraception" in p.reason_codes


def test_late_status():
    periods, last = periods_from_lengths([28, 28, 28, 28, 28, 28])
    p = predict_next_period(periods, last + timedelta(days=40))
    assert p.status == "late"
    assert p.days_late == 12


def test_reason_text_bilingual():
    p = predict_next_period([], date(2026, 9, 27))
    assert p.to_dict("ar")["reason"].startswith("لم يتم")
    assert p.to_dict("en")["reason"].startswith("No periods")
