from app.schemas import CycleStat, UserContext
from app.services.rules_engine import compute_findings, max_severity
from app.schemas import Severity


def _ctx(cycles, avg=None, lengths=None):
    return UserContext(
        cycles_recorded=cycles,
        avg_cycle_days=avg,
        last_cycles=[CycleStat(start_date="2026-01-01", length_days=l)
                     for l in (lengths or [])],
    )


def test_insufficient_data():
    fs = compute_findings(_ctx(1))
    assert len(fs) == 1
    assert fs[0].rule_code == "INSUFFICIENT_DATA"
    assert fs[0].severity == Severity.MONITOR


def test_long_cycle_avg():
    fs = compute_findings(_ctx(6, avg=40, lengths=[38, 42]))
    codes = {f.rule_code for f in fs}
    assert "LONG_CYCLE" in codes


def test_long_cycle_escalates_at_60():
    fs = compute_findings(_ctx(6, avg=65, lengths=[60, 70]))
    f = next(x for x in fs if x.rule_code == "LONG_CYCLE")
    assert f.severity == Severity.MEDICAL_REVIEW


def test_irregular_high_spread():
    fs = compute_findings(_ctx(6, avg=30, lengths=[24, 42]))
    f = next(x for x in fs if x.rule_code == "IRREGULAR_CYCLE")
    assert f.severity == Severity.MEDICAL_REVIEW
    assert any("spread_days=18" in e for e in f.evidence)


def test_no_alert_pattern_when_normal():
    fs = compute_findings(_ctx(6, avg=28, lengths=[28, 29]))
    assert [f.rule_code for f in fs] == ["NO_ALERT_PATTERN"]


def test_max_severity_ordering():
    fs = compute_findings(_ctx(6, avg=65, lengths=[60, 70]))
    assert max_severity(fs) == Severity.MEDICAL_REVIEW
