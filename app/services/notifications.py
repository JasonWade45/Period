"""إشعارات عربية آمنة لشاشة القفل: لا معلومة صحية في النص الظاهر.

المشكلة الواقعية: إشعار «دورتكِ متأخرة ١٢ يومًا» يظهر على شاشة القفل أمام أي
شخص يحمل الهاتف. الخصوصية هنا ليست تفضيلًا: تطبيق صحة نسائية على هاتف مشترك
أو هاتف يراه أفراد الأسرة قد يُسبب ضررًا فعليًا.

لذلك:
- نص الإشعار ثابت من ملف الموارد وخالٍ من أي معلومة صحية (لا دورة، لا نزيف،
  لا أعراض، لا أرقام أيام).
- التفاصيل تُترك للتطبيق نفسه.
- `assert_lock_screen_safe` يفحص النص قبل الإرسال ويفشل صراحة إن تسرّبت كلمة
  صحية — حماية من إضافة نص لاحقًا بلا انتباه.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..i18n import get_translator, resolve_locale

# كلمات ممنوعة في نص شاشة القفل (عربي/إنجليزي). أي إضافة لنص إشعار تخضع لهذا.
#
# العربية تُلحق الضمائر بالكلمة («دورتكِ»، «نزيفك»)، ولذلك:
#   - نُدرج الصيغ الملحقة صراحةً (دورتك/دورتها/نزيفك…) بدل اشتقاق آلي.
#     الاشتقاق على جذع «دور» يطابق «دورك في المجتمع»، وعلى «الم» يطابق
#     «المهم/المرأة» — إنذارات كاذبة تُعلّم الفريق تجاهل الفحص.
#   - المطابقة على النص المُطبَّع (بلا تشكيل، همزات موحّدة).
#   - مصطلح عربي بطول ≥ 4 حروف ⇒ مطابقة جزئية؛ أقصر (مثل «دم») ⇒ بحدود عربية
#     وإلا طابق «تقديم» و«خدمة».
#   - إنجليزي ⇒ حدود كلمة من الطرفين، فلا تُطابق "periodic" كلمة "period".
FORBIDDEN_TERMS: tuple[str, ...] = (
    "الدورة", "دورة", "دورتك", "دورتها", "دورتي", "دورات", "نزيف", "نزيفك",
    "حيض", "طمث", "ألم", "ألمك", "وجع", "وجعك", "حمل", "حملك", "حامل", "مخاض",
    "إجهاض", "تكيس", "بطانة", "لولب", "هرمون", "هرمونات", "دوخة", "إغماء", "دم",
    "period", "periods", "cycle", "cycles", "menstrual", "menstruation",
    "bleeding", "bleed", "cramp", "cramps", "pain", "pregnancy", "pregnant",
    "miscarriage", "endometriosis", "hormone", "hormonal", "clot", "clots",
)

SHORT_ARABIC_TERM_LENGTH = 4      # أقل من هذا ⇒ مطابقة بحدود لا جزئية

NOTIFICATION_KINDS = ("cycle_expected", "log_reminder", "checkin")


class UnsafeNotification(ValueError):
    """نص إشعار يحتوي معلومة صحية — لا يُرسل."""


@dataclass
class Notification:
    title: str
    body: str
    kind: str
    locale: str
    # بيانات الاعتماد للواجهة: تُسلَّم للـhandler ولا تُطبع على الشاشة
    data: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}


def assert_lock_screen_safe(text: str) -> None:
    """يرفض النص الذي يحمل معلومة صحية — يُستدعى على كل إشعار قبل الإرسال."""
    from .arabic import normalize_arabic

    normalized = normalize_arabic(text).lower()
    for term in FORBIDDEN_TERMS:
        if _is_arabic(term):
            stem = normalize_arabic(term)
            if len(stem) >= SHORT_ARABIC_TERM_LENGTH:
                if stem in normalized:
                    raise _unsafe(term)
            else:
                pattern = (r"(?<![\u0600-\u06FF])" + re.escape(stem)
                           + r"(?![\u0600-\u06FF])")
                if re.search(pattern, normalized):
                    raise _unsafe(term)
        elif re.search(r"\b" + re.escape(term) + r"\b", normalized):
            raise _unsafe(term)


def _unsafe(term: str) -> UnsafeNotification:
    return UnsafeNotification(
        f"نص الإشعار يحتوي معلومة صحية «{term}» — الخاصية المطلوبة أن "
        "شاشة القفل لا تكشف شيئًا عن حالة المستخدمة."
    )


def _is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def build_notification(kind: str, locale: str | None = None, *,
                       payload: dict[str, Any] | None = None) -> Notification:
    """يبني إشعارًا آمنًا لشاشة القفل.

    `payload` بيانات تُسلَّم للتطبيق (مثل معرّف السجل) ولا تُعرض على الشاشة.
    النوع غير المعروف ينحدر إلى `generic` بدل أن يفشل: إشعار تذكير بلا نوع
    صحيح أفضل من عدم إرساله.
    """
    loc = resolve_locale(locale)
    t = get_translator()
    key = kind if kind in NOTIFICATION_KINDS else None
    title = t.t(f"notifications.{key}.title" if key else "notifications.generic_title", loc)
    body = t.t(f"notifications.{key}.body" if key else "notifications.generic_body", loc)

    assert_lock_screen_safe(title)
    assert_lock_screen_safe(body)

    return Notification(title=title, body=body, kind=kind, locale=loc,
                        data=dict(payload or {}))


def notifications_for_cycle_reminders(*, days_before: int = 2, locale: str | None = None,
                                      payload: dict[str, Any] | None = None
                                      ) -> list[Notification]:
    """تذكيرات دورة متوقَّعة: العدد نفسه لا يُذكر في النص (يكفي داخل التطبيق).

    وجود `days_before` هنا مقصود: وقت الإرسال قرار تشغيلي، أما نصّه فلا يتغيّر
    بتغيّر عدد الأيام — لأن ذكره يكشف المعلومة على الشاشة.
    """
    if days_before < 0:
        raise ValueError("days_before لا يكون سالبًا")
    return [build_notification("cycle_expected", locale, payload=payload)]


__all__ = [
    "Notification", "UnsafeNotification", "FORBIDDEN_TERMS", "NOTIFICATION_KINDS",
    "build_notification", "assert_lock_screen_safe", "notifications_for_cycle_reminders",
]
