#!/usr/bin/env python3
"""تدقيق المصطلحات: `python tools/check_glossary.py [--path ...] [--fix-report ...]`

glossary_ar.csv هو المصدر الوحيد للمصطلح (single source of truth). هذا السكربت:

1. يتحقق من صحة الملف نفسه (أعمدة، تكرار، فراغات، needs_review صريح).
2. يبحث في نصوص المستخدم (locales/*.json، knowledge/*.jsonl، نصوص الردود في
   locales) عن المصطلحات **غير المفضّلة** ويفشل إن وُجدت.
3. ينبّه على مصطلح إنجليزي بلا مقابل عربي، وعلى مصطلح `needs_review=true`
   لم يُراجَع بعد.

لا يُعدّل شيئًا: الفشل يعني أن على إنسان أن يصحّح النص أو يعتمد المصطلح.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# قاموس صاحبة المشروع يصل في حزمة kb/؛ ونسخة المستودع الحالية بديل مؤقت حتى
# تصل الحزمة. الأولوية دائمًا لملف الحزمة لأنه مصدر الحقيقة المعلن.
GLOSSARY_PATHS = (Path("kb/knowledge/glossary_ar.csv"),
                  Path("knowledge/glossary_ar.csv"))


def resolve_glossary_path() -> Path:
    for candidate in GLOSSARY_PATHS:
        if candidate.exists():
            return candidate
    return GLOSSARY_PATHS[0]


GLOSSARY_PATH = resolve_glossary_path()

# صيغتان مدعومتان:
# 1) صيغة حزمة صاحبة المشروع: english, arabic_preferred_user_facing,
#    arabic_formal_alt, note — وهي المصدر المعلن، ولا تُعدَّل أبدًا في الكود.
#    لا تحمل قائمة «ممنوع»: الصيغة الرسمية بديل مسموح (سجل مختلف) لا خطأ.
# 2) صيغة المستودع القديمة: term_en, preferred_ar, not_preferred_ar, notes,
#    needs_review — تبقى مدعومة للاختبارات ولأي قاموس لاحق بمعجم ممنوع.
PACK_COLUMNS = ("english", "arabic_preferred_user_facing", "arabic_formal_alt", "note")
LEGACY_COLUMNS = ("term_en", "preferred_ar", "not_preferred_ar", "notes", "needs_review")
REQUIRED_COLUMNS = LEGACY_COLUMNS  # للتوافق مع ما يستورده غيرنا

# نصوص يخضع لها التدقيق: ما يراه المستخدم فعليًا.
SCAN_GLOBS = ("locales/*.json", "knowledge/*.jsonl", "store/*.md")


@dataclass
class GlossaryTerm:
    term_en: str
    preferred_ar: str
    not_preferred: list[str] = field(default_factory=list)
    notes: str = ""
    needs_review: bool = False


@dataclass
class Issue:
    level: str            # error | warning
    where: str
    message: str

    def __str__(self) -> str:
        icon = "✗" if self.level == "error" else "!"
        return f"{icon} [{self.where}] {self.message}"


def load_glossary(path: Path | None = None) -> tuple[list[GlossaryTerm], list[Issue]]:
    path = Path(path) if path is not None else resolve_glossary_path()
    issues: list[Issue] = []
    if not path.exists():
        return [], [Issue("error", str(path), "ملف المصطلحات غير موجود")]

    terms: list[GlossaryTerm] = []
    seen_preferred: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = set(reader.fieldnames or [])
        if set(PACK_COLUMNS) <= columns:
            return _load_pack_glossary(path, reader)
        missing = [c for c in LEGACY_COLUMNS if c not in columns]
        if missing:
            return [], [Issue("error", str(path),
                              f"أعمدة ناقصة: {', '.join(missing)} "
                              f"(أو أعمدة الحزمة: {', '.join(PACK_COLUMNS)})")]
        for lineno, row in enumerate(reader, start=2):
            if not (row.get("term_en") or "").strip():
                issues.append(Issue("error", f"{path}:{lineno}", "مصطلح إنجليزي فارغ"))
                continue
            preferred = (row.get("preferred_ar") or "").strip()
            if not preferred:
                issues.append(Issue("error", f"{path}:{lineno}",
                                    f"لا مقابل عربي للمصطلح {row['term_en']}"))
            flag = (row.get("needs_review") or "").strip().lower()
            if flag not in ("true", "false"):
                issues.append(Issue("error", f"{path}:{lineno}",
                                    f"needs_review يجب أن تكون true/false لا «{flag}»"))
            if preferred:
                if preferred in seen_preferred:
                    issues.append(Issue(
                        "error", f"{path}:{lineno}",
                        f"«{preferred}» مستخدم لمصطلحين: "
                        f"{seen_preferred[preferred]} و{row['term_en']}"))
                seen_preferred[preferred] = row["term_en"]
            terms.append(GlossaryTerm(
                term_en=row["term_en"].strip(),
                preferred_ar=preferred,
                not_preferred=[t.strip() for t in
                               (row.get("not_preferred_ar") or "").split("|") if t.strip()],
                notes=(row.get("notes") or "").strip(),
                needs_review=flag == "true",
            ))
    return terms, issues


def _load_pack_glossary(path: Path, reader: "csv.DictReader") -> tuple[list[GlossaryTerm], list[Issue]]:
    """يقرأ قاموس صاحبة المشروع كما هو.

    الصيغة الرسمية البديلة (`arabic_formal_alt`) **مسموحة** ولا تُعدّ خطأ: هي
    سجل لغوي آخر (فصحى/مصطلح مرجعي)، وليست صيغة مرفوضة. لذا `not_preferred`
    تبقى فارغة، والتدقيق يتوقف على: تكرار الصيغة المفضّلة، وغياب مقابل عربي،
    وكل صف يُعلَّم `needs_review=true` لأن الملف لا يحمل حالة مراجعة بشرية —
    ولا نخترع حالة «مُراجَع» لم يكتبها إنسان.
    """
    terms: list[GlossaryTerm] = []
    issues: list[Issue] = []
    seen_preferred: dict[str, str] = {}
    for lineno, row in enumerate(reader, start=2):
        english = (row.get("english") or "").strip()
        preferred = (row.get("arabic_preferred_user_facing") or "").strip()
        if not english:
            issues.append(Issue("error", f"{path}:{lineno}", "مصطلح إنجليزي فارغ"))
            continue
        if not preferred:
            issues.append(Issue("error", f"{path}:{lineno}",
                                f"لا مقابل عربي للمصطلح {english}"))
        elif preferred in seen_preferred:
            issues.append(Issue(
                "error", f"{path}:{lineno}",
                f"«{preferred}» مستخدم لمصطلحين: {seen_preferred[preferred]} و{english}"))
        if preferred:
            seen_preferred[preferred] = english
        terms.append(GlossaryTerm(
            term_en=english, preferred_ar=preferred,
            not_preferred=[],
            notes=(row.get("note") or "").strip(),
            needs_review=True,
        ))
    return terms, issues


def scan_text(terms: list[GlossaryTerm], text: str, where: str) -> list[Issue]:
    """يبحث عن الصيغ غير المفضّلة. المطابقة على كلمة/عبارة كاملة لا جزئية.

    السبب: «الدم» جزء من «جلطة دموية»، والمطابقة الجزئية تُنتج إنذارات كاذبة
    تُجعل الفحص يُتجاهل. نستخدم حدودًا عربية (\b لا تعمل مع العربية بثقة).
    """
    issues: list[Issue] = []
    for term in terms:
        for bad in term.not_preferred:
            pattern = r"(?<![\u0600-\u06FF])" + re.escape(bad) + r"(?![\u0600-\u06FF])"
            if re.search(pattern, text):
                issues.append(Issue(
                    "error", where,
                    f"صيغة غير مفضّلة «{bad}» — المعتمد: «{term.preferred_ar}» "
                    f"({term.term_en})"))
    return issues


def scan_files(terms: list[GlossaryTerm], root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for pattern in SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.name == "glossary_ar.csv":
                continue
            text = path.read_text(encoding="utf-8-sig")
            if path.suffix == ".json":
                # نفحص القيم النصية فقط: أسماء المفاتيح إنجليزية بالتصميم
                issues += scan_text(terms, json_text_values(path), str(path))
            else:
                issues += scan_text(terms, text, str(path))
    return issues


def json_text_values(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    collected: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("_"):
                    continue          # مفاتيح التعليقات الداخلية
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            collected.append(node)

    walk(data)
    return "\n".join(collected)


def needs_review_terms(terms: list[GlossaryTerm]) -> list[GlossaryTerm]:
    return [t for t in terms if t.needs_review]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="تدقيق مصطلحات الترجمة")
    parser.add_argument("--path", default=str(GLOSSARY_PATH))
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default=None, help="كتابة قائمة needs_review")
    args = parser.parse_args(argv)

    terms, issues = load_glossary(Path(args.path) if args.path else None)
    issues += scan_files(terms, Path(args.root))

    pending = needs_review_terms(terms)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(
            [{"term_en": t.term_en, "preferred_ar": t.preferred_ar, "notes": t.notes}
             for t in pending], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"مصطلحات: {len(terms)} | بانتظار مراجعة بشرية: {len(pending)}")
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for issue in errors + warnings:
        print(issue)

    if errors:
        print(f"\nفشل: {len(errors)} خطأ.", file=sys.stderr)
        return 1
    print("المصطلحات سليمة: لا صيغة غير مفضّلة في نصوص المستخدم.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
