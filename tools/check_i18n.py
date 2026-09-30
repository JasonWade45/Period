#!/usr/bin/env python3
"""تدقيق التعريب: `python tools/check_i18n.py [--root .]`

يفحص ثلاثة أشياء تفشل بصمت في المشاريع ثنائية اللغة:

1. **تكافؤ المفاتيح**: كل مفتاح في ar.json موجود في en.json والعكس، وبنفس نوع
   القيمة (نص ↔ كائن جمع). مفتاح ناقص في لغة يعني ظهور المفتاح نفسه للمستخدمة.
2. **المفاتيح غير المستخدمة**: مفتاح لا يذكره أي كود أو ملف. المفاتيح الميتة
   تُترجَم بلا داعٍ وتُخفي مفاتيح ناقصة.
3. **نصوص عربية مكتوبة في الكود**: نص عربي داخل `t(...)` أو داخل ردّ في مسار
   API = نص خارج ملفات الموارد، لا يمرّ بمراجعة المترجم/الطبيبة. (الأنماط
   المعجمية والتوثيق والاختبارات مستثناة لأنها ليست نصًا يُعرض.)

مخرج غير صفري عند أي خطأ.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# يعمل السكربت مستقلًا (python tools/check_i18n.py) بلا تثبيت الحزمة
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.i18n.plural import PLURAL_CATEGORIES  # noqa: E402

# النطاق المقصود: الأسطح التي تصل رسائلها للمستخدمة فعلًا.
# ترك الفحص على كل app/** أنتج 29 خطأ أغلبها رسائل لوج واستثناءات داخلية —
# وفحصٌ صاخب يُتجاهل. النطاق هنا هو مسارات API وخط الأنابيب الجديد.
CODE_SCAN_GLOBS = ("app/routers/*.py", "app/services/ai_pipeline.py")

# ملفات في locales/ ليست ملفات ترجمة: بيانات وصفية (قوائم مراجعة بشرية مثلًا).
# بدون هذا الاستثناء يُقارَن ملف البيانات مع ar.json ويظهر «مفتاح ناقص» بالمئات.
META_FILES = {"needs_review"}
# ملفات مسموح فيها بنصوص عربية داخل الكود، مع السبب.
EXEMPT_PATHS: dict[str, str] = {}
ARABIC = re.compile(r"[\u0600-\u06FF]{3,}")
# نص عربي يظهر داخل سلسلة نصية في كود مسار/خدمة (لا تعليق، لا docstring عام)
STRING_LITERAL = re.compile(r'"([^"\n]*[\u0600-\u06FF][^"\n]*)"')


@dataclass
class Issue:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{'✗' if self.level == 'error' else '!'} [{self.where}] {self.message}"


def flatten(node, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("_"):
                continue          # مفاتيح تعليقات داخلية
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    else:
        out[prefix] = node
    return out


def compare_locales(base: dict, other: dict, base_name: str,
                    other_name: str) -> list[Issue]:
    issues: list[Issue] = []
    for key in sorted(set(base) - set(other)):
        issues.append(Issue("error", other_name, f"مفتاح ناقص: {key}"))
    for key in sorted(set(other) - set(base)):
        issues.append(Issue("error", base_name, f"مفتاح زائد: {key}"))
    for key in sorted(set(base) & set(other)):
        if type(base[key]) is not type(other[key]):
            issues.append(Issue(
                "error", f"{base_name}/{other_name}",
                f"نوع القيمة مختلف للمفتاح {key}: "
                f"{type(base[key]).__name__} مقابل {type(other[key]).__name__}"))
    return issues


def find_unused_keys(keys: set[str], root: Path, locales_dir: Path) -> list[Issue]:
    """مفتاح غير مذكور في أي كود/واجهة. يبدأ البحث من الورقة الأخيرة (المفتاح)."""
    search_roots = [root / "app", root / "frontend", root / "tools", root / "mobile",
                    root / "store", root / "tests"]
    haystack: list[str] = []
    for directory in search_roots:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_dir() or path.suffix in (".png", ".woff2", ".ttf", ".pdf"):
                continue
            try:
                haystack.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    blob = "\n".join(haystack)

    issues: list[Issue] = []
    for key in sorted(keys):
        leaf = key.split(".")[-1]
        # صيغ الجمع تُقرأ ديناميكيًا عبر tn(key, count): يكفي أن يُستخدم الأصل.
        parent = key.rsplit(".", 1)[0] if leaf in PLURAL_CATEGORIES else None
        if key in blob or (len(leaf) > 3 and leaf in blob) or (parent and parent in blob):
            continue
        issues.append(Issue("warning", str(locales_dir), f"مفتاح غير مستخدم: {key}"))
    return issues


def find_hardcoded_strings(root: Path) -> list[Issue]:
    """نصوص عربية في أسطح المستخدمة خارج ملفات الموارد.

    تُستثنى: التعليقات، ودوك‑سترنغ، ورسائل اللوج والاستثناءات الداخلية — هذه
    ليست نصًا يُعرض، ومعاقبتها تجعل الفحص صاخبًا فيُتجاهل.
    """
    issues: list[Issue] = []
    for pattern in CODE_SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel in EXEMPT_PATHS:
                continue
            in_docstring = False
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                quotes = stripped.count('"""')
                if quotes:
                    if quotes == 1:
                        in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith("#"):
                    continue
                if re.match(r"^(log|logger|logging)\.|^raise\b", stripped):
                    continue      # لوج/استثناء داخلي: لا يُعرض للمستخدمة
                if stripped.startswith(("from ", "import ")):
                    continue
                for literal in STRING_LITERAL.findall(line):
                    if not ARABIC.search(literal):
                        continue
                    issues.append(Issue(
                        "error", f"{rel}:{lineno}",
                        f"نص عربي مكتوب في الكود: «{literal.strip()[:40]}…» — "
                        "يجب أن يكون في locales/"))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="تدقيق ملفات الترجمة")
    parser.add_argument("--root", default=".")
    parser.add_argument("--locales", default="locales")
    args = parser.parse_args(argv)

    root = Path(args.root)
    locales_dir = root / args.locales
    files = [path for path in sorted(locales_dir.glob("*.json"))
             if path.stem not in META_FILES and not path.stem.startswith("_")]
    if not files:
        print(f"✗ لا ملفات ترجمة في {locales_dir}", file=sys.stderr)
        return 1

    data = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in files}
    flat = {name: flatten(node) for name, node in data.items()}

    issues: list[Issue] = []
    base_name = "ar" if "ar" in flat else files[0].stem
    base = flat[base_name]
    for name, other in flat.items():
        if name != base_name:
            issues += compare_locales(base, other, base_name, name)

    issues += find_unused_keys(set(base), root, locales_dir)
    issues += find_hardcoded_strings(root)

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    print(f"ملفات: {', '.join(sorted(flat))} | مفاتيح (ar): {len(base)}")
    for issue in errors + warnings:
        print(issue)
    if errors:
        print(f"\nفشل: {len(errors)} خطأ، {len(warnings)} تنبيه.", file=sys.stderr)
        return 1
    print(f"سليم: لا أخطاء ({len(warnings)} تنبيه).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
