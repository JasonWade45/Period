"""تقريران منفصلان: أسئلة الوكلاء (eval/) وأسئلة صاحبة المشروع (kb/).

```
python -m app.eval.compare            # بلا شبكة: موديل مسجِّل + فاحص محلي
python -m app.eval.compare --live     # بموديل حقيقي (يتطلب مفتاحًا)
```

لا دمج ولا استبدال: كل مجموعة تُقاس وحدها، وتُكتب في تقرير باسمها، ويُطبع
السطران جنبًا إلى جنب ليُقرأ الفرق صراحةً. غياب مجموعة صاحبة المشروع (الحزمة
لم تصل) ليس فشلًا صامتًا: يُطبع أنه لا يوجد تقرير لها، ويُقاس ما هو موجود.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import settings

AGENT_SET = Path("eval/eval_questions_seed.jsonl")
USER_SET = Path(getattr(settings, "kb_dir", "kb")) / "eval" / "eval_questions_seed.jsonl"


def _build(pipeline_args: argparse.Namespace):
    """نفس اختيار الموديل/الفاحص في `app.eval.run` — بلا ازدواج منطق."""
    if pipeline_args.live:
        from ..services.llm import LLMClient
        from .judge import select_judge

        llm: Any = LLMClient()
        mode_label = "live"
        judge = select_judge(llm if pipeline_args.judge != "local" else None,
                             pipeline_args.judge)
    else:
        from .judge import LocalRubricJudge, select_judge
        from .run import RecordingLLM

        llm = RecordingLLM(invalid_json=pipeline_args.invalid_json)
        mode_label = "offline"
        judge = select_judge(None, pipeline_args.judge) if pipeline_args.judge != "local" \
            else LocalRubricJudge()
    return llm, judge, mode_label


def main(argv: Optional[list[str]] = None) -> int:
    for _stream in (sys.stdout, sys.stderr):  # ويندوز cp1256: إخراج UTF-8 بدل انهيار ⇒/✓
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — بيئة بلا reconfigure
            pass
    parser = argparse.ArgumentParser(description="تقريران منفصلان للتقييم")
    parser.add_argument("--agent-set", default=str(AGENT_SET))
    parser.add_argument("--user-set", default=str(USER_SET))
    parser.add_argument("--reports-dir", default="eval/reports")
    parser.add_argument("--judge", choices=["local", "llm", "auto"], default="local")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--invalid-json", action="store_true")
    args = parser.parse_args(argv)

    from .run import print_report, run, set_label

    llm, judge, mode_label = _build(args)
    reports_dir = Path(args.reports_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    sets = [(args.agent_set, "agent"), (args.user_set, "user")]
    summaries: list[dict[str, Any]] = []
    exit_code = 0

    for set_path_str, expected_label in sets:
        set_path = Path(set_path_str)
        if not set_path.exists():
            note = {"label": expected_label, "set": str(set_path), "status": "missing",
                    "questions": 0, "passed": 0, "pass_rate": None,
                    "gating_failed": False}
            summaries.append(note)
            if expected_label == "user":
                print(f"[لا تقرير] أسئلة صاحبة المشروع — الملف غير موجود: {set_path}\n"
                      "           الحزمة kb/ لم تصل بعد؛ لا يُخترع محتواها ولا تُقاس "
                      "أسئلة ليست لها.", file=sys.stderr)
            continue

        report_path = reports_dir / f"report-{expected_label}-{stamp}.json"
        report = run(set_path, llm=llm, judge=judge, mode_label=mode_label,
                     report_path=report_path, label=set_label(set_path))
        print_report(report)
        print()
        summaries.append({"label": report["label"], "set": report["set"],
                          "status": "ok",
                          "questions": report["totals"]["questions"],
                          "passed": report["totals"]["passed"],
                          "pass_rate": report["totals"]["pass_rate"],
                          "gating_failed": report["gating_failed"],
                          "report": str(report_path)})
        exit_code = exit_code or (1 if report["gating_failed"] else 0)

    print("=" * 68)
    print("الخلاصة: مجموعتان منفصلتان — لا دمج ولا استبدال")
    for item in summaries:
        owner = {"user": "أسئلة صاحبة المشروع (kb/)", "agent": "أسئلة الوكلاء (eval/)"}
        if item["status"] == "missing":
            print(f"  {owner[item['label']]:<28} لم تُقَس — الملف غير موجود")
        else:
            print(f"  {owner[item['label']]:<28} "
                  f"{item['passed']}/{item['questions']} ({item['pass_rate']})")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
