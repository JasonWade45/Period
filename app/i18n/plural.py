"""قواعد الجمع — العربية والإنجليزية حسب CLDR.

العربية ليست جمعًا ثنائيًا (مفرد/جمع) كما الإنجليزية، بل ست حالات:
zero / one / two / few / many / other. تطبيق قاعدتين فقط يُنتج جملًا خاطئة
مثل «3 يومًا» بدل «3 أيام»، وهي أخطاء تظهر فورًا في نصوص طبية.
"""
from __future__ import annotations

from decimal import Decimal

PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")


def _integer_value(value: float | int | Decimal) -> Decimal:
    return Decimal(str(value))


def arabic_category(value: float | int) -> str:
    """قاعدة CLDR للعربية.

    0 → zero | 1 → one | 2 → two | 3–10 → few | 11–99 → many | غير ذلك → other

    الأعداد المركبة (100 وما فوق) تُقاس بباقي القسمة على 100، فـ103 → few
    و111 → many، وهذا ما يفرّق العربية عن الإنجليزية في الجمل المتعلقة
    بالمدد مثل «103 أيام».
    """
    n = _integer_value(value)
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    remainder = abs(n) % 100
    if 3 <= remainder <= 10:
        return "few"
    if 11 <= remainder <= 99:
        return "many"
    return "other"


def english_category(value: float | int) -> str:
    """قاعدة CLDR للإنجليزية: one للواحد، other لغير ذلك."""
    return "one" if abs(_integer_value(value)) == 1 else "other"


def category(locale: str, value: float | int) -> str:
    """اختيار القاعدة حسب اللغة — مع خلفية عربية عند لغة غير معروفة."""
    if locale.startswith("en"):
        return english_category(value)
    return arabic_category(value)


def plural_forms_count(locale: str) -> int:
    """عدد الصيغ المطلوبة: 6 للعربية، 2 للإنجليزية."""
    return 6 if not locale.startswith("en") else 2


__all__ = ["PLURAL_CATEGORIES", "arabic_category", "english_category", "category",
           "plural_forms_count"]
