from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from ..schemas import Finding, Severity
from .emergency_numbers import EmergencyInfo

# ---------------------------------------------------------------------
# كلمات مفتاحية: فصحى + عامية مصرية + خليجية + إملاءات شائعة + إنجليزي
# التحقق الفعلي يتم بالـ classifier في الإنتاج؛ هذه الطبقة الأولى.
#
# تُطبَّق الأنماط على نص مُوحَّد (بلا تشكيل، ألف/ياء/تاء مربوطة موحّدة،
# بلا تطويل أو مسافات زائدة)، لذا تُكتب الأنماط بالهمزات الموحّدة (ا/ي)
# لا (أ/إ/ى/ئ) — وهذا يمنع تجاوز الفلتر بفرق حرف واحد.
# ---------------------------------------------------------------------

MEDICAL_PATTERNS: list[str] = [
    # NOTE: المطابقة جزئية (substring) عن قصد، لأن العربية تُلحق «ال» وحروف
    # الجر بالكلمة فتفشل حدود الكلمات («نزيف» لا تُطابق «النزيف» بحدود).
    # النتيجة: ممنوع تمامًا إدراج كلمة قصيرة غامضة وحدها — «الم» تطابق
    # «المنتظمة» و«المهبلي»، فتُحوّل أسئلة تثقيفية إلى رد طوارئ. كل عنصر هنا
    # يجب أن يكون عبارة مؤهَّلة (صفة شدة أو تركيب) لا كلمة مفردة عامة.
    # فصحى
    "نزيف شديد", "نزيف غزير", "نزيف حاد", "نزيف مستمر", "نزيف لا يتوقف",
    "نزيف كتير", "اغزر من", "يغرق", "فوطه كل ساعه", "كل ساعه", "كل نص ساعه",
    "جلطات دم", "جلطات كبيره", "جلطات كتير", "خثرات",
    "اغماء", "اغمي عليا", "اغمي علي", "فقدان الوعي", "فقدت الوعي", "شبه اغماء",
    "دوخه شديده", "دوخه قويه", "دوار شديد",
    "الم شديد", "الم مفاجي", "الم حاد", "الم لا يحتمل", "الم لا يطاق",
    "وجع شديد", "وجع قوي", "ضيق تنفس", "صعوبه تنفس", "صعوبه في التنفس",
    "الم في الصدر", "وجع صدر", "الم بالصدر", "الم الصدر",
    "حمل خارج الرحم", "خارج الرحم", "تسمم حمل", "مخاض", "تشنجات",
    "ارتفاع حراره شديد", "حمى شديده", "حراره عاليه",
    "نزيف بعد انقطاع الطمث", "نزيف بعد سن الياس", "نزيف بعد الولاده",
    # عامية مصرية
    "بنزف", "بنزيف", "بنزل جلطات", "بنزل دم كتير", "مغشي عليا", "مغشي عليها",
    "هغمى عليا", "غمى عليا",
    "وجعي جامد", "المي جامد", "وجع قوي في بطني", "بتتقلص جامد",
    "مش قادره اتنفس", "مش قادر اتنفس", "مش قادره اقف", "مش قادره اتحرك",
    # خليجية
    "انزف وايد", "دم وايد", "ما اقدر اتنفس", "الم فظيع", "وجع فظيع",
    # إملاءات شائعة وأخطاء كتابة
    "نزف غزير", "نزف شديد", "بينزف", "بنزفف", "نزيف غزير", "نزيف غزيير",
    # إنجليزي
    "heavy bleeding", "soaking through", "soaking a pad", "pad an hour",
    "fainting", "fainted", "pass out", "passed out", "severe pain",
    "chest pain", "trouble breathing", "shortness of breath", "can't breathe",
    "cant breathe", "shoulder pain", "emergency", "unbearable pain",
    "bleeding heavily", "blood clots",
]

# حمل + نزيف/دم = طارئ نسائي (احتمال حمل خارج الرحم أو إجهاض).
# هذه قاعدة تركيبية لأن العبارة قد تُكتب بترتيب أو صياغة لا يلتقطها نمط واحد.
PREGNANCY_PATTERNS: list[str] = ["حامل", "حمل مؤكد", "حمل محتمل", "حمل خارج الرحم", "pregnant"]
BLEEDING_PATTERNS: list[str] = [
    "دم", "نزيف", "نزف", "بنزف", "جلطات", "bleeding", "blood", "spotting",
]

# حمل + ألم كتف = احتمال حمل خارج الرحم (علامة خطر صريحة في البرومبت).
# ألم الكتف وحده شائع وحميد، لذا لا يُدرج كمصطلح مستقل بل كقاعدة تركيبية.
PREGNANCY_SHOULDER_PAIN: list[str] = ["الكتف", "كتفي", "shoulder"]

CRISIS_PATTERNS: list[str] = [
    # فصحى
    "ايذاء النفس", "إيذاء النفس", "اؤذي نفسي", "أوذي نفسي", "جرح نفسي",
    "اريد ان اموت", "اريد الموت", "ابغى اموت", "ابي اموت", "اتمنى الموت",
    "لا اريد الحياه", "لا اريد ان اعيش", "ما عادت الحياه تعجبني",
    "انهي حياتي", "انتحار", "اقتل نفسي", "انهاء حياتي", "التخلص من حياتي",
    # عامية مصرية
    "اموت", "مش عايزه اعيش", "مش عايز اعيش", "مبقتش عايزه اعيش",
    "مبقتش عايز اعيش", "مخنوقه ومش عايزه", "اخلص من كل ده", "خلصت من كل حاجه",
    "عايزه اموت", "عايز اموت", "هقتل نفسي", "نفسي اموت", "مرهق نفسي",
    "بفكر اموت", "مفيش فايده من حياتي", "مش فارقه معايا اعيش",
    # خليجية
    "ابي اموت", "ودي اموت", "ابغى اموت", "خلاص تعبت من الحياه",
    # إنجليزي
    "suicide", "suicidal", "kill myself", "killing myself", "self harm",
    "self-harm", "hurt myself", "hurting myself", "don't want to live",
    "dont want to live", "want to die", "wanna die", "end my life",
    "end it all", "no reason to live",
]

EMERGENCY_FIRST_SENTENCE = "الأعراض التي ذكرتِها قد تحتاج رعاية طبية عاجلة"

CRISIS_FIRST_SENTENCE = "ما تشهدينه من أفكار إيذاء النفس يستحق دعمًا فوريًا"

_AR_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_AR_ALEF = re.compile(r"[أإآٱ]")
_AR_YA = re.compile(r"[ىئ]")
_AR_TA_MARBUTA = re.compile(r"\u0629")
_WS = re.compile(r"\s+")


@dataclass
class FilterResult:
    kind: Optional[str]  # "medical" | "crisis" | None
    matched: str = ""
    source: str = ""  # "message" | "findings"

    @property
    def triggered(self) -> bool:
        return self.kind is not None


def normalize(text: str) -> str:
    """توحيد النص لمطابقة مستقرة: تشكيل، همزات، تطويل، مسافات."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _AR_DIACRITICS.sub("", text)
    text = _AR_ALEF.sub("ا", text)
    text = _AR_YA.sub("ي", text)
    text = _AR_TA_MARBUTA.sub("ه", text)
    return _WS.sub(" ", text.lower()).strip()


def _find(patterns: Iterable[str], normalized_text: str) -> str:
    for p in patterns:
        if normalize(p) in normalized_text:
            return p
    return ""


def check_message(message: str) -> FilterResult:
    """فحص رسالة المستخدمة.

    ملاحظة: كلمات مثل «نزيف» أو «دوخة» وحدها **لا** تُفعّل الفلتر، بل تحتاج
    درجة (غزير/شديد/لا يتوقف) أو عبارة شخصية. المطابقة الحرفية لما ذُكر هنا
    مقصودة ومحافظة، لكنها معجمية بطبيعتها: أي عبارة تفوت يجب إضافتها هنا ومعها
    اختبار في tests/test_emergency_filter.py.
    """
    text = normalize(message)
    if not text:
        return FilterResult(kind=None)
    hit = _find(CRISIS_PATTERNS, text)
    if hit:
        return FilterResult(kind="crisis", matched=hit, source="message")
    hit = _find(MEDICAL_PATTERNS, text)
    if hit:
        return FilterResult(kind="medical", matched=hit, source="message")

    # قاعدة تركيبية: حمل + أي إشارة نزيف = طارئ نسائي
    if _find(PREGNANCY_PATTERNS, text) and _find(BLEEDING_PATTERNS, text):
        return FilterResult(kind="medical", matched="pregnancy+bleeding", source="message")

    # قاعدة تركيبية: حمل + ألم كتف = احتمال حمل خارج الرحم
    if _find(PREGNANCY_PATTERNS, text) and _find(PREGNANCY_SHOULDER_PAIN, text):
        return FilterResult(kind="medical", matched="pregnancy+shoulder_pain", source="message")

    return FilterResult(kind=None)


def check_findings(findings: list[Finding]) -> FilterResult:
    if any(f.severity == Severity.EMERGENCY for f in findings):
        return FilterResult(kind="medical", matched="severity=EMERGENCY", source="findings")
    if any(f.severity == Severity.URGENT for f in findings):
        return FilterResult(kind="medical", matched="severity=URGENT", source="findings")
    return FilterResult(kind=None)


def run_filter(message: str, findings: list[Finding]) -> FilterResult:
    """فحص ما قبل الموديل: رسالة المستخدمة ثم نتائج محرك القواعد."""
    result = check_message(message)
    if result.triggered:
        return result
    return check_findings(findings)


def build_fixed_reply(result: FilterResult, info: EmergencyInfo) -> str:
    """رد ثابت — لا استدعاء للموديل. يذكر الرقم حرفيًا مع حالته التحققية.

    إن كان الرقم غير مُتحقق منه لهذا البلد نقول ذلك صراحة بدل تقديمه كأنه
    الصحيح، ونوجّه لطلب الإسعاف بأي رقم طوارئ محلي تعرفه.
    """
    caveat = ""
    if not info.verified:
        caveat = (
            f"الرقم {info.number} غير مُتحقق منه لبلدك، فلا تعتمدي عليه وحده: "
            "اطلبي الإسعاف على رقم الطوارئ المحلي الذي تعرفينه."
        )

    if result.kind == "crisis":
        lines = [
            CRISIS_FIRST_SENTENCE,
            "",
            "تواصلي فورًا مع شخص تثق به الآن، ولا تكوني وحدها.",
        ]
        if info.crisis_line:
            lines.append(f"خط الدعم: {info.crisis_line}")
        else:
            lines.append("لا يتوفّر لديّ خط دعم نفسي مُتحقق منه في بلدك؛ اطلبيه من الطوارئ أو من طبيبة.")
        lines.append("لو كان هناك خطر مباشر على حياتكِ، اطلبي الإسعاف فورًا.")
        if info.number:
            lines.append(f"الإسعاف: {info.number}")
        if caveat:
            lines.append(caveat)
        return "\n".join(lines)

    lines = [
        EMERGENCY_FIRST_SENTENCE,
        "",
        f"توجهي الآن لأقرب طوارئ أو اطلبي الإسعاف على {info.number} ولا تنتظري ردًّا آخر.",
        "حاولي ألا تكوني وحدها إن أمكن.",
    ]
    if caveat:
        lines.append(caveat)
    return "\n".join(lines)


def ensure_emergency_text(answer: str, info: EmergencyInfo, *, crisis: bool) -> str:
    """ضمان ذكر الرقم حرفيًا حين يعلن الموديل طوارئ لم يلتقطها الفلتر.

    الطبقة الأولى معجمية، لذا قد يكتشف الموديل حالة طوارئ صيغتها غير مدرجة.
    حينها لا يكفي أن يكون العلم true: يجب أن يظهر الرقم **والتعليمات المعيارية**
    في نص الرد نفسه (واجهة أخرى قد تتجاهل الأعلام).

    ملاحظة عن خلل سابق: كان يكفي وجود الرقم وحده لتخطّي هذه الدالة، فتمرّ أزمة
    بلا صياغة الدعم المعيارية؛ وكان وجود الجملة المعيارية وحدها يُسقط الرقم.
    الآن يُضمن الاثنان معًا، بلا تكرار الجملة إن كانت موجودة.
    """
    text = (answer or "").strip()
    header = CRISIS_FIRST_SENTENCE if crisis else EMERGENCY_FIRST_SENTENCE
    has_header = header in text
    has_number = bool(info.number) and info.number in text

    # الطوارئ الطبية: ذكر الرقم كافٍ (الردّ صار موجَّهًا فعليًا).
    # الأزمة النفسية: الشرط أشد — الجملة المعيارية والرقم معًا، لأن جملة الدعم
    # وحثّها على عدم البقاء وحدها جزء من سلامة الرد لا تحسين أسلوبي.
    if has_number and (has_header or not crisis):
        return text
    if not text:
        return build_fixed_reply(FilterResult(kind="crisis" if crisis else "medical"), info)

    block = build_fixed_reply(FilterResult(kind="crisis" if crisis else "medical"), info)

    if has_header:
        # التعليمات موجودة والناقص الرقم: نضيف سطر الرقم وحده بلا تكرار الجملة
        number_line = next((line for line in block.split("\n")
                            if info.number and info.number in line), "")
        return f"{text}\n{number_line}".strip() if number_line else f"{text}\n\n{block}"

    # التعليمات غائبة: تُقدَّم أولًا دائمًا، ويليها كلام الموديل
    return f"{block}\n\n---\n\n{text}"
