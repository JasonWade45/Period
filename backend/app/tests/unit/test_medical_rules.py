"""Medical Rules Engine regression tests (PRD §61)."""

from datetime import date, timedelta

import pytest

from app.domain import ClotSize, FlowLevel, PregnancyTestResult, Severity
from app.services.cycle_engine import BleedingEntry, PeriodRecord, SymptomEntry
from app.services.medical_rules import MedicalRulesEngine, PregnancyTestEntry, RulesInput
from app.tests.factories import periods_from_lengths

TODAY = date(2026, 9, 27)


def evaluate(language="en", **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("pregnancy_possibility", "no")
    return MedicalRulesEngine(language=language).evaluate(RulesInput(**kw))


def codes(ev):
    return {f.rule_code: f.severity for f in ev.findings}


def recent_periods(lengths, days_after_last=5, **kw):
    """Periods ending so that the last one started `days_after_last` days before TODAY."""
    total = sum(lengths) + days_after_last
    return periods_from_lengths(lengths, start=TODAY - timedelta(days=total), **kw)[0]


def sym(days_ago, **kw):
    if "symptoms" in kw:
        kw["symptoms"] = frozenset(kw["symptoms"])
    if "pain_location" in kw:
        kw["pain_location"] = frozenset(kw["pain_location"])
    return SymptomEntry(TODAY - timedelta(days=days_ago), **kw)


def bleed(days_ago, flow=FlowLevel.MEDIUM, **kw):
    return BleedingEntry(TODAY - timedelta(days=days_ago), flow, **kw)


# ---------------------------------------------------------------- §61 examples


def test_spec_regular_cycles_normal():
    ev = evaluate(periods=recent_periods([28, 30, 29, 31]))
    assert ev.overall_severity == Severity.NORMAL
    assert ev.findings == []
    assert ev.alert is None


def test_spec_long_cycles_medical_review():
    ev = evaluate(periods=recent_periods([40, 42, 38, 41]))
    assert codes(ev)["MR-001"] == Severity.MEDICAL_REVIEW
    assert ev.overall_severity == Severity.MEDICAL_REVIEW
    f = next(f for f in ev.findings if f.rule_code == "MR-001")
    assert f.evidence["cycle_lengths"] == [40, 42, 38, 41]
    assert f.rule_version == "1.0" and f.content_version == "1.0"


def test_spec_bleeding_9_days_medical_review():
    periods = recent_periods([28, 29], period_days=9)
    ev = evaluate(periods=periods)
    assert codes(ev)["MR-002"] == Severity.MEDICAL_REVIEW
    assert ev.findings[0].evidence["prolonged_periods"][0]["days"] == 9


def test_spec_pregnancy_confirmed_heavy_bleeding_is_at_least_urgent():
    # §61 lists URGENT; §24 / NHS say heavy bleeding in pregnancy → EMERGENCY.
    # We implement the stricter NHS behaviour; either way it must be ≥ URGENT.
    ev = evaluate(
        periods=[PeriodRecord(TODAY - timedelta(days=50), TODAY - timedelta(days=46))],
        pregnancy_tests=[PregnancyTestEntry(TODAY - timedelta(days=10), PregnancyTestResult.POSITIVE)],
        bleeding=[bleed(0, FlowLevel.HEAVY)],
    )
    assert ev.overall_severity >= Severity.URGENT
    assert codes(ev)["MR-008"] == Severity.EMERGENCY
    assert ev.alert.is_emergency


# ---------------------------------------------------------------- MR-001


def test_mr001_single_isolated_cycle_is_not_a_medical_alert():
    ev = evaluate(periods=recent_periods([28, 29, 28, 30, 40]))
    assert codes(ev).get("MR-001") == Severity.MONITOR
    assert ev.overall_severity < Severity.MEDICAL_REVIEW


def test_mr001_short_cycles():
    ev = evaluate(periods=recent_periods([19, 20, 28, 18]))
    assert codes(ev)["MR-001"] == Severity.MEDICAL_REVIEW


def test_mr001_adolescent_wider_range():
    ev = evaluate(periods=recent_periods([40, 42, 38, 41]), age=16)
    assert "MR-001" not in codes(ev)


def test_mr001_hormonal_contraception_adds_context_note():
    ev = evaluate(periods=recent_periods([40, 42, 38, 41]), contraception="combined_pill")
    f = next(f for f in ev.findings if f.rule_code == "MR-001")
    assert f.severity == Severity.MEDICAL_REVIEW
    assert any("hormonal" in n for n in f.context_notes)


# ---------------------------------------------------------------- MR-002


def test_mr002_seven_days_is_not_prolonged():
    ev = evaluate(periods=recent_periods([28, 29], period_days=7))
    assert "MR-002" not in codes(ev)


def test_mr002_open_period_derived_from_logs():
    start = TODAY - timedelta(days=9)
    periods = [*recent_periods([28, 29], days_after_last=40), PeriodRecord(start)]
    logs = [BleedingEntry(start + timedelta(days=i), FlowLevel.LIGHT) for i in range(9)]
    ev = evaluate(periods=periods, bleeding=logs)
    f = next(f for f in ev.findings if f.rule_code == "MR-002")
    assert f.evidence["prolonged_periods"][0]["end_recorded"] is False
    assert f.context_notes  # derived_from_logs note


# ---------------------------------------------------------------- MR-003


@pytest.mark.parametrize(
    "entry",
    [
        dict(frequent_changes=True),
        dict(double_protection=True),
        dict(leakage=True),
        dict(clot_size=ClotSize.LARGE),
        dict(affects_daily_life=True),
    ],
)
def test_mr003_each_indicator_triggers_review(entry):
    ev = evaluate(periods=recent_periods([28, 28], days_after_last=10), bleeding=[bleed(10, FlowLevel.HEAVY, **entry)])
    assert codes(ev)["MR-003"] == Severity.MEDICAL_REVIEW


def test_mr003_heavy_label_alone_is_not_enough():
    ev = evaluate(periods=recent_periods([28, 28], days_after_last=10), bleeding=[bleed(10, FlowLevel.HEAVY)])
    assert "MR-003" not in codes(ev)


def test_mr003_heavy_with_fainting_is_emergency():
    ev = evaluate(
        periods=recent_periods([28, 28], days_after_last=1),
        bleeding=[bleed(0, FlowLevel.VERY_HEAVY, frequent_changes=True)],
        symptoms=[sym(0, symptoms={"fainting"})],
    )
    assert codes(ev)["MR-003"] == Severity.EMERGENCY


def test_mr003_heavy_with_dizziness_is_urgent():
    ev = evaluate(
        periods=recent_periods([28, 28], days_after_last=1),
        bleeding=[bleed(0, FlowLevel.HEAVY)],
        symptoms=[sym(0, symptoms={"dizziness"})],
    )
    assert codes(ev)["MR-003"] == Severity.URGENT


def test_mr003_old_dizziness_does_not_escalate():
    ev = evaluate(
        periods=recent_periods([28, 28], days_after_last=20),
        bleeding=[bleed(20, FlowLevel.HEAVY, leakage=True)],
        symptoms=[sym(20, symptoms={"dizziness"})],
    )
    assert codes(ev)["MR-003"] == Severity.MEDICAL_REVIEW


# ---------------------------------------------------------------- MR-004 / MR-005


def test_mr004_explicit_bleeding_between_periods():
    ev = evaluate(periods=recent_periods([28, 28], days_after_last=15), symptoms=[sym(3, symptoms={"bleeding_between_periods"})])
    assert codes(ev)["MR-004"] == Severity.MEDICAL_REVIEW


def test_mr004_derived_single_day_is_monitor_two_days_review():
    periods = recent_periods([28, 28], days_after_last=15)
    ev1 = evaluate(periods=periods, bleeding=[bleed(3, FlowLevel.SPOTTING)])
    assert codes(ev1)["MR-004"] == Severity.MONITOR
    ev2 = evaluate(periods=periods, bleeding=[bleed(3, FlowLevel.SPOTTING), bleed(6, FlowLevel.LIGHT)])
    assert codes(ev2)["MR-004"] == Severity.MEDICAL_REVIEW


def test_mr004_bleeding_during_period_not_flagged():
    periods = recent_periods([28, 28], days_after_last=2)  # last period covers days -2..+2
    ev = evaluate(periods=periods, bleeding=[bleed(2), bleed(1), bleed(0)])
    assert "MR-004" not in codes(ev)


def test_mr004_pre_period_spotting_not_flagged():
    periods = recent_periods([28, 28], days_after_last=2)
    ev = evaluate(periods=periods, bleeding=[bleed(3, FlowLevel.SPOTTING)])  # 1 day before start
    assert "MR-004" not in codes(ev)


def test_mr005_bleeding_after_sex():
    ev = evaluate(symptoms=[sym(5, symptoms={"bleeding_after_sex"})])
    assert codes(ev)["MR-005"] == Severity.MEDICAL_REVIEW


# ---------------------------------------------------------------- MR-006


def test_mr006_severe_pain_affecting_daily_activity_review():
    ev = evaluate(symptoms=[sym(20, pain_level=8, affects_daily_activity=True)])
    assert codes(ev)["MR-006"] == Severity.MEDICAL_REVIEW


def test_mr006_severe_pain_not_affecting_daily_life_no_finding():
    ev = evaluate(symptoms=[sym(20, pain_level=8)])
    assert "MR-006" not in codes(ev)


def test_mr006_moderate_pain_no_finding():
    ev = evaluate(symptoms=[sym(1, pain_level=5, affects_daily_activity=True)])
    assert "MR-006" not in codes(ev)


def test_mr006_painkillers_not_helping_urgent():
    ev = evaluate(symptoms=[sym(0, pain_level=9, symptoms={"painkillers_not_helping"})])
    assert codes(ev)["MR-006"] == Severity.URGENT


def test_mr006_severe_pain_with_fainting_emergency():
    ev = evaluate(symptoms=[sym(0, pain_level=9, symptoms={"fainting"})])
    assert codes(ev)["MR-006"] == Severity.EMERGENCY
    assert ev.alert.is_emergency


def test_mr006_severe_pain_with_possible_pregnancy_urgent_and_sudden_emergency():
    ev = evaluate(symptoms=[sym(0, pain_level=8)], pregnancy_possibility="unsure")
    assert codes(ev)["MR-006"] == Severity.URGENT
    ev2 = evaluate(
        symptoms=[sym(0, pain_level=8, symptoms={"sudden_severe_pain"}, pain_location={"one_sided"})], pregnancy_possibility="yes"
    )
    assert codes(ev2)["MR-006"] == Severity.EMERGENCY


def test_mr006_old_red_flags_do_not_trigger_emergency():
    ev = evaluate(symptoms=[sym(30, pain_level=9, symptoms={"fainting"}, affects_daily_activity=True)])
    assert codes(ev)["MR-006"] == Severity.MEDICAL_REVIEW


# ---------------------------------------------------------------- MR-007


def test_mr007_three_missed_periods_review():
    periods = recent_periods([28, 28, 28, 28], days_after_last=100)
    ev = evaluate(periods=periods)
    f = next(f for f in ev.findings if f.rule_code == "MR-007")
    assert f.severity == Severity.MEDICAL_REVIEW
    assert f.evidence["estimated_missed_periods"] >= 3


def test_mr007_one_late_period_monitor():
    periods = recent_periods([28, 28, 28, 28], days_after_last=40)
    assert codes(evaluate(periods=periods))["MR-007"] == Severity.MONITOR


def test_mr007_not_late_no_finding():
    periods = recent_periods([28, 28, 28, 28], days_after_last=30)
    assert "MR-007" not in codes(evaluate(periods=periods))


def test_mr007_breastfeeding_downgrades():
    periods = recent_periods([28, 28, 28, 28], days_after_last=100)
    f = next(f for f in evaluate(periods=periods, breastfeeding=True).findings if f.rule_code == "MR-007")
    assert f.severity == Severity.MONITOR
    assert any("breastfeeding" in n or "ترضع" in n for n in f.context_notes)


def test_mr007_hormonal_contraception_downgrades():
    periods = recent_periods([28, 28, 28, 28], days_after_last=100)
    ev = evaluate(periods=periods, contraception="injection")
    assert codes(ev)["MR-007"] == Severity.MONITOR


def test_mr007_never_infers_pregnancy_only_suggests_test_when_possible():
    periods = recent_periods([28, 28, 28, 28], days_after_last=45)
    f = next(f for f in evaluate(periods=periods, pregnancy_possibility="yes").findings if f.rule_code == "MR-007")
    assert "pregnant" not in f.title.lower()
    assert any("pregnancy test" in n for n in f.context_notes)
    f2 = next(f for f in evaluate(periods=periods, pregnancy_possibility="no").findings if f.rule_code == "MR-007")
    assert not any("pregnancy test" in n for n in f2.context_notes)


def test_mr007_skipped_when_pregnancy_confirmed():
    periods = recent_periods([28, 28], days_after_last=100)
    ev = evaluate(periods=periods, pregnancy_tests=[PregnancyTestEntry(TODAY - timedelta(days=60), PregnancyTestResult.POSITIVE)])
    assert "MR-007" not in codes(ev)


# ---------------------------------------------------------------- MR-008


def _pregnant(**kw):
    return dict(
        periods=[PeriodRecord(TODAY - timedelta(days=60), TODAY - timedelta(days=56))],
        pregnancy_tests=[PregnancyTestEntry(TODAY - timedelta(days=30), PregnancyTestResult.POSITIVE)],
        **kw,
    )


def test_mr008_light_bleeding_in_pregnancy_urgent():
    ev = evaluate(**_pregnant(bleeding=[bleed(1, FlowLevel.SPOTTING)]))
    assert codes(ev)["MR-008"] == Severity.URGENT


@pytest.mark.parametrize("flag", ["fainting", "dizziness", "shoulder_tip_pain", "loss_of_consciousness", "nausea", "vomiting"])
def test_mr008_bleeding_with_red_flags_emergency(flag):
    ev = evaluate(**_pregnant(bleeding=[bleed(0, FlowLevel.LIGHT)], symptoms=[sym(0, symptoms={flag})]))
    assert codes(ev)["MR-008"] == Severity.EMERGENCY


def test_mr008_bleeding_with_severe_pain_emergency():
    ev = evaluate(**_pregnant(bleeding=[bleed(0, FlowLevel.LIGHT)], symptoms=[sym(0, pain_level=8)]))
    assert codes(ev)["MR-008"] == Severity.EMERGENCY


def test_mr008_older_bleeding_in_pregnancy_review():
    ev = evaluate(**_pregnant(bleeding=[bleed(14, FlowLevel.SPOTTING)]))
    assert codes(ev)["MR-008"] == Severity.MEDICAL_REVIEW


def test_mr008_negative_test_after_positive_cancels_pregnancy():
    kw = _pregnant(bleeding=[bleed(1, FlowLevel.MEDIUM)])
    kw["pregnancy_tests"].append(PregnancyTestEntry(TODAY - timedelta(days=5), PregnancyTestResult.NEGATIVE))
    assert "MR-008" not in codes(evaluate(**kw))


def test_mr008_user_reported_confirmed_pregnancy():
    ev = evaluate(bleeding=[bleed(0, FlowLevel.SPOTTING)], pregnancy_possibility="confirmed")
    assert codes(ev)["MR-008"] == Severity.URGENT


def test_mr008_does_not_fire_without_pregnancy_even_if_period_late():
    periods = recent_periods([28, 28], days_after_last=50)
    ev = evaluate(periods=periods, bleeding=[bleed(0, FlowLevel.HEAVY)], pregnancy_possibility="unsure")
    assert "MR-008" not in codes(ev)


def test_mr008_stale_positive_test_superseded_by_periods():
    periods = recent_periods([28, 28], days_after_last=5)
    ev = evaluate(
        periods=periods,
        pregnancy_tests=[PregnancyTestEntry(TODAY - timedelta(days=120), PregnancyTestResult.POSITIVE)],
        bleeding=[bleed(4)],
    )
    assert "MR-008" not in codes(ev)
    assert "positive_pregnancy_test_superseded_by_logged_periods" in ev.data_gaps


# ---------------------------------------------------------------- MR-009


def test_mr009_single_change_monitor():
    ev = evaluate(periods=recent_periods([28, 29, 28, 29, 28, 20]))
    assert codes(ev)["MR-009"] == Severity.MONITOR


def test_mr009_persistent_change_review():
    ev = evaluate(periods=recent_periods([26, 27, 26, 27, 26, 34, 35]))
    assert codes(ev)["MR-009"] == Severity.MEDICAL_REVIEW
    assert "MR-001" not in codes(ev)  # 34/35 still inside 21–35


def test_mr009_needs_enough_baseline():
    ev = evaluate(periods=recent_periods([28, 20]))
    assert "MR-009" not in codes(ev)


# ---------------------------------------------------------------- MR-010


def test_mr010_consolidates_multiple_findings_into_one_alert():
    periods = recent_periods([40, 42, 38, 41], days_after_last=10)
    ev = evaluate(
        periods=periods,
        bleeding=[bleed(10, FlowLevel.HEAVY, frequent_changes=True)],
        symptoms=[sym(10, pain_level=8, affects_daily_activity=True)],
    )
    assert {"MR-001", "MR-003", "MR-006"} <= set(codes(ev))
    assert ev.alert.rule_code == "MR-010"
    assert ev.alert.severity == Severity.MEDICAL_REVIEW
    assert set(ev.alert.evidence["component_rule_codes"]) >= {"MR-001", "MR-003", "MR-006"}


def test_mr010_emergency_takes_top_severity():
    ev = evaluate(
        periods=recent_periods([40, 42, 38, 41], days_after_last=1),
        symptoms=[sym(0, pain_level=10, symptoms={"fainting"})],
    )
    assert ev.alert.severity == Severity.EMERGENCY
    assert ev.overall_severity == Severity.EMERGENCY


def test_single_finding_alert_is_that_finding():
    ev = evaluate(symptoms=[sym(5, symptoms={"bleeding_after_sex"})])
    assert ev.alert.rule_code == "MR-005"


# ---------------------------------------------------------------- MR-011


def test_mr011_repeated_associated_pain():
    ev = evaluate(symptoms=[sym(3, symptoms={"pain_during_sex"}), sym(20, symptoms={"pain_urinating"})])
    assert codes(ev)["MR-011"] == Severity.MEDICAL_REVIEW


# ---------------------------------------------------------------- engine-wide


def test_deterministic_same_input_same_output():
    kw = dict(periods=recent_periods([40, 42, 38, 41]), symptoms=[sym(3, pain_level=8, affects_daily_activity=True)])
    assert evaluate(**kw).to_dict() == evaluate(**kw).to_dict()


def test_every_finding_explains_itself():
    ev = evaluate(
        periods=recent_periods([40, 42, 38, 41], period_days=9, days_after_last=10),
        bleeding=[bleed(10, FlowLevel.HEAVY, leakage=True)],
        symptoms=[sym(3, symptoms={"bleeding_after_sex", "bleeding_between_periods"})],
    )
    assert len(ev.findings) >= 4
    for f in ev.findings:
        assert f.title and f.summary and f.why and f.recommended_action and f.disclaimer
        assert f.evidence
        assert "{" not in f.summary and "{" not in f.title  # all placeholders filled


def test_arabic_content():
    ev = evaluate(language="ar", periods=recent_periods([40, 42, 38, 41]))
    f = ev.findings[0]
    assert f.title == "دوراتك كانت غير منتظمة"
    assert "40، 42، 38، 41" in f.summary
    assert "تشخيص" in f.disclaimer


def test_no_diagnostic_language_in_any_content():
    import json

    from app.services.medical_rules import MEDICAL_DIR

    text = (MEDICAL_DIR / "content.json").read_text(encoding="utf-8").lower()
    for banned in (
        "you have pcos",
        "you have endometriosis",
        "you are pregnant",
        "you are not pregnant",
        "nothing to worry about",
        "you are fine",
    ):
        assert banned not in text
    json.loads(text)


def test_disabled_rule_does_not_fire():
    from copy import deepcopy

    from app.services.medical_rules import load_rules_config

    cfg = deepcopy(load_rules_config())
    cfg["rules"]["MR-005"]["enabled"] = False
    ev = MedicalRulesEngine(config=cfg, language="en").evaluate(RulesInput(today=TODAY, symptoms=[sym(5, symptoms={"bleeding_after_sex"})]))
    assert ev.findings == []


def test_future_dated_logs_are_ignored():
    ev = evaluate(symptoms=[SymptomEntry(TODAY + timedelta(days=3), pain_level=10, symptoms=frozenset({"fainting"}))])
    assert ev.findings == []
