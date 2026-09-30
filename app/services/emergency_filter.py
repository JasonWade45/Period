from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from ..schemas import Finding, Severity

# ---------------------------------------------------------------------
# كلمات مفتاحية: فصحى + عامية مصرية + إنجليزي
# التحقق الفعلي يتم بالـ classifier في الإنتاج؛ هذه الطبقة الأولى.
# ---------------------------------------------------------------------

MEDICAL_PATTERNS: list[str] = [
    # فصحى
    "نزيف شديد", "نزيف غزير", "أغزر من", "يغرق", "كل ساعة", "جلطات دم",
    "إغماء", "اغماء", "فقدان الوعي", "دوخة شديدة", "ألم شديد",
    "ألم مفاجئ", "ضيق تنفس", "صعوبة تنفس", "ألم في الصدر", "وجع صدر",
    "حمل خارج الرحم", "طلق",
    # عامية مصرية
    "بنزف", "بنزيف", "بنزل جلطات", "مغشي عليها",
    "دوخة قوية", "وجع قوي في بطني",
    # إنجليزي
    "heavy bleeding", "soaking through", "fainting", "fainted",
    "severe pain", "chest pain", "trouble breathing", "shortness of breath",
    "shoulder pain", "emergency", "can't breathe", "cant breathe",
]

CRISIS_PATTERNS: list[str] = [
    # فصحى
    "إيذاء النفس", "ايذاء نفسي", "أريد أن أموت", "ابغى أموت",
    "لا أريد الحياة", "ما عادت الحياة تعجبني", "أنهي حياتي",
    # عامية مصرية
    "انتحار", "أموت", "اموت", "مش عايزة أعيش", "مبقتش عايزة أعيش",
    "مخنوقه ومش عايزة", "أخلص من كل ده", "خلصت من كل حاجة",
    # إنجليزي
    "suicide", "kill myself", "self harm", "hurt myself",
    "don't want to live", "want to die", "end my life",
]

EMERGENCY_FIRST_SENTENCE = "الأعراض التي ذكرتِها قد تحتاج رعاية طبية عاجلة"

CRISIS_FIRST_SENTENCE = "ما تشهدينه من أفكار إيذاء النفس يستحق دعمًا فوريًا"


@dataclass
class FilterResult:
    kind: Optional[str]  # "medical" | "crisis" | None
    matched: str = ""
    source: str = ""  # "message" | "findings"

    @property
    def triggered(self) -> bool:
        return self.kind is not None


def _find(patterns: Iterable[str], text: str) -> str:
    lowered = text.lower()
    for p in patterns:
        if p.lower() in lowered:
            return p
    return ""


def check_message(message: str) -> FilterResult:
    hit = _find(CRISIS_PATTERNS, message)
    if hit:
        return FilterResult(kind="crisis", matched=hit, source="message")
    hit = _find(MEDICAL_PATTERNS, message)
    if hit:
        return FilterResult(kind="medical", matched=hit, source="message")
    return FilterResult(kind=None)


def check_findings(findings: list[Finding]) -> FilterResult:
    if any(f.severity == Severity.EMERGENCY for f in findings):
        return FilterResult(kind="medical", matched="severity=EMERGENCY", source="findings")
    if any(f.severity == Severity.URGENT for f in findings):
        return FilterResult(kind="medical", matched="severity=URGENT", source="findings")
    return FilterResult(kind=None)


def run_filter(message: str, findings: list[Finding]) -> FilterResult:
    """فحص ما قبل الموديل: رسالة المستخدم ثم نتائج محرك القواعد."""
    result = check_message(message)
    if result.triggered:
        return result
    return check_findings(findings)


def build_fixed_reply(result: FilterResult, emergency_number: str, crisis_line: str) -> str:
    """رد ثابت — لا استدعاء للموديل. يشمل الأرقام حرفيًا."""
    if result.kind == "crisis":
        lines = [
            CRISIS_FIRST_SENTENCE,
            "",
            f"تواصلي فورًا مع شخص تثق به الآن، ولا تكوني وحدها.",
        ]
        if crisis_line:
            lines.append(f"خط الدعم: {crisis_line}")
        lines.append("لو كان هناك خطر مباشر على حياتكِ، اتصلي بالإسعاف فورًا.")
        if emergency_number:
            lines.append(f"الإسعاف: {emergency_number}")
        return "\n".join(lines)

    lines = [
        EMERGENCY_FIRST_SENTENCE,
        "",
        f"توجهي الآن لأقرب طوارئ أو اتصلي بالإسعاف على {emergency_number} ولا تنتظري ردًّا آخر.",
        "حاولي ألا تكوني وحدها إن أمكن.",
    ]
    return "\n".join(lines)
