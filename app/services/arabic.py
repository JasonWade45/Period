"""تطبيع النص العربي — مصدر واحد لكل طبقات النظام.

كان التطبيع مكرّرًا في `validator` و`emergency_filter` (نسختان متطابقتان
تقريبًا). أي اختلاف بينهما يعني أن عبارة تُمنع في مكان وتفوت في آخر، لذا
التطبيع هنا مركزي: فلتر الطوارئ، مدقّق المخرجات، وفهرس البحث الكلمي كلهم
يستدعونه.

التطبيع لا يغيّر المعنى ولا المعروض للمستخدمة — يُستخدم للمقارنة والبحث فقط.
"""
from __future__ import annotations

import re
import unicodedata

# ملاحظة مهمة: `str.lower()` تعمل على العربية بلا أثر، و`NFKC` يحوّل أشكال
# العرض إلى أشكالها القياسية. كلاهما آمن هنا.

_TASHKEEL = re.compile(r"[\u064B-\u0652\u0653-\u0655\u0670]")
_TATWEEL = re.compile(r"\u0640")
_ALEF = re.compile(r"[\u0622\u0623\u0625\u0671]")      # آ أ إ ٱ → ا
_YAA = re.compile(r"[\u0649\u0626]")                    # ى ئ → ي
_TAA_MARBUTA = re.compile(r"\u0629")                    # ة → ه
_HAMZA = re.compile(r"[\u0624]")                        # ؤ → و (اختياري، للبحث)

_LATIN_DIGITS = "0123456789"
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"          # U+0660–U+0669
_EXTENDED_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹"  # U+06F0–U+06F9 (فارسية/أردية)

_DIGIT_MAP = {}
for _i, _d in enumerate(_LATIN_DIGITS):
    _DIGIT_MAP[ord(_ARABIC_INDIC[_i])] = ord(_d)
    _DIGIT_MAP[ord(_EXTENDED_ARABIC_INDIC[_i])] = ord(_d)

_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_digits(text: str) -> str:
    """يحوّل الأرقام العربية-الهندية (٠١٢) والفارسية (۰۱۲) إلى غربية (012).

    ضروري قبل أي تحقّق أو مقارنة: المستخدمة قد تكتب «٤٥» أو «45» أو تخلط
    الاثنين، وكلها تعني نفس الرقم. لا يمسّ النص المعروض.
    """
    return (text or "").translate(_DIGIT_MAP)


def strip_tashkeel(text: str) -> str:
    """إزالة التشكيل والتطويل — «نَزِيــف» تساوي «نزيف»."""
    return _TATWEEL.sub("", _TASHKEEL.sub("", text or ""))


def unify_letters(text: str) -> str:
    """توحيد الألف والياء والتاء المربوطة والهمزة.

    الغرض: ألا يكون فرق حرف واحد في الإملاء (فصحى مقابل عامية) طريقًا
    لتجاوز فلتر أمان أو لإخفاء نتيجة بحث.
    """
    text = _ALEF.sub("\u0627", text or "")
    text = _YAA.sub("\u064A", text)
    text = _TAA_MARBUTA.sub("\u0647", text)
    return _HAMZA.sub("\u0648", text)


def normalize_arabic(text: str, *, collapse_whitespace: bool = True) -> str:
    """التطبيع الكامل للمقارنة: NFKC + أرقام غربية + تشكيل + توحيد حروف.

    لا يحوّل علامات الترقيم إلى مسافات (استخدم `normalize_for_search` لذلك).
    """
    out = unicodedata.normalize("NFKC", text or "")
    out = normalize_digits(out)
    out = strip_tashkeel(out)
    out = unify_letters(out)
    if collapse_whitespace:
        out = _WS.sub(" ", out).strip()
    return out


def normalize_for_search(text: str) -> str:
    """تطبيع للفهرسة الكلمية: كل ما سبق + إزالة الترقيم + lowercase.

    يُستخدم لبناء `tsv`/الفهرس الكلمي ولتحويل استعلام المستخدمة بنفس الطريقة،
    حتى يطابق «النزيف الغزير!» مقطعًا مكتوبًا «نزيف غزير».
    """
    out = normalize_arabic(text, collapse_whitespace=False)
    out = _NON_WORD.sub(" ", out)
    return _WS.sub(" ", out).strip().lower()


def search_tokens(text: str) -> list[str]:
    """كلمات البحث بعد التطبيع — نتجاهل ما طوله حرف واحد (أدوات ربط)."""
    return [t for t in normalize_for_search(text).split() if len(t) > 1]


def has_arabic(text: str) -> bool:
    """هل النص يحتوي حروفًا عربية؟ يُستخدم لاختيار لغة الرد."""
    return any("\u0600" <= ch <= "\u06FF" for ch in text or "")
