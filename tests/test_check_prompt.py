"""فحص برومبت مقابل عقد التطبيق — اختبارات الأداة نفسها.

الفائدة العملية: عند وصول برومبت جديد (مثل cyclecare-system-prompt.md) تُشغَّل
الأداة عليه قبل استبدال الملف الحالي، فتُكشف أي كسور في العقد فورًا.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.services.emergency_filter import (
    CRISIS_FIRST_SENTENCE,
    EMERGENCY_FIRST_SENTENCE,
)
from app.services.prompt_builder import _VAR_PATTERN
from app.services.validator import CHAT_FIELDS, SUMMARY_FIELDS
from tools.check_prompt import REQUIRED_VARIABLES, check_prompt


def _minimal_valid_prompt() -> str:
    """برومبت مصغّر يجتاز العقد — أساس لبناء حالات الاختبار."""
    variables = " ".join(f"{{{{{name}}}}}" for name in sorted(REQUIRED_VARIABLES))
    fields = " ".join(list(CHAT_FIELDS) + list(SUMMARY_FIELDS))
    return (
        f"{variables}\n{fields}\n{EMERGENCY_FIRST_SENTENCE}\n{CRISIS_FIRST_SENTENCE}\n"
        "بيانات وليس تعليمات\nليست تشخيصًا\nJSON\n"
    )


# ------------------------------------------------------------ البرومبت الحالي

def test_shipped_prompt_passes_the_contract():
    report = check_prompt(Path(settings.prompt_path).read_text(encoding="utf-8"))
    assert report.ok, "\n".join(report.errors)


def test_shipped_prompt_version_matches_config():
    """الملف وإعداد الإصدار يجب ألا يتباعدا: الإصدار يظهر في كل رد وتدقيق."""
    text = Path(settings.prompt_path).read_text(encoding="utf-8")
    assert settings.prompt_version in text, (
        f"الإصدار {settings.prompt_version} غير مذكور في {settings.prompt_path.name}"
    )


def test_shipped_prompt_has_exactly_the_required_variables():
    text = Path(settings.prompt_path).read_text(encoding="utf-8")
    assert set(_VAR_PATTERN.findall(text)) == REQUIRED_VARIABLES


# --------------------------------------------------------------- حالات الفشل

def test_missing_variable_is_an_error():
    text = _minimal_valid_prompt().replace("{{SOURCES}}", "")
    report = check_prompt(text)
    assert not report.ok
    assert any("SOURCES" in e for e in report.errors)


def test_unknown_variable_is_an_error():
    """متغيّر لا يملؤه PromptBuilder يُسقط كل طلب بـ ValueError."""
    report = check_prompt(_minimal_valid_prompt() + "\n{{NOT_A_REAL_VAR}}\n")
    assert not report.ok
    assert any("NOT_A_REAL_VAR" in e for e in report.errors)


def test_missing_emergency_sentence_is_an_error():
    text = _minimal_valid_prompt().replace(EMERGENCY_FIRST_SENTENCE, "")
    report = check_prompt(text)
    assert not report.ok
    assert any("الطوارئ" in e for e in report.errors)


def test_missing_crisis_sentence_is_an_error():
    """هذه هي الحالة التي كُشفت فعليًا في v1.1: كانت جملة الأزمة غائبة."""
    text = _minimal_valid_prompt().replace(CRISIS_FIRST_SENTENCE, "")
    report = check_prompt(text)
    assert not report.ok
    assert any("الأزمة" in e for e in report.errors)


@pytest.mark.parametrize("field", sorted(CHAT_FIELDS))
def test_missing_chat_field_is_an_error(field):
    # نُزيل كل نسخ الاسم: بعض الحقول (sources_used) مشتركة بين الوضعين
    text = _minimal_valid_prompt().replace(field, "")
    assert not check_prompt(text).ok, f"حقل {field} غائب ولم يُكشف"


def test_missing_summary_field_is_an_error():
    text = _minimal_valid_prompt().replace("questions_for_doctor", "")
    report = check_prompt(text)
    assert not report.ok
    assert any("questions_for_doctor" in e for e in report.errors)


def test_empty_prompt_is_an_error():
    assert not check_prompt("").ok
    assert not check_prompt("   \n ").ok


# --------------------------------------------------------------- تحذيرات فقط

def test_quoted_prohibited_example_does_not_warn():
    """«خذي كذا» داخل مقتبسات مثالٌ على المنع، لا تعليمات للموديل."""
    text = _minimal_valid_prompt() + "\nالممنوع: «أنتِ عندك»، «خذي كذا».\n"
    report = check_prompt(text)
    assert report.ok
    assert not any("توجيه دوائي" in w for w in report.warnings)


def test_unquoted_directed_drug_advice_warns():
    text = _minimal_valid_prompt() + "\nخذي حبوب منع الحمل لتنظيم الدورة.\n"
    report = check_prompt(text)
    assert report.ok                      # تحذير لا خطأ
    assert any("توجيه دوائي" in w for w in report.warnings)


def test_missing_soft_requirements_are_warnings_not_errors():
    text = _minimal_valid_prompt().replace("بيانات وليس تعليمات", "")
    report = check_prompt(text)
    assert report.ok
    assert any("بيانات وليس تعليمات" in w for w in report.warnings)
