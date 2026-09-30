#!/usr/bin/env python3
"""أداة مراجعة المعرفة الطبية.

    # عرض ما ينتظر مراجعة
    python tools/review_sources.py list

    # عرض مقطع كامل قبل المراجعة
    python tools/review_sources.py show draft-pcos-overview

    # ترقية بعد المراجعة الطبية (اسم المراجع إلزامي)
    python tools/review_sources.py promote draft-pcos-overview \
        --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05

الترقية تنقل المقطع من sources_draft.json إلى sources.json كـ«مُتحقَّق»، وبعدها
فقط يصبح قابلًا للاسترجاع والاستشهاد. لا توجد طريقة أخرى ليصل مقطع غير مُراجَع
إلى الموديل.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.knowledge_review import list_pending, promote, stats  # noqa: E402
from app.services.rag import KnowledgeError  # noqa: E402


def cmd_list(args) -> int:
    overview = stats(settings.sources_path, settings.draft_sources_path)
    print(f"مُتحقَّق: {overview['verified']} | قيد المراجعة: {overview['draft']}\n")
    pending = list_pending(settings.draft_sources_path)
    if not pending:
        print("لا توجد مسودات تنتظر المراجعة.")
        return 0
    for item in pending:
        sources = "، ".join(item.derived_from) or "—"
        print(f"• {item.id}\n  القسم: {item.section}\n  مستخلَص من: {sources}")
    print("\nاستخدمي: python tools/review_sources.py show <id>")
    return 0


def cmd_show(args) -> int:
    raw = json.loads(Path(settings.draft_sources_path).read_text(encoding="utf-8"))
    chunk = next((c for c in raw if c["id"] == args.chunk_id), None)
    if chunk is None:
        print(f"لا توجد مسودة بالمعرّف {args.chunk_id}", file=sys.stderr)
        return 1
    print(json.dumps(chunk, ensure_ascii=False, indent=2))
    print("\n--- قائمة التحقق قبل الترقية ---")
    print("1) هل راجعتِ النص على المصدر الأصلي المذكور في derived_from؟")
    print("2) هل الأرقام والحدود مطابقة للمصدر؟")
    print("3) هل النص يخلو من أي إيحاء تشخيصي أو جرعة دواء؟")
    print("4) هل title/section يوضّحان أن هذا تعليم عام لا استشارة؟")
    return 0


def cmd_promote(args) -> int:
    chunk = promote(
        settings.draft_sources_path, settings.sources_path,
        args.chunk_id, reviewer=args.reviewer, reviewed_at=args.date,
    )
    print(f"تمت الترقية: {chunk.id} — المراجع: {chunk.reviewer} — التاريخ: {chunk.reviewed_at}")
    print("أعيدي تشغيل الخادم ليصبح المقطع قابلًا للاستشهاد.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="مراجعة المعرفة الطبية وترقيتها")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="عرض المسودات المنتظرة").set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="عرض مسودة كاملة")
    show.add_argument("chunk_id")
    show.set_defaults(func=cmd_show)

    prom = sub.add_parser("promote", help="ترقية مسودة بعد المراجعة")
    prom.add_argument("chunk_id")
    prom.add_argument("--reviewer", required=True, help="اسم/جهة المراجعة الطبية")
    prom.add_argument("--date", default=None, help="تاريخ المراجعة YYYY-MM-DD (افتراضيًا اليوم)")
    prom.set_defaults(func=cmd_promote)

    args = parser.parse_args()
    try:
        return args.func(args)
    except KnowledgeError as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
