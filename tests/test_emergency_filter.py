from app.schemas import Finding, Severity
from app.services.emergency_filter import (
    build_fixed_reply,
    check_findings,
    check_message,
    run_filter,
)


def test_medical_emergency_arabic():
    r = check_message("عملت نزيف شديد وجلطات كبيرة")
    assert r.kind == "medical"


def test_medical_emergency_english():
    r = check_message("I think I'm soaking through every hour")
    assert r.kind == "medical"


def test_crisis_dialect():
    r = check_message("مش عايزة أعيش خالص")
    assert r.kind == "crisis"


def test_crisis_english():
    r = check_message("i want to kill myself")
    assert r.kind == "crisis"


def test_benign_message_passes():
    r = check_message("تأخرت دوري 10 أيام، المفروض أعمل إيه؟")
    assert not r.triggered


def test_findings_urgent_triggers():
    f = [Finding(rule_code="X", severity=Severity.URGENT, title="t")]
    assert check_findings(f).kind == "medical"


def test_findings_normal_passes():
    f = [Finding(rule_code="X", severity=Severity.MONITOR, title="t")]
    assert not check_findings(f).triggered


def test_filter_precedence_message_over_findings():
    f = [Finding(rule_code="X", severity=Severity.EMERGENCY, title="t")]
    r = run_filter("مش عايزة أعيش", f)
    assert r.kind == "crisis" and r.source == "message"


def test_fixed_reply_contains_number_verbatim():
    r = check_message("بنزف بشكل شديد")
    reply = build_fixed_reply(r, emergency_number="123", crisis_line="")
    assert "123" in reply
    assert "الأعراض التي ذكرتِها قد تحتاج رعاية طبية عاجلة" in reply


def test_crisis_reply_contains_crisis_line():
    r = check_message("عايزة أنهي حياتي")
    reply = build_fixed_reply(r, emergency_number="123", crisis_line="0800-XXXX")
    assert "0800-XXXX" in reply
