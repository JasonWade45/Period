#!/usr/bin/env python3
"""تدقيق RTL: `python tools/check_rtl.py`

يفحص ما يمكن فحصه بلا متصفح (يفشل البناء عند الخطأ):
1. `dir` و`lang` مضبوطان على <html>، ويُغيّران مع اللغة.
2. لا خصائص CSS فيزيائية في ملفات RTL الجديدة (left/right/float) — تُستخدم
   الخصائص المنطقية (inline-start/inline-end).
3. أرقام الهواتف تُعرض داخل عنصر عزل اتجاهي (`phone-number`/`data-ltr`)
   وإلا قلبها BiDi داخل جملة عربية.
4. لا تباعد أحرف ولا ميل للنص العربي.
5. الأيقونات الاتجاهية مُعلَّمة `flip-rtl` وغير الاتجاهية `no-flip`.

لا يتحقق من الشكل النهائي (ذلك يحتاج متصفحًا): انظر
`frontend/tests/rtl.spec.js` — اختبار Playwright جاهز للتشغيل في CI مع لقطات.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PHYSICAL_PROPS = re.compile(
    r"(?<![\w-])(margin|padding|border|inset)?-?(left|right)\s*:", re.IGNORECASE)
ALLOWED_PHYSICAL = {"style.css"}      # التصميم الأصلي قائم؛ التنبيه لا الخطأ


@dataclass
class Issue:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{'✗' if self.level == 'error' else '!'} [{self.where}] {self.message}"


def check_html(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    text = path.read_text(encoding="utf-8")
    root = re.search(r"<html[^>]*>", text)
    if not root:
        return [Issue("error", str(path), "لا وسم <html>")]
    tag = root.group(0)
    if 'dir="rtl"' not in tag:
        issues.append(Issue("error", str(path),
                            "html يفتقد dir=rtl (العربية هي الافتراضية)"))
    if not re.search(r'lang="ar', tag):
        issues.append(Issue("error", str(path), "html يفتقد lang=ar"))

    # كل عنصر يعرض رقمًا إحصائيًا/هاتفيًا يجب أن يُعزل
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "phone" in line.lower() or "emergency" in line.lower():
            if "data-ltr" not in line and "phone-number" not in line \
                    and "ltr-isolate" not in line:
                issues.append(Issue(
                    "warning", f"{path}:{lineno}",
                    "سطر يحتوي رقمًا/طوارئ بلا عزل اتجاهي (قد ينقلبه BiDi)"))

    if "i18n.js" not in text:
        issues.append(Issue("error", str(path), "لا تحميل لملف التعريب i18n.js"))
    if "rtl.css" not in text:
        issues.append(Issue("error", str(path), "لا تحميل لملف قواعد RTL"))
    return issues


def check_css(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith(("/*", "*", "//")):
            continue
        if PHYSICAL_PROPS.search(line):
            level = "warning" if path.name in ALLOWED_PHYSICAL else "error"
            issues.append(Issue(
                level, f"{path}:{lineno}",
                f"خاصية فيزيائية في RTL: «{line.strip()[:50]}» — استخدمي "
                "inline-start/inline-end"))

    if path.name == "rtl.css":
        if ".phone-number" not in text or "unicode-bidi: isolate" not in text:
            issues.append(Issue("error", str(path), "لا عزل اتجاهي لأرقام الهواتف"))
        if "letter-spacing" not in text:
            issues.append(Issue("error", str(path), "لا منع لتباعد الأحرف في العربية"))
        if "scaleX(-1)" not in text:
            issues.append(Issue("error", str(path), "لا عكس للأيقونات الاتجاهية"))
    return issues


def check_js(path: Path) -> list[Issue]:
    issues: list[Issue] = []
    text = path.read_text(encoding="utf-8")
    if path.name == "i18n.js":
        for needle, message in (
            ("document.documentElement", "لا ضبط للاتجاه على <html>"),
            ("arabic_indic", "لا دعم للأرقام العربية-الهندية"),
            ("state.dir =", "لا اشتقاق للاتجاه من اللغة"),
            ('"/locales/"', "لا قراءة لملفات الموارد من الخادم"),
        ):
            if needle not in text:
                issues.append(Issue("error", str(path), message))
    if path.name == "app.js":
        # نصوص واجهة مكتوبة في JS بدل ملفات الموارد (عدّ إرشادي)
        literals = re.findall(r'"([^"\n]*[\u0600-\u06FF]{3,}[^"\n]*)"', text)
        # الاستثناءات: نصوص احتياطية داخل i18nText(..) أو تعليقات
        fallbacks = len(re.findall(r'i18nText\("[^"]+",\s*"[^"]+"\)', text))
        remaining = len(literals) - fallbacks
        if remaining > 0:
            issues.append(Issue(
                "warning", str(path),
                f"{remaining} نص عربي مكتوب في JS (منها {fallbacks} احتياطي داخل "
                "i18nText) — المطلوب نقلها إلى locales/ تدريجيًا"))
    return issues


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):  # ويندوز cp1256: إخراج UTF-8
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — بيئة بلا reconfigure
            pass
    parser = argparse.ArgumentParser(description="تدقيق RTL")
    parser.add_argument("--root", default="frontend")
    args = parser.parse_args(argv)

    root = Path(args.root)
    issues: list[Issue] = []
    for html in sorted(root.glob("*.html")):
        issues += check_html(html)
    for css in sorted(root.glob("*.css")):
        issues += check_css(css)
    for js in sorted(root.glob("*.js")):
        issues += check_js(js)

    spec = root / "tests" / "rtl.spec.js"
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for issue in errors + warnings:
        print(issue)
    if not spec.exists():
        print("! لا اختبار لقطات RTL (frontend/tests/rtl.spec.js)")
        warnings.append(Issue("warning", str(spec), "اختبار اللقطات مفقود"))

    print(f"\nRTL: {len(errors)} خطأ، {len(warnings)} تنبيه.")
    if errors:
        return 1
    print("الفحوص الساكنة سليمة. اللقطات البصرية تحتاج متصفحًا: "
          "npx playwright test frontend/tests/rtl.spec.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
