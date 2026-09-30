import json

import pytest

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


# ---------------------------------------------------------------------
# تراجعات أمنية: صيغ إسناد/تطمين/جرعة كانت تتجاوز الفلتر قبل التوحيد
# والتوسيع. كل سطر هنا حالة الإسناد المطلوب منعها.
# ---------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    # إسناد تشخيص أو حمل للمستخدمة
    "يبدو إنك حامل وده سبب التأخر.",
    "انتي حامل على الأغلب",
    "إنتي حامل",
    "واضح إنك عندك أنيميا",
    "عندك تكيس المبايض على الأغلب",
    "أنتي بتعاني من بطانة الرحم المهاجرة",
    "شكلك عندك التهاب",
    "إنتي مصابة بعدوى",
    "أنتِ عندك تكيّس المبايض",
    # نفي قاطع أو تطمين
    "متقلقيش، ده طبيعي",
    "اطمني مفيش حاجة",
    "مافيش مشكلة خالص",
    "ده مش مقلق",
    "أنتِ بخير ومفيش حاجة تقلق",
    "مش حامل",
    # وصف دواء أو جرعة أو إيقافه
    "خدي حبوب منع الحمل",
    "لازم تاخدي مسكن",
    "خذي حبوب منع الحمل",
    "الجرعة 500 ملغ",
    "بلاش توقفي الدواء",
])
def test_known_bypass_patterns_are_blocked(text):
    assert check_banned_attribution(text), f"لم يُمنع: {text}"


@pytest.mark.parametrize("text", [
    "تكيّس المبايض من الأسباب الشائعة لتأخر الدورة، وسؤال طبيبة مفيد.",
    "تكيّس المبايض قد يسبب اضطراب في الدورة، وهو من الأسباب الشائعة للتأخر.",
    "هذه النتيجة ليست تشخيصًا.",
    "التفاوت الطفيف بين الدورات أمر شائع وقد لا يعني حالة مرضية.",
    "الدورة الأطول من 35 يومًا قد تكون محورًا مفيدًا للنقاش مع طبيبة.",
    "سجّلتِ 3 دورات بأطوال 38 و41 و44 يومًا، وهذا يستحق المراجعة.",
    "أعراض ما قبل الدورة تشمل تقلبات المزاج والانتفاخ، وأعراضكِ المسجّلة تستحق النقاش.",
    "إن كان النزيف يغرق منتجًا كل ساعة، فهذا يستدعي رعاية عاجلة.",
    "لا أملك معلومة موثوقة كافية عن هذا.",
])
def test_legitimate_educational_text_is_not_blocked(text):
    """إفراط الفلتر يُفشل كل رد؛ التعليم العام يجب أن يمر."""
    assert not check_banned_attribution(text), f"مُنع خطأً: {text}"


def test_normalization_covers_spelling_variants():
    """فروق الهمزة/الياء/التاء المربوطة لا تُستخدم للتجاوز."""
    variants = ["أنتِ عندك أنيميا", "انتي عندك انيميا", "إنتي عندك أنيميا"]
    assert all(check_banned_attribution(v) for v in variants)


@pytest.mark.parametrize("text", [
    "خدي إيبوبروفين قبل الدورة",
    "ابدئي ترانيكساميك في أيام النزيف",
    "جربي لولب هرموني",
    "استخدمي ميتفورمين لتنظيم الدورة",
    "لازم تاخدي حبوب منع الحمل",
    "take ibuprofen for the pain",
])
def test_directed_drug_advice_is_blocked(text):
    """المسودة تذكر خيارات علاجية تعليميًا؛ تحويلها إلى نصيحة موجّهة ممنوع."""
    assert check_banned_attribution(text), f"لم يُمنع: {text}"


@pytest.mark.parametrize("text", [
    "من الخيارات التي تقررها الطبيبة: مضادات الالتهاب غير الستيرويدية مثل الإيبوبروفين.",
    "اللولب الهرموني من الخيارات الفعالة للنزيف الغزير، ويقرره الطبيب.",
    "حمض الترانيكساميك يقلل النزيف وله موانع منها تاريخ جلطات.",
])
def test_educational_mention_of_treatments_is_allowed(text):
    """ذكر الخيارات العلاجية كمعلومة عامة مسموح؛ الممنوع هو توجيه المستخدمة."""
    assert not check_banned_attribution(text), f"مُنع خطأً: {text}"


@pytest.mark.parametrize("text", ["خذي وقتك في متابعة الأعراض.", "خذي نفسًا عميقًا.",
                                  "خذي راحتك.", "خذي وضعية الطفل 30 ثانية."])
def test_benign_directives_are_not_blocked(text):
    """الإفراط في المنع يُفشل ردودًا سليمة: النصيحة الحميدة يجب أن تمر."""
    assert not check_banned_attribution(text), f"مُنع خطأً: {text}"


@pytest.mark.parametrize("text", ["خذي مسكن", "خذي مضاد حيوي", "خذي حقنة",
                                  "خذي كبسولة", "خذي دوا"])
def test_directive_with_a_medicine_object_is_blocked(text):
    assert check_banned_attribution(text), f"لم يُمنع: {text}"
