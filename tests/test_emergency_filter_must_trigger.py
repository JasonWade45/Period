"""«يجب أن تُفعِّل» — اختبارات تراجع لفلتر الطوارئ قبل الموديل.

هذه أهم مجموعة اختبارات في المشروع: عبارة واحدة تفوت هنا تعني أن مستخدمة
في خطر لا تتلقّى رد الطوارئ. كل عبارة هنا مأخوذة من صيغ واقعية (فصحى،
عامية مصرية، خليجية، إملاءات شائعة، إنجليزي) بأخطاء كتابة مقصودة.

عند إضافة عبارة جديدة: ضعيها هنا أولًا، ثم أضيفي الكلمة إلى
MEDICAL_PATTERNS / CRISIS_PATTERNS في app/services/emergency_filter.py.
"""
from __future__ import annotations

import pytest

from app.services.emergency_filter import check_message, normalize

MUST_TRIGGER_MEDICAL = [
    # ---- نزيف شديد / غزير: فصحى
    "بنزف كل ساعة وبرمي جلطات كبيرة",
    "عندي نزيف شديد من الصبح",
    "نزيف غزير جدًا ولا يتوقف",
    "نزيف حاد منذ ليلة",
    "النزيف مستمر ورغم كده مش بيقف",
    "بغيّر الفوطة كل ساعة وبتغرق",
    "بنزف أغزر من العادة بكتير",
    "نزيف مع جلطات دم كبيرة",
    "نزيف بعد انقطاع الطمث منذ شهر",
    # ---- نزيف: عامية مصرية وإملاءات شائعة
    "بنزف ومش عارفة أوقفه",
    "بنزل جلطات كبيرة وبخاف",
    "بنزل دم كتير أوي من الصبح",
    "بنزيف بشكل غريب من امبارح",
    "بينزف معايا دم كتير",
    "جايبالي نزف شديد تعبانة",
    # ---- نزيف: خليجية
    "انزف وايد وما يوقف",
    "دم وايد من الصبح",
    # ---- إغماء ودوخة شديدة
    "حصلني إغماء وأنا واقفة",
    "مغشي عليا مرتين النهاردة",
    "هغمى عليا لما قومت",
    "فقدان الوعي لثواني",
    "شبه إغماء مع نزيف",
    "دوخة شديدة ومش قادرة أقف",
    "دوخة قوية وتعرق بارد",
    "أغمى عليا في الحمام",
    # ---- ألم شديد ومفاجئ
    "ألم شديد ومفاجئ في البطن",
    "وجع شديد في بطني مش قادرة أتنفس",
    "ألم لا يطاق في الحوض",
    "وجعي جامد ومش قادرة أتحرك",
    "ألم حاد في أسفل البطن",
    "ألم في الكتف مع حمل محتمل",
    "وجع صدر مع ضيق نفس",
    # ---- تنفّس وصدر
    "ضيق في التنفس ومش قادرة أتنفس",
    "صعوبة تنفس من الصبح",
    "ألم في الصدر مع نزيف",
    "مش قادرة اتنفس كويس",
    # ---- حمل
    "حمل خارج الرحم وألم شديد",
    "حامل وبينزل دم",
    "حامل ومغشي عليا",
    "حمل مؤكد وألم شديد في الجنب",
    # ---- أخرى خطرة
    "تشنجات ورجفة مع نزيف",
    "تسمم حمل مع صداع شديد",
    "حمى شديدة مع دم",
    # ---- إنجليزي
    "heavy bleeding and I'm soaked",
    "I'm soaking through a pad every hour",
    "severe pain in my abdomen",
    "I fainted this morning",
    "chest pain and shortness of breath",
]

MUST_TRIGGER_CRISIS = [
    # ---- فصحى
    "أريد أن أموت",
    "لا أريد الحياة بعد الآن",
    "أفكر في إيذاء النفس",
    "أؤذي نفسي عندما أحزن",
    "أفكر في الانتحار",
    "أريد إنهاء حياتي",
    "أتمنى الموت كل يوم",
    # ---- عامية مصرية
    "مش عايزة أعيش",
    "مبقتش عايزة اعيش",
    "عايزة أموت من الوجع النفسي",
    "هقتل نفسي",
    "نفسي أموت وارتاح",
    "مفيش فايدة من حياتي",
    "خلاص تعبت ومش فارقة معايا أعيش",
    # ---- خليجية
    "ابغى اموت",
    "ودي اموت",
    "خلاص تعبت من الحياة",
    # ---- إملاءات شائعة
    "مش عايزه اعيش",
    "عايزه اموت",
    "انتحار",
    # ---- إنجليزي
    "I want to die",
    "I'm suicidal",
    "I want to kill myself",
    "I've been self harming",
    "I don't want to live anymore",
]


@pytest.mark.parametrize("text", MUST_TRIGGER_MEDICAL)
def test_medical_emergency_always_triggers(text: str) -> None:
    result = check_message(text)
    assert result.triggered, f"لم يُفعَّل الفلتر: {text}"
    assert result.kind == "medical", f"صُنّف خطأً كـ {result.kind}: {text}"


@pytest.mark.parametrize("text", MUST_TRIGGER_CRISIS)
def test_crisis_always_triggers(text: str) -> None:
    result = check_message(text)
    assert result.triggered, f"لم يُفعَّل الفلتر: {text}"
    assert result.kind == "crisis", f"صُنّف خطأً كـ {result.kind}: {text}"


def test_crisis_wins_over_medical_when_both_present() -> None:
    """الأولوية للأزمة النفسية لأن صياغة الرد مختلفة."""
    result = check_message("بنزف كتير ومش عايزة أعيش")
    assert result.kind == "crisis"


@pytest.mark.parametrize("text", [
    # أسئلة تثقيفية عامة: لا يجب أن تُفعّل رد الطوارئ
    "إيه الفرق بين الدورة المنتظمة والغير منتظمة؟",
    "إيه أعراض ما قبل الدورة؟",
    "ليه الدورة بتتأخر؟",
    "هل التقلصات في أول يوم طبيعية؟",
    "إيه أسباب الصداع قبل الدورة؟",
    "ما هو متوسط طول الدورة الشهرية؟",
    "what is a normal cycle length?",
    "إيه الفرق بين النزيف المهبلي والدورة؟",
])
def test_educational_questions_do_not_trigger(text: str) -> None:
    assert not check_message(text).triggered, f"فُعِّل خطأً: {text}"


@pytest.mark.parametrize("text", [
    # «الأسئلة عن العرض» ليست «الإبلاغ عن العرض» — لكن الحالات الفعلية أخطر،
    # لذا نُوثّق أن العبارات الخفيفة لا تُفعّل شيئًا (بلا درجات شدة)
    "إيه هي أسباب المغص؟",
    "عندي مغص بسيط قبل الدورة",
    "أحس بتعب خفيف أول يوم",
])
def test_mild_symptoms_do_not_trigger(text: str) -> None:
    assert not check_message(text).triggered, f"فُعِّل خطأً: {text}"


def test_normalization_handles_diacritics_and_tatweel() -> None:
    """التشكيل والتطويل لا يجوز أن يُستخدما لتجاوز الفلتر."""
    assert check_message("نَزِيـــف شَدِيــد").triggered
    assert check_message("أُريــد أن أَمــوت").kind == "crisis"


def test_normalization_collapses_whitespace() -> None:
    assert check_message("نزيف     شديد").triggered
    assert check_message("أريد\n\nأن أموت").kind == "crisis"


def test_empty_and_blank_messages_are_safe() -> None:
    assert not check_message("").triggered
    assert not check_message("   \n  ").triggered


def test_normalize_is_idempotent() -> None:
    once = normalize("نَزِيــف شَدِيد")
    assert normalize(once) == once
