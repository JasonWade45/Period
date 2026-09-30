"""اختيار رقم الطوارئ حسب البلد + سلوك «غير مُتحقق منه»."""
from __future__ import annotations

import pytest

import app.main as main
from app.services.emergency_filter import (
    FilterResult,
    build_fixed_reply,
    ensure_emergency_text,
)
from app.services.emergency_numbers import EmergencyInfo, EmergencyNumbers

EN = EmergencyNumbers()


def test_known_country_with_verified_number():
    info = EN.lookup("EG")
    assert info.number == "123"
    assert info.verified is True
    assert info.country_name == "مصر"
    assert info.source  # كل رقم له مصدر مذكور


def test_country_code_is_case_insensitive():
    assert EN.lookup("eg").number == EN.lookup("EG").number


@pytest.mark.parametrize("code", ["SA", "AE", "KW", "QA", "BH", "OM", "JO", "MA", "TN", "DZ",
                                  "SD", "IQ", "SY", "LB", "YE", "LY", "PS", "TR", "GB", "US",
                                  "DE", "FR"])
def test_every_listed_country_has_a_source(code):
    """قاعدة المشروع: لا رقم بلا مصدر. اختبار يحمي الجدول من الإضافة العشوائية."""
    info = EN.lookup(code)
    assert info.number, f"{code} بلا رقم"
    assert info.source, f"{code} بلا مصدر"
    assert info.verified is True, f"{code} بلا verified_at"


def test_unknown_country_uses_generic_number_and_says_so():
    info = EN.lookup("ZZ")
    assert info.verified is False
    assert info.number  # رقم عام (112)
    assert "غير مُتحقق" in info.source or "غير مُتحقق" in build_fixed_reply(
        FilterResult(kind="medical"), info)


def test_override_wins_over_table():
    """EMERGENCY_NUMBER الصريح يتقدّم على جدول البلد."""
    info = EN.lookup("EG", override_number="999")
    assert info.number == "999"


def test_override_crisis_line_wins():
    info = EN.lookup("EG", override_crisis_line="0800-123")
    assert info.crisis_line == "0800-123"


def test_crisis_line_is_empty_rather_than_invented():
    """لا نخترع خطوط دعم نفسي: الجدول لا يحتوي أي crisis_line حاليًا."""
    assert all(EN.lookup(c).crisis_line == "" for c in EN.known_countries)


# ----------------------------------------------------------------- رد ثابت

def test_unverified_number_reply_adds_explicit_caveat():
    info = EmergencyInfo(number="112", country_code="ZZ", country_name="ZZ",
                         verified=False, crisis_line="")
    reply = build_fixed_reply(FilterResult(kind="medical"), info)
    assert "112" in reply
    assert "غير مُتحقق منه" in reply


def test_verified_number_reply_has_no_caveat():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    reply = build_fixed_reply(FilterResult(kind="medical"), info)
    assert "123" in reply
    assert "غير مُتحقق منه" not in reply


def test_crisis_reply_admits_missing_crisis_line():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    reply = build_fixed_reply(FilterResult(kind="crisis"), info)
    assert "خط دعم" in reply and "لا يتوفّر" in reply
    assert "123" in reply  # رقم الإسعاف يبقى مذكورًا


def test_crisis_reply_includes_verified_line_when_present():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="0800-XXXX")
    reply = build_fixed_reply(FilterResult(kind="crisis"), info)
    assert "0800-XXXX" in reply


# ------------------------------- ضمان الرقم حين يعلن الموديل طوارئ فاتت الفلتر

def test_ensure_emergency_text_prepends_number_when_missing():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    out = ensure_emergency_text("يجب مراجعة طبيبة قريبًا.", info, crisis=False)
    assert "123" in out
    assert "يجب مراجعة طبيبة قريبًا." in out


def test_ensure_emergency_text_keeps_answer_when_number_present():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    original = "توجهي للطوارئ على 123 الآن."
    assert ensure_emergency_text(original, info, crisis=False) == original


def test_ensure_emergency_text_handles_empty_answer():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    out = ensure_emergency_text("", info, crisis=False)
    assert "123" in out and "رعاية طبية عاجلة" in out


def test_ensure_emergency_text_crisis_uses_crisis_wording():
    info = EmergencyInfo(number="123", country_code="EG", country_name="مصر",
                         verified=True, crisis_line="")
    out = ensure_emergency_text("كلام عام", info, crisis=True)
    assert "إيذاء النفس" in out


# ---------------------------------------------------------------------
# تراجع: القيمة الافتراضية لـ EMERGENCY_NUMBER لا يجوز أن تتقدّم على جدول
# البلد، وإلا صار اختيار البلد بلا أثر وعادت كل الطلبات رقم بلد واحد.
# ---------------------------------------------------------------------
def test_emergency_reply_uses_country_table(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    body = client.post("/v1/chat", json={
        "message": "بنزف كل ساعة وبرمي جلطات كبيرة",
        "country_code": "AE",
    }).json()
    assert "998" in body["answer"]
    assert "123" not in body["answer"]


@pytest.mark.parametrize("code,number", [
    ("SA", "997"), ("AE", "998"), ("KW", "112"), ("JO", "911"), ("GB", "999"), ("US", "911"),
])
def test_each_country_gets_its_own_number(client, monkeypatch, code, number):
    monkeypatch.setattr(main, "_llm", None)
    body = client.post("/v1/chat", json={
        "message": "ألم شديد في الصدر", "country_code": code,
    }).json()
    assert number in body["answer"], f"{code} لم يحصل على {number}"


def test_unknown_country_gets_generic_number_with_caveat(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    body = client.post("/v1/chat", json={
        "message": "بنزف كل ساعة وبرمي جلطات كبيرة", "country_code": "ZZ",
    }).json()
    assert "112" in body["answer"]           # الرقم العام
    assert "غير مُتحقق منه" in body["answer"]  # مع تنبيه صريح


def test_explicit_operator_override_still_wins(client, monkeypatch):
    """لو ضبط المشغّل EMERGENCY_NUMBER صراحةً فهو المتقدّم على الجدول."""
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_llm", None)
    monkeypatch.setattr(main_mod, "_emergency_override", lambda: "999")

    body = client.post("/v1/chat", json={
        "message": "بنزف كل ساعة وبرمي جلطات كبيرة", "country_code": "AE",
    }).json()
    assert "999" in body["answer"]


def test_meta_lists_countries_with_numbers_and_verification(client):
    body = client.get("/v1/meta").json()
    codes = {c["code"] for c in body["countries"]}
    assert {"EG", "SA", "AE", "GB", "US"} <= codes
    assert all(c["emergency"] for c in body["countries"])
    assert body["default_country"] == main.settings.country_code


# ---------------------------------------------------------------------------
# الأزمة النفسية نصابها أشد: الجملة المعيارية والرقم معًا.
# خلل سابق: وجود الرقم وحده كان يُسقط جملة الدعم ونصيحة «لا تكوني وحدها»،
# ووجود الجملة وحدها كان يُسقط الرقم.
# ---------------------------------------------------------------------------

def _info(number="123", crisis_line=""):
    return EmergencyInfo(number=number, country_code="EG", country_name="مصر",
                         verified=True, crisis_line=crisis_line)


def test_crisis_answer_with_number_but_no_header_gets_the_header():
    out = ensure_emergency_text("اتصلي بالإسعاف على 123 فورًا.", _info(), crisis=True)
    assert "إيذاء النفس" in out
    assert "123" in out
    assert "اتصلي بالإسعاف على 123 فورًا." in out


def test_crisis_answer_with_header_but_no_number_gets_the_number():
    text = "ما تشهدينه من أفكار إيذاء النفس يستحق دعمًا فوريًا. أنتِ لست وحدك."
    out = ensure_emergency_text(text, _info(), crisis=True)
    assert "الإسعاف: 123" in out
    assert out.count("إيذاء النفس") == 1        # بلا تكرار الجملة


def test_complete_crisis_answer_is_untouched():
    text = ("ما تشهدينه من أفكار إيذاء النفس يستحق دعمًا فوريًا\n"
            "تواصلي فورًا مع شخص تثق به.\nالإسعاف: 123")
    assert ensure_emergency_text(text, _info(), crisis=True) == text


def test_medical_answer_with_number_is_untouched():
    """الطوارئ الطبية: ذكر الرقم كافٍ — لا نُثقل ردًّا موجَّهًا فعلًا."""
    text = "توجهي لأقرب طوارئ أو اتصلي بـ 123، ولا تنتظري."
    assert ensure_emergency_text(text, _info(), crisis=False) == text


def test_crisis_without_number_configured_still_gets_header():
    out = ensure_emergency_text("كلام عام", _info(number=""), crisis=True)
    assert "إيذاء النفس" in out
