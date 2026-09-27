"""Deterministic AI safety layer (PRD §55).

1. `screen_user_message` — detects emergency phrases in what the USER typed, so
   emergencies bypass the LLM entirely (PRD P4 / §49).
2. `validate_ai_response` — rejects model output containing prohibited claims
   (diagnoses, pregnancy status, reassurance that dismisses care, etc.).

Both are intentionally conservative, keyword/regex based, and unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Prohibited claims in AI output
# ---------------------------------------------------------------------------

_CONDITIONS_EN = (
    r"pcos|pmos|polycystic ovar(?:y|ian) syndrome|polyendocrine metabolic ovarian syndrome|endometriosis|adenomyosis|"
    r"fibroids?|thyroid (?:disease|disorder|problem)|hypothyroidism|hyperthyroidism|an?a?emia|anaemic|anemic|"
    r"infertil(?:e|ity)|an ectopic pregnancy|ectopic|a miscarriage|cancer|pelvic inflammatory disease|pid"
)

_EN_PATTERNS: list[tuple[str, str]] = [
    (
        "diagnosis",
        rf"\byou(?:'ve| have| probably have| likely have| definitely have| certainly have| clearly have)\s+(?:got\s+)?(?:an?\s+)?(?:{_CONDITIONS_EN})\b",
    ),
    (
        "diagnosis",
        rf"\byou(?:'re| are)\s+(?:probably |likely |definitely |clearly )?(?:suffering from|diagnosed with)\s+(?:{_CONDITIONS_EN})\b",
    ),
    ("diagnosis", r"\b(?:this|these|that|your (?:data|cycles?|symptoms?|pattern))\s+(?:proves?|confirms?|means you have)\b"),
    ("diagnosis", r"\byou definitely have\b"),
    # Bare "you are pregnant" is only allowed when the user's data records a confirmed pregnancy.
    ("pregnancy_claim", r"\byou(?:'re| are)\s+pregnant\b"),
    (
        "pregnancy_status",
        r"\byou(?:'re| are)\s+(?:definitely|probably|likely|most likely|certainly|not|definitely not|probably not)\s+pregnant\b",
    ),
    (
        "pregnancy_status",
        r"\byou can(?:not|'t) be pregnant\b|\b(?:a|your) late period (?:means|confirms|shows) (?:that )?you(?:'re| are) pregnant\b",
    ),
    ("fertility_status", r"\byou(?:'re| are)\s+(?:infertile|fertile)\b|\byou (?:can(?:not|'t)|will never) (?:get|become) pregnant\b"),
    ("dismissal", r"\byou(?:'re| are)\s+(?:completely |totally |perfectly )?(?:fine|healthy|normal)\b"),
    ("dismissal", r"\b(?:there(?:'s| is)\s+)?nothing to worry about\b"),
    (
        "dismissal",
        r"\byou (?:don't|do not|won't|will not) need (?:to see |to visit |to contact )?(?:a |any )?(?:doctor|gp|gynaecologist|gynecologist|medical (?:help|care|attention))\b",
    ),
    ("dismissal", r"\b(?:no need|not necessary) to (?:see|visit|contact) (?:a |any )?(?:doctor|gp|gynaecologist|gynecologist)\b"),
    ("dismissal", r"\b(?:ignore|disregard|dismiss) (?:this|the|that|your) (?:alert|warning|finding)\b"),
    ("guarantee", r"\byour (?:next )?period will (?:definitely|certainly) (?:start|come|arrive)\b"),
    ("guarantee", r"\byou (?:definitely|certainly) (?:ovulated|will ovulate)\b"),
    (
        "prescription",
        r"\byou should (?:start|take|increase|double|reduce|stop|try) (?:taking )?(?:\w+ ){0,2}(?:metformin|clomi(?:ph|f)ene|letrozole|tranexamic acid|mefenamic acid|norethisterone|levothyroxine|progesterone|the (?:combined )?pill|birth control)\b",
    ),
    ("prescription", r"\b(?:increase|decrease|double|halve|change) (?:your|the) (?:dose|dosage)\b"),
]

_CONDITIONS_AR = r"(?:تكيس|تكيّس|متلازمة تكيس|بطانة الرحم المهاجرة|انتباذ|الانتباذ البطاني|العضال الغدي|ألياف|أورام ليفية|الغدة الدرقية|فقر الدم|أنيميا|انيميا|العقم|حمل خارج الرحم|سرطان|إجهاض)"

_AR_PATTERNS: list[tuple[str, str]] = [
    ("diagnosis", rf"(?:أنتِ|انتِ|أنت|انت|انتي|إنتي)\s+(?:مصابة|تعانين|عندك|لديك|لديكِ)\s+(?:ب|من\s+)?\s*{_CONDITIONS_AR}"),
    ("diagnosis", rf"(?:عندك|عندِك|لديك|لديكِ)\s+(?:بالتأكيد\s+|أكيد\s+|غالبًا\s+|غالبا\s+)?{_CONDITIONS_AR}"),
    ("diagnosis", rf"(?:مصابة|تعانين من)\s+(?:بالتأكيد\s+)?(?:ب)?{_CONDITIONS_AR}"),
    ("diagnosis", r"(?:هذا|ده|دي|هذه)\s+(?:يثبت|يؤكد|بيأكد|بيثبت)\s+(?:أن|إن|ان)ك"),
    ("pregnancy_claim", r"(?:أنتِ|انتِ|أنت|انت|انتي|إنتي)\s+(?:حامل|حاملًا|حاملا)"),
    ("pregnancy_status", r"(?:أنتِ|انتِ|أنت|انت|انتي|إنتي)\s+(?:بالتأكيد|أكيد|غالبًا|غالبا|مش|غير)\s+(?:حامل|حاملًا|حاملا)"),
    ("pregnancy_status", r"(?:لستِ|لست|مش)\s+حامل"),
    ("fertility_status", r"(?:لن|مش هت|مش حت)\s*(?:تستطيعي|تقدري|تقدرين)\s+(?:أن\s+)?(?:تحملي|الحمل)"),
    ("dismissal", r"لا\s+داعي\s+(?:للقلق|لزيارة الطبيب|لمراجعة الطبيب|للطبيب)"),
    ("dismissal", r"(?:مفيش|ما فيش|لا يوجد)\s+(?:حاجة|شيء|أي شيء)\s+(?:تقلق|يقلق|يدعو للقلق|مقلق)"),
    (
        "dismissal",
        r"(?:مش|لستِ|لست|غير)\s+(?:محتاجة|بحاجة|في حاجة)\s+(?:ل|إلى\s+|الى\s+)?(?:دكتور|دكتورة|طبيب|طبيبة|الطبيب|الدكتور|الدكتورة)",
    ),
    ("dismissal", r"(?:أنتِ|انتِ|أنت|انت|انتي|إنتي)\s+(?:سليمة|بخير|كويسة)\s*(?:تمامًا|تماما|خالص|ومفيش)"),
    ("dismissal", r"(?:تجاهلي|تجاهل|اتجاهلي)\s+(?:هذا\s+)?(?:التنبيه|التحذير)"),
]

_NEGATION_PREFIX = re.compile(
    r"(?:\bif\b|\bwhether\b|\bdoes not mean\b|\bdoesn't mean\b|\bnot mean\b|\bcannot say\b|\bcan't say\b|\bwithout saying\b|لا يعني|لا تعني|مش معناه|ليس معناه|لا أستطيع أن أقول|لا يمكنني القول|إذا|لو)\s*[^.!?؟\n]{0,40}$",
    re.IGNORECASE,
)

_COMPILED = [(k, re.compile(p, re.IGNORECASE)) for k, p in _EN_PATTERNS] + [(k, re.compile(p)) for k, p in _AR_PATTERNS]


@dataclass
class Violation:
    kind: str
    match: str


def validate_ai_response(text: str, *, pregnancy_confirmed: bool = False) -> list[Violation]:
    """Return prohibited-claim violations found in an AI response.

    `pregnancy_confirmed` — the user's own data records a confirmed pregnancy, so the
    model may refer to it ("because you are pregnant, ..."). It still may never
    speculate ("you are probably pregnant") or deny it ("you are not pregnant").
    """
    violations: list[Violation] = []
    for kind, rx in _COMPILED:
        if kind == "pregnancy_claim" and pregnancy_confirmed:
            continue
        for m in rx.finditer(text):
            prefix = text[max(0, m.start() - 60) : m.start()]
            # Allow hedged/negated/conditional contexts: "this does not mean you have PCOS",
            # "if you are pregnant, ...", "لا يعني أنكِ مصابة ...".
            if _NEGATION_PREFIX.search(prefix):
                continue
            violations.append(Violation(kind, m.group(0)))
    return violations


# ---------------------------------------------------------------------------
# Emergency screen on USER input
# ---------------------------------------------------------------------------

_EMERGENCY_EN = [
    r"\b(?:i|i've|i have|she|she's)\s+(?:just\s+)?(?:fainted|passed out|blacked out|lost consciousness)\b",
    r"\bfainting\b.*\b(?:bleed|blood)",
    r"\b(?:bleed|blood)\w*\b.*\bfaint",
    r"\bsoak(?:ing|ed)? (?:through )?(?:a |my )?(?:pad|tampon)s? (?:every|in (?:less than )?an?) hour\b",
    # Only first-person / current pregnancy — not "can I get pregnant if ...?"
    r"\b(?:i'?m|i am|currently|while|during my|weeks?)\s+pregnan\w*\b.*\b(?:bleed\w*|blood|spotting|severe pain|shoulder pain|dizzy|faint\w*)",
    r"\b(?:bleed\w*|blood|spotting|severe pain|shoulder pain|dizzy|faint\w*)\b.*\b(?:i'?m|i am|while|and)\s+pregnant\b",
    r"\bcan(?:not|'t) (?:stand up|breathe)\b",
    r"\b(?:unbearable|worst ever|excruciating) pain\b",
    r"\bsuicid\w*|\bkill myself\b|\bend my life\b",
]
_EMERGENCY_AR = [
    r"(?:أغمي|اغمي|أُغمي|أغمى|اغمى|فقدت الوعي|فقدت الوعى|وقعت من طولي|اتغمى|أغمى علي)",
    r"(?:أنا حامل|انا حامل|وأنا حامل|وانا حامل|حامل في (?:الشهر|الأسبوع|الاسبوع)|خلال الحمل|أثناء الحمل|اثناء الحمل)[^.؟!\n]*(?:نزيف|نزف|دم|ألم شديد|وجع شديد|دوخة|دايخة|ألم في الكتف|وجع في الكتف)",
    r"(?:نزيف|نزف|دم|ألم شديد|وجع شديد|دوخة|دايخة)[^.؟!\n]*(?:وأنا حامل|وانا حامل|أنا حامل|انا حامل|أثناء الحمل|اثناء الحمل)",
    r"(?:نزيف|نزف)\s+(?:شديد|غزير|جامد)\s+(?:جدًا|جدا|أوي|اوي)",
    r"(?:بغير|بغيّر)\s+(?:الفوطة|فوطة|الفوط)\s+كل\s+(?:ساعة|نص ساعة|نصف ساعة)",
    r"(?:مش قادرة|لا أستطيع)\s+(?:أتنفس|اتنفس|أقف|اقف)",
    r"(?:ألم|وجع)\s+(?:لا يُحتمل|لا يحتمل|مش محتمل|فظيع)",
    r"(?:انتحار|أنتحر|انتحر|أموت نفسي|أنهي حياتي)",
]
_EMERGENCY_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _EMERGENCY_EN] + [re.compile(p, re.DOTALL) for p in _EMERGENCY_AR]
_SELF_HARM = re.compile(r"suicid|kill myself|end my life|انتحار|أنتحر|انتحر|أموت نفسي|أنهي حياتي", re.IGNORECASE)


@dataclass
class ScreenResult:
    is_emergency: bool
    category: str | None = None  # "physical" | "self_harm"


def screen_user_message(text: str) -> ScreenResult:
    if _SELF_HARM.search(text):
        return ScreenResult(True, "self_harm")
    for rx in _EMERGENCY_COMPILED:
        if rx.search(text):
            return ScreenResult(True, "physical")
    return ScreenResult(False)


_ARABIC_CHARS = re.compile(r"[\u0600-\u06FF]")


def detect_language(text: str, default: str = "ar") -> str:
    if _ARABIC_CHARS.search(text):
        return "ar"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return default


SELF_HARM_MESSAGE = {
    "en": "It sounds like you may be going through something very difficult. You deserve support right now. If you are in immediate danger, please call your local emergency number. You can also reach out to a crisis line or someone you trust.",
    "ar": "يبدو أنكِ تمرّين بوقت صعب جدًا، وتستحقين الدعم الآن. إذا كنتِ في خطر مباشر، يرجى الاتصال برقم الطوارئ المحلي. يمكنك أيضًا التواصل مع خط دعم نفسي أو شخص تثقين به.",
}
