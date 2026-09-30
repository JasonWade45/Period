#!/usr/bin/env python3
"""فحص ملف برومبت نظام مقابل «عقد» التطبيق قبل اعتماده.

الغرض: البرومبت ليس نصًا حرًا — الباكإند يملأ فيه متغيّرات محددة، والـ validator
يفرض حقول JSON بعينها، وردود الطوارئ تشترط جملًا ثابتة. تغيير البرومبت بلا فحص
يكسر البايبلاين بصمت (KeyError عند البناء، أو ردود يرفضها الـ validator فتصير
«إجابة احتياطية» لكل المستخدمات).

    python tools/check_prompt.py                       # يفحص البرومبت الحالي
    python tools/check_prompt.py path/to/candidate.md  # يفحص مرشّحًا قبل استبداله

يُرجع 0 إن اجتاز، و1 إن فشل، و2 إن تعذّر تحميل العقد.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.emergency_filter import (  # noqa: E402
    CRISIS_FIRST_SENTENCE,
    EMERGENCY_FIRST_SENTENCE,
)
from app.services.prompt_builder import _VAR_PATTERN, ALLOWED_MODES  # noqa: E402
from app.services.validator import CHAT_FIELDS, SUMMARY_FIELDS  # noqa: E402

# المتغيّرات التي يملأها PromptBuilder.build فعليًا
REQUIRED_VARIABLES = {
    "MODE", "USER_CONTEXT", "FINDINGS", "SOURCES", "RULE_GLOSSARY",
    "CURRENT_DATE", "EMERGENCY_NUMBER", "CRISIS_LINE",
}

# أنماط ممنوعة في البرومبت: وصف دواء موجّه، أو وعود تشخيصية
RISKY_PATTERNS = [
    (r"خذي|خدي|تاخدي|تاخذي", "توجيه دوائي مباشر («خذي/خدي…»)"),
    (r"\b(?:mg|ملغ|مجم)\b", "ذكر جرعة بالأرقام"),
    (r"أكّدي|أكدي\s+(?:أن|ان)\s+(?:عندها|عندك)", "تأكيد تشخيص للمستخدمة"),
]


@dataclass
class Report:
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_prompt(text: str) -> Report:
    report = Report(path="")
    if not text.strip():
        report.errors.append("البرومبت فارغ")
        return report

    # 1) المتغيّرات: كل المطلوب موجود، ولا يوجد متغيّر مجهول (يكسر _render)
    present = set(_VAR_PATTERN.findall(text))
    for name in sorted(REQUIRED_VARIABLES - present):
        report.errors.append(f"متغيّر مطلوب غائب عن البرومبت: {{{{{name}}}}}")
    for name in sorted(present - REQUIRED_VARIABLES):
        report.errors.append(
            f"متغيّر غير معروف: {{{{{name}}}}} — PromptBuilder لا يملؤه، "
            "وسيرفع ValueError عند أول طلب"
        )

    # 2) جمل الطوارئ الثابتة: الباكإند يبني ردًّا ثابتًا بها، والبرومبت يجب أن
    #    يأمر الموديل بنفس الصياغة حتى لا تختلف رسالة الطوارئ حسب مسار الرد.
    for label, sentence in (("الطوارئ الطبية", EMERGENCY_FIRST_SENTENCE),
                            ("الأزمة النفسية", CRISIS_FIRST_SENTENCE)):
        if sentence not in text:
            report.errors.append(f"جملة {label} الأولى غير موجودة حرفيًا: «{sentence}»")

    # 3) حقول JSON التي يفرضها الـ validator
    for mode, spec in (("chat", CHAT_FIELDS), ("summary", SUMMARY_FIELDS)):
        missing = [name for name in spec if name not in text]
        if missing:
            report.errors.append(
                f"حقول وضع {mode} غير مذكورة في البرومبت: {', '.join(missing)} "
                "(الـ validator يرفض أي رد ينقصه حقل)"
            )

    # 4) الأوضاع المدعومة
    for mode in sorted(ALLOWED_MODES):
        if mode not in text:
            report.warnings.append(f"الوضع «{mode}» غير مذكور في البرومبت")

    # 5) أنماط خطرة نصيًا — بعد إزالة المقتبسات «…» لأن البرومبت يذكر صيغًا
    #    ممنوعة كمثال على المنع («خذي كذا») وليس كتعليمات للموديل.
    unquoted = re.sub(r"«[^»]*»", " ", text)
    for pattern, why in RISKY_PATTERNS:
        if re.search(pattern, unquoted):
            report.warnings.append(f"نمط مثير للانتباه: {why}")

    # 6) ما لا يستطيع الـ validator فرضه لكن وجوده مطلوب عمليًا
    checks = [
        ("بيانات وليس تعليمات", "حماية من التلاعب في الرسائل وحقول السياق"),
        ("ليست تشخيصًا", "تنبيه صريح بأن النتائج ليست تشخيصًا"),
        ("JSON", "تقييد صيغة الإخراج"),
        ("sources_used", "الإسناد لمعرّفات المصادر المسموح بها"),
    ]
    for needle, why in checks:
        if needle not in text:
            report.warnings.append(f"لا يوجد ذكر لـ «{needle}» ({why})")

    report.notes.append(f"طول البرومبت: {len(text)} حرفًا، {text.count(chr(10)) + 1} سطرًا")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="فحص برومبت مقابل عقد التطبيق")
    parser.add_argument("path", nargs="?", default=str(settings.prompt_path))
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"خطأ: الملف غير موجود — {target}", file=sys.stderr)
        return 2

    report = check_prompt(target.read_text(encoding="utf-8"))
    print(f"فحص: {target}")
    print(f"العقد: متغيّرات {len(REQUIRED_VARIABLES)} | حقول chat "
          f"{len(CHAT_FIELDS)} | حقول summary {len(SUMMARY_FIELDS)}")
    for note in report.notes:
        print(f"  · {note}")
    if report.warnings:
        print("\nتحذيرات:")
        for w in report.warnings:
            print(f"  ! {w}")
    if report.errors:
        print("\nأخطاء تمنع الاعتماد:")
        for e in report.errors:
            print(f"  ✗ {e}")
        return 1
    print("\n✓ اجتاز فحص العقد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
