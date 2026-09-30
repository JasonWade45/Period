"""ترتيب مستويات الخطورة.

السبب في وجود هذا الملف: `Severity` يرث من `str` لتسهيل JSON، وهذا يجعل
المقارنات الافتراضية (`>=`, `<`) أبجدية لا ترتيبية، فتظهر أخطاء صامتة مثل
`needs_doctor=True` لنتيجة MONITOR. الاختبارات هنا تثبّت الترتيب الصحيح.
"""
from __future__ import annotations

import pytest

from app.schemas import SEVERITY_ORDER, Finding, Severity, max_severity_of
from app.services.rules_engine import compute_findings, max_severity
from app.schemas import UserContext


ALL = list(Severity)


def test_order_is_low_to_high():
    assert [s.value for s in SEVERITY_ORDER] == [
        "NORMAL", "MONITOR", "MEDICAL_REVIEW", "URGENT", "EMERGENCY",
    ]
    assert [s.rank for s in SEVERITY_ORDER] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("a", ALL)
@pytest.mark.parametrize("b", ALL)
def test_all_comparisons_follow_severity_not_alphabet(a, b):
    """كل أزواج المقارنة يجب أن تتبع rank، لا أسماء القيم."""
    assert (a < b) is (a.rank < b.rank)
    assert (a <= b) is (a.rank <= b.rank)
    assert (a > b) is (a.rank > b.rank)
    assert (a >= b) is (a.rank >= b.rank)
    assert a.at_least(b) is (a.rank >= b.rank)


def test_alphabetical_trap_is_gone():
    """هذه بالضبط الحالة التي أنتجت شارة «يستحق مراجعة طبية» خطأً."""
    assert not (Severity.MONITOR >= Severity.MEDICAL_REVIEW)      # "MONITOR" > "MEDICAL_REVIEW" أبجديًا
    assert not Severity.MONITOR.at_least(Severity.MEDICAL_REVIEW)
    assert Severity.EMERGENCY >= Severity.MEDICAL_REVIEW
    assert Severity.EMERGENCY.at_least(Severity.MEDICAL_REVIEW)
    assert not (Severity.NORMAL >= Severity.MONITOR)


def test_comparison_with_plain_strings_uses_severity_order():
    assert not (Severity.MONITOR >= "MEDICAL_REVIEW")
    assert Severity.EMERGENCY >= "MEDICAL_REVIEW"


def test_unknown_string_does_not_raise():
    assert (Severity.MONITOR >= "not-a-severity") is False or True  # لا انهيار
    with pytest.raises(ValueError):
        Severity.MONITOR.at_least("not-a-severity")


def test_max_severity_of_helpers_agree():
    findings = [
        Finding(rule_code="A", severity=Severity.MONITOR, title="t"),
        Finding(rule_code="B", severity=Severity.MEDICAL_REVIEW, title="t"),
        Finding(rule_code="C", severity=Severity.NORMAL, title="t"),
    ]
    assert max_severity(findings) == Severity.MEDICAL_REVIEW
    assert max_severity_of([f.severity for f in findings]) == Severity.MEDICAL_REVIEW
    assert max_severity([]) == Severity.NORMAL
    assert max_severity_of([]) == Severity.NORMAL


def test_insufficient_data_is_only_monitor():
    """أقل من 3 دورات = MONITOR، ولا يستدعي ادّعاء حاجة لمراجعة طبية."""
    findings = compute_findings(UserContext(cycles_recorded=0))
    assert [f.rule_code for f in findings] == ["INSUFFICIENT_DATA"]
    assert max_severity(findings) == Severity.MONITOR
    assert not max_severity(findings).at_least(Severity.MEDICAL_REVIEW)
