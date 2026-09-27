from datetime import date, timedelta

from app.domain import FlowLevel
from app.services.analytics_engine import cycle_stats, period_stats, symptom_frequency
from app.services.cycle_engine import BleedingEntry, SymptomEntry, build_cycle_views
from app.tests.factories import periods_from_lengths


def test_cycle_stats_basic():
    s = cycle_stats([29, 34, 41, 27, 38, 31])
    assert s.cycle_count == 6
    assert s.median == 32.5
    assert s.min == 27 and s.max == 41
    assert s.mean == 33.3
    assert s.recent_3_mean == round((27 + 38 + 31) / 3, 1)
    assert s.variability == "moderate"  # range 14
    assert s.pattern == "variable"


def test_regular_cycles_low_variability():
    s = cycle_stats([28, 30, 29, 31])
    assert s.variability == "low" and s.pattern == "regular"


def test_high_variability():
    assert cycle_stats([26, 48, 30, 45]).variability == "high"


def test_insufficient_data():
    s = cycle_stats([29])
    assert s.pattern == "insufficient_data"
    assert s.std_dev is None


def test_empty():
    s = cycle_stats([])
    assert s.cycle_count == 0 and s.mean is None


def test_symptom_frequency_counts_cycles_not_days():
    periods, last = periods_from_lengths([28, 28])
    views = build_cycle_views(periods)
    symptoms = [
        SymptomEntry(periods[0].start, symptoms=frozenset({"headache"})),
        SymptomEntry(periods[0].start + timedelta(days=1), symptoms=frozenset({"headache"})),  # same cycle
        SymptomEntry(periods[1].start, symptoms=frozenset({"headache", "bloating"}), pain_level=8),
    ]
    freq = symptom_frequency(views, symptoms, last + timedelta(days=3))
    by = {s["symptom"]: s for s in freq["symptoms"]}
    assert by["headache"]["label"] == "2/3"
    assert by["bloating"]["label"] == "1/3"
    assert freq["severe_pain_cycles"] == 1


def test_period_stats():
    periods, _ = periods_from_lengths([28, 28], period_days=5)
    bleeding = [BleedingEntry(periods[0].start, FlowLevel.HEAVY), BleedingEntry(periods[1].start, FlowLevel.MEDIUM)]
    views = build_cycle_views(periods, bleeding)
    st = period_stats(views, bleeding, date(2026, 12, 1))
    assert st["mean_duration"] == 5
    assert st["average_peak_flow_score"] == 3.5
