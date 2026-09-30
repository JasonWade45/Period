import json

from app.services.validator import (
    check_banned_attribution,
    extract_json,
    validate,
)

ALLOWED = {"nhs-cycle-length"}
CHAT_OK = json.dumps({
    "answer": "سجّلتِ دورتين بطول 40 و42 يومًا وفق البيانات. يستحق النقاش مع طبيبة.",
    "sources_used": ["nhs-cycle-length"],
    "needs_doctor": False,
    "emergency": False,
    "crisis": False,
    "missing_info": [],
}, ensure_ascii=False)


def test_valid_chat_passes():
    r = validate(CHAT_OK, "chat", ALLOWED)
    assert r.ok, r.errors


def test_fenced_json_is_stripped():
    r = validate(f"```json\n{CHAT_OK}\n```", "chat", ALLOWED)
    assert r.ok, r.errors


def test_missing_field_fails():
    obj = json.loads(CHAT_OK)
    del obj["emergency"]
    r = validate(json.dumps(obj), "chat", ALLOWED)
    assert not r.ok and any("emergency" in e for e in r.errors)


def test_unknown_source_id_fails():
    obj = json.loads(CHAT_OK)
    obj["sources_used"] = ["made-up-id"]
    r = validate(json.dumps(obj), "chat", ALLOWED)
    assert not r.ok and any("made-up-id" in e for e in r.errors)


def test_banned_attribution_detected():
    errors = check_banned_attribution("أنتِ عندك تكيّس المبايض")
    assert errors


def test_banned_prescription_detected():
    assert check_banned_attribution("خذي حبوب منع الحمل")
    assert check_banned_attribution("الجرعة 500 ملغ")


def test_negative_diagnosis_detected():
    assert check_banned_attribution("أنتِ بخير ومفيش حاجة تقلق")


def test_general_education_allowed():
    assert not check_banned_attribution(
        "تكيّس المبايض قد يسبب اضطراب في الدورة، وهو من الأسباب الشائعة للتأخر."
    )


def test_summary_requires_meaning_and_questions():
    summary = {
        "overview": "x", "what_changed": "y", "patterns": "z",
        "medical_alerts": "", "what_this_does_not_mean": "",
        "questions_for_doctor": ["q1"],
        "sources_used": [],
    }
    r = validate(json.dumps(summary), "summary", set())
    assert not r.ok
    assert any("what_this_does_not_mean" in e for e in r.errors)
    assert any("3-5" in e for e in r.errors)


def test_extract_json_finds_embedded_object():
    obj = extract_json('garbage before {"a": 1} garbage after')
    assert obj == {"a": 1}
