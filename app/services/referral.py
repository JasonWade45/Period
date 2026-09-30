"""السطر الإلزامي في نهاية كل إجابة عادية — قرار المالك (2026-09-30).

المحتوى مرجعي إرشادي: لا تشخيص ولا دواء، وكل إجابة عادية تُختم بسطر الاستشارة
حتمًا من الخادم نفسه — لا اعتمادًا على التزام الموديل بالبرومبت. ردود الطوارئ
مستثناة (رقم الطوارئ يسبق كل شيء هناك).
"""
from __future__ import annotations

from app.i18n import get_translator


def ensure_referral_notice(answer: str, language: str) -> str:
    """يختم الإجابة بسطر «استشيري طبيبك» إن لم يكن فيها — دون تكرار."""
    text = answer or ""
    notice = get_translator().t("answer.referral_notice", language)
    if not text.strip() or notice in text:
        return text
    return f"{text.rstrip()}\n\n{notice}"
