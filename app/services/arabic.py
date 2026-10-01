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


# ----------------------------------------------------------------------------
# رموز الاسترجاع (retrieval): تجريد خفيف + كلمات وصل + مرادفات عامية.
#
# لماذا منفصلة عن `search_tokens`: تلك تحرس عقدًا قائمًا (التطبيع فقط)، وهذه
# «تفهم» السؤال العامي: «المغص» لا تطابق «مغص» بلا تجريد «ال»، و«إيه» و«ليه»
# ضجيج يرفع درجة مقاطع لا علاقة لها. التجريد يُطبَّق على المقطع وعلى السؤال
# بنفس الدالة، فلا يهم أن يكون خشنًا — المهم أن يكون متطابقًا في الجهتين.
# ----------------------------------------------------------------------------

# كلمات وصل/استفهام شائعة (بعد التطبيع: همزات→ا، ة→ه، ى→ي)
_STOPWORDS = frozenset(
    "من في على عن الي الى الا ان انا انت انتي هل ما ماذا ايه اي ليه لما ازاي امتي ايمتي "
    "فين مين كام هو هي هم ده دي دا دول هذا هذه ذلك تلك لو مع بعد قبل كان كانت يكون "
    "تكون بقي يعني كده كدا بس لازم عشان علشان او ثم قد لا مش مفيش فيه بتاع بتاعت "
    "اللي الذي التي هيك حاجه شي كل عند عندي عندها عندك ممكن يمكن اعمل اعملي قولي قوليلي "
    "عايزه عايز نفسي حابه اسال اسأل سؤال".split()
)

# عامية ← فصحى الكتب (مفاتيح بعد التطبيع وبعد التجريد)
_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "مغص": ("الم", "تقلص"),
    "دكتور": ("طبيب",), "دكتوره": ("طبيب",),
    "حبوب": ("مانع", "هرموني"), "حبايه": ("مانع",),
    "منع": ("مانع",),
    "رياض": ("نشاط", "تمارين"), "تمرين": ("نشاط",),
    "اكل": ("غذاء", "تغذيه"), "اكلي": ("تغذيه",),
    "حديد": ("حديد", "انيميا"),
    "تعب": ("ارهاق",), "ارهاق": ("تعب",),
    "عصبيه": ("مزاج", "اعراض"), "مزاج": ("مزاج",),
    "نزيف": ("نزيف", "غزير"),
    "ثقيل": ("غزير",), "كتير": ("غزير",),
    "تاخير": ("تاخر",), "اتاخر": ("تاخر",), "متاخر": ("تاخر",),
    
    "ولاده": ("بعد الولاده",), "رضاعه": ("رضاع",),
    "مراهق": ("مراهقات",), "بنت": ("مراهقات",),
}

_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "بت", "هت", "ب", "ل", "و")
_SUFFIXES = ("ات", "ون", "ين", "ها", "ه", "ي")


def light_stem(token: str) -> str:
    """تجريد خفيف للمقارنة فقط: بادئات «ال/بال/وال/بت…» ولواحق «ات/ه/ها…».

    لا يقصّ كلمة إلى أقل من 3 أحرف، ولا يمسّ الأرقام والكلمات اللاتينية.
    """
    if not token or not token[0] >= "\u0600" or token[0] > "\u06ff":
        return token
    for prefix in _PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            # «ب/ل/و» المفردة تُقصّ فقط من الكلمات الطويلة (حتى لا نُتلف «بنت»)
            if len(prefix) == 1 and len(token) < 6:
                continue
            token = token[len(prefix):]
            break
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[:-len(suffix)]
            break
    return token


# المفاتيح تُجرَّد بنفس الدالة كي تطابق ما يخرج من `light_stem` فعلًا
_EXPANSIONS = {light_stem(k): v for k, v in _QUERY_EXPANSIONS.items()}


def retrieval_tokens(text: str, *, expand: bool = False) -> list[str]:
    """رموز البحث الكلمي: تطبيع ← حذف كلمات الوصل ← تجريد (+ مرادفات للسؤال).

    `expand=True` للسؤال فقط: يضيف المقابل الفصيح للكلمة العامية. المقطع لا
    يُوسَّع (نصه ثابت)، فلا نضخّم أي مقطع بكلمات لم يكتبها أحد فيه.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        if len(token) > 1 and token not in seen:
            seen.add(token)
            out.append(token)

    for raw in normalize_for_search(text).split():
        if raw in _STOPWORDS:
            continue
        stem = light_stem(raw)
        if stem in _STOPWORDS or len(stem) <= 1:
            continue
        add(stem)
        if expand:
            for extra in _EXPANSIONS.get(stem, ()):
                for part in extra.split():
                    add(light_stem(part))
    return out
