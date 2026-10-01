from datetime import date, timedelta

from app.schemas import CycleStat, UserContext
from app.services.rules_engine import compute_findings as _compute_findings, max_severity
from app.schemas import Severity

TODAY = date(2026, 9, 30)


def compute_findings(ctx):
    """التاريخ المرجعي ثابت = TODAY حتى لا تعتمد الاختبارات على ساعة الجهاز."""
    return _compute_findings(ctx, today=TODAY)


def _days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _ctx(cycles, avg=None, bleeding=None, starts=None, gaps=None):
    """`bleeding` = طول النزيف بالأيام، و`starts` = تواريخ البداية (ومنها تُحسب أطوال الدورات)."""
    if starts is None:
        starts = [_days_ago(5)] * len(bleeding or [])
    return UserContext(
        cycles_recorded=cycles,
        avg_cycle_days=avg,
        cycle_gaps=gaps or [],
        last_cycles=[CycleStat(start_date=d, length_days=l)
                     for d, l in zip(starts, bleeding or [None] * len(starts))],
    )


def test_insufficient_data():
    fs = compute_findings(_ctx(1))
    assert len(fs) == 1
    assert fs[0].rule_code == "INSUFFICIENT_DATA"
    assert fs[0].severity == Severity.MONITOR


def test_long_cycle_avg():
    fs = compute_findings(_ctx(6, avg=40, bleeding=[5, 5]))
    codes = {f.rule_code for f in fs}
    assert "LONG_CYCLE" in codes


def test_long_cycle_escalates_at_60():
    fs = compute_findings(_ctx(6, avg=65, bleeding=[5, 5]))
    f = next(x for x in fs if x.rule_code == "LONG_CYCLE")
    assert f.severity == Severity.MEDICAL_REVIEW


def test_irregular_high_spread():
    # أطوال الدورات من فروق التواريخ: 24، 42 → تفاوت 18
    fs = compute_findings(_ctx(6, avg=33, bleeding=[5, 5, 5],
                               starts=[_days_ago(66), _days_ago(42), _days_ago(0)],
                               gaps=[24, 42]))
    f = next(x for x in fs if x.rule_code == "IRREGULAR_CYCLE")
    assert f.severity == Severity.MEDICAL_REVIEW
    assert any("spread_days=18" in e for e in f.evidence)


def test_no_alert_pattern_when_normal():
    fs = compute_findings(_ctx(6, avg=28, bleeding=[5, 5],
                               starts=[_days_ago(56), _days_ago(28)], gaps=[28, 28]))
    assert [f.rule_code for f in fs] == ["NO_ALERT_PATTERN"]


def test_max_severity_ordering():
    fs = compute_findings(_ctx(6, avg=65, bleeding=[5, 5]))
    assert max_severity(fs) == Severity.MEDICAL_REVIEW


# ---------------------------------------------------------------------------
# قواعد مستخلصة من المسودة التثقيفية: طول النزيف، وتوقف الدورة.
# ---------------------------------------------------------------------------

def _bleeding(days, start_offset=5):
    return compute_findings(_ctx(1, bleeding=[days], starts=[_days_ago(start_offset)]))


def test_normal_bleeding_length_does_not_flag():
    codes = {f.rule_code for f in _bleeding(5)}
    assert "PROLONGED_BLEEDING" not in codes


def test_bleeding_over_seven_days_is_monitored():
    f = next(x for x in _bleeding(8) if x.rule_code == "PROLONGED_BLEEDING")
    assert f.severity == Severity.MONITOR
    assert any("longest_bleeding_days=8" in e for e in f.evidence)


def test_very_long_bleeding_escalates():
    f = next(x for x in _bleeding(14) if x.rule_code == "PROLONGED_BLEEDING")
    assert f.severity == Severity.MEDICAL_REVIEW


def test_bleeding_flag_works_with_a_single_record():
    """لا ننتظر 3 دورات لتنبيه على نزيف 12 يومًا: معلومة واحدة واضحة تكفي."""
    fs = _bleeding(12)
    codes = [f.rule_code for f in fs]
    assert codes[0] == "INSUFFICIENT_DATA"   # السجل ناقص للنمط
    assert "PROLONGED_BLEEDING" in codes      # لكن التنبيه يظهر


def test_missed_period_flags_after_90_days():
    fs = compute_findings(_ctx(4, avg=28, bleeding=[5], starts=[_days_ago(120)]))
    f = next(x for x in fs if x.rule_code == "MISSED_PERIOD")
    assert f.severity == Severity.MONITOR
    assert any("days_since_last_logged_bleeding=120" in e for e in f.evidence)
    # النص يفرّق بين «دورة متوقفة» و«سجل لم يُحدَّث» — لا ادّعاء تشخيصي
    assert any("لم يُحدَّث" in e for e in f.evidence)


def test_missed_period_does_not_flag_before_90_days():
    fs = compute_findings(_ctx(4, avg=28, bleeding=[5], starts=[_days_ago(60)]))
    assert "MISSED_PERIOD" not in {f.rule_code for f in fs}


# ---------------------------------------------------------------------------
# تراجع دلالي: طول النزيف ≠ طول الدورة. خلطهما كان ينتج تنبيهات كاذبة.
# ---------------------------------------------------------------------------

def test_bleeding_length_does_not_create_irregular_cycle():
    """نزيف طويل (9 أيام) مع دورات منتظمة تمامًا لا يجوز أن يُقرأ كعدم انتظام."""
    starts = [_days_ago(84), _days_ago(56), _days_ago(28), _days_ago(0)]
    fs = compute_findings(_ctx(4, avg=28, bleeding=[9, 9, 9, 9], starts=starts, gaps=[28, 28, 28]))
    codes = {f.rule_code for f in fs}
    assert "IRREGULAR_CYCLE" not in codes
    assert "PROLONGED_BLEEDING" in codes


def test_cycle_gaps_do_not_create_prolonged_bleeding():
    """دورات طويلة (45 يومًا) مع نزيف 4 أيام لا تعني نزيفًا طويلًا."""
    starts = [_days_ago(90), _days_ago(45), _days_ago(0)]
    fs = compute_findings(_ctx(3, avg=45, bleeding=[4, 4, 4], starts=starts, gaps=[45, 45]))
    codes = {f.rule_code for f in fs}
    assert "PROLONGED_BLEEDING" not in codes
    assert "LONG_CYCLE" in codes


def test_gaps_are_derived_from_dates_when_not_provided():
    """لو لم يُرسل الخادم cycle_gaps نحسبها من تواريخ البداية."""
    starts = [_days_ago(66), _days_ago(42), _days_ago(0)]  # 24 ثم 42
    fs = compute_findings(_ctx(3, avg=33, bleeding=[5, 5, 5], starts=starts))
    f = next(x for x in fs if x.rule_code == "IRREGULAR_CYCLE")
    assert any("spread_days=18" in e for e in f.evidence)


def test_duplicate_dates_are_ignored_not_zero_gap():
    """تاريخان متطابقان (إدخال مكرر) لا يُنتجان دورة بطول صفر."""
    starts = [_days_ago(28), _days_ago(28), _days_ago(0)]
    fs = compute_findings(_ctx(3, avg=28, bleeding=[5, 5, 5], starts=starts))
    assert "IRREGULAR_CYCLE" not in {f.rule_code for f in fs}
