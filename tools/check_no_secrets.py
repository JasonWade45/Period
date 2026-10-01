#!/usr/bin/env python3
"""فحص المفاتيح والبيانات في الملفات المتتبَّعة: `python tools/check_no_secrets.py`

خط دفاع محلي يكمّل gitleaks في CI — يفحص ما يتتبّعه git فعلًا (ملفاتك المحلية
غير المتتبَّعة لا تدخل الفحص):

1. مسارات ممنوعة أن تُرفع أبدًا: `.env` الحقيقي (المثال `.env.example` مسموح)،
   مفاتيح وشهادات (`*.key`, `*.pem`, `*.p12`, `*.pfx`)، قواعد بيانات صحية
   (`*.db`, `*.sqlite*`)، سجل التدقيق (`audit/`)، ومخلفات تشغيل محلية
   (`_dbg*`, `_live*`, `*.log`).
2. بصمات مفاتيح حقيقية داخل أي ملف نصري متتبَّع: `gsk_` (Groq)، `sk-`
   (OpenAI-style)، `AKIA` (AWS)، `ghp_`/`gho_`/`ghs_`/`ghr_` و`github_pat_`
   (GitHub)، `AIza` (Google)، وترويسات PEM للمفاتيح الخاصة.

الملفات ثنائية الحجم تُتخطّى (لا فحص نصي للخطوط/الصور — سطر واحد من بصمة
كاذب فيه أسوأ من تخطّيه). الأمثلة الضمنية قصيرة عمدًا (`gsk_xxx`) فلا تطابق
الأنماط. الخطأ يُعيد 1، والنظيف يُعيد 0.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_SUFFIXES = (
    ".key", ".pem", ".p12", ".pfx",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".log",
)
ALLOWED_SUFFIXES = (".env.example", ".env.sample")   # المثال ليس مفتاحًا
FORBIDDEN_PREFIXES = ("_dbg", "_live")               # مخرجات تشغيل محلية
FORBIDDEN_DIRS = ("audit/",)                         # سجل التدقيق لا يُرفع

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("مفتاح Groq (gsk_)", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("مفتاح بصيغة sk-", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("معرّف وصول AWS (AKIA)", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("رمز GitHub قديم (ghp_/gho_/ghs_/ghr_)",
     re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("مفتاح GitHub دقيق (github_pat_)",
     re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("مفتاح Google (AIza)", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("مفتاح خاص PEM",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
MAX_SCAN_BYTES = 4 * 1024 * 1024      # أكبر من ذلك = ملف بيانات لا كود


@dataclass
class Issue:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{'✗' if self.level == 'error' else '!'} [{self.where}] {self.message}"


def _tracked_files(root: Path) -> list[str] | None:
    """قائمة الملفات المتتبَّعة (مسارات نسبية بفواصل POSIX). None = git فشل."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root,
            capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def _path_issues(rel: str) -> list[Issue]:
    issues: list[Issue] = []
    lower = rel.lower()
    if any(lower.endswith(s) for s in ALLOWED_SUFFIXES):
        return issues
    if lower == ".env" or lower.endswith("/.env"):
        issues.append(Issue("error", rel, "ملف .env متتبَّع — المفاتيح في المستودع"))
    elif any(lower.endswith(s) for s in FORBIDDEN_SUFFIXES):
        issues.append(Issue("error", rel,
                            f"امتداد ممنوع للرفع ({Path(rel).suffix}) — بيانات "
                            "محلية/مفتاح"))
    if any(lower.startswith(p) or f"/{p}" in lower for p in FORBIDDEN_DIRS):
        issues.append(Issue("error", rel, "سجل التدقيق لا يُرفع إلى المستودع"))
    name = Path(rel).name
    if any(name.startswith(p) for p in FORBIDDEN_PREFIXES):
        issues.append(Issue("error", rel, "ملف تشغيل/تشخيص محلي (_dbg/_live)"))
    return issues


def _content_issues(rel: str, path: Path) -> list[Issue]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [Issue("warning", rel, f"تعذّرت القراءة: {exc.strerror}")]
    if len(raw) > MAX_SCAN_BYTES or b"\0" in raw[:8192]:
        return []                                  # بيانات ثنائية/كبيرة: لا فحص نصي
    text = raw.decode("utf-8", "replace")
    issues: list[Issue] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for label, pattern in PATTERNS:
            if pattern.search(line):
                issues.append(Issue("error", f"{rel}:{lineno}",
                                    f"بصمة {label} في نص متتبَّع"))
    return issues


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):  # ويندوز cp1256: إخراج UTF-8
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — بيئة بلا reconfigure
            pass
    parser = argparse.ArgumentParser(description="فحص مفاتيح/بيانات متتبَّعة")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    root = Path(args.root)
    files = _tracked_files(root)
    if files is None:
        print("✗ لا يمكن قراءة قائمة الملفات المتتبَّعة (git ls-files) — "
              "شغّل الأداة داخل المستودع.")
        return 1

    issues: list[Issue] = []
    for rel in files:
        issues += _path_issues(rel)
        issues += _content_issues(rel, root / rel)

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for issue in errors + warnings:
        print(issue)
    print(f"\nفحص المفاتيح: {len(files)} ملفًا متتبَّعًا، "
          f"{len(errors)} خطأ، {len(warnings)} تنبيه.")
    if errors:
        print("لا يُرفع مفتاح أو بيانات صحية إلى المستودع أبدًا — راجع .gitignore "
              "وارفع القيم عبر متغيرات البيئة.")
        return 1
    print("لا مفاتيح ولا بيانات محملة في الملفات المتتبَّعة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
