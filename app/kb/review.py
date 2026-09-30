"""سير مراجعة المقاطع: `python -m app.kb.review <list|show|set-status|stats>`.

الفكرة الحاكمة: لا يصل نص إلى مستخدمة إلا بعد سلسلة موثّقة باسم إنسان وتاريخ.
`draft_unreviewed → physician_reviewed → approved`، وأي قفزة تُرفض.

    python -m app.kb.review list
    python -m app.kb.review list --status draft_unreviewed
    python -m app.kb.review show kb-001
    python -m app.kb.review set-status kb-001 approved \\
        --reviewer "د. فلانة — أخصائية نسا وتوليد" --date 2026-10-05
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ..config import settings
from .schemas import ChunkStatus, ReviewDecision, TransitionError
from .postgres import build_store

STATUS_HELP = {
    ChunkStatus.DRAFT_UNREVIEWED: "مسودة لم تُراجَع — غير قابلة للاسترجاع في الإنتاج",
    ChunkStatus.PHYSICIAN_REVIEWED: "راجعها طبيب — لم تُعتمد بعد، وغير قابلة للاسترجاع",
    ChunkStatus.APPROVED: "معتمد — قابل للاسترجاع في الإنتاج",
    ChunkStatus.RETIRED: "منسحب — خارج الاستخدام",
}


def _store(args):
    """نفس الواجهة على SQLite أو PostgreSQL حسب --backend / KB_BACKEND."""
    return build_store(backend=getattr(args, "backend", None), sqlite_path=args.db)


def cmd_list(args) -> int:
    store = _store(args)
    statuses = [ChunkStatus(args.status)] if args.status else None
    chunks = store.list_chunks(statuses)
    if not chunks:
        print("لا مقاطع مطابقة.")
        return 0
    counts: dict[str, int] = {}
    for chunk in chunks:
        counts[chunk.status.value] = counts.get(chunk.status.value, 0) + 1
    print(f"الإجمالي: {len(chunks)} — " + " | ".join(f"{k}: {v}" for k, v in counts.items()))
    print()
    for chunk in chunks:
        pending = len(chunk.source_refs_to_verify)
        flag = f" ⚠ {pending} مرجع للتحقق" if pending else ""
        reviewer = f" — راجعه: {chunk.reviewed_by}" if chunk.reviewed_by else ""
        print(f"• {chunk.id} [{chunk.status.value}]{flag}{reviewer}")
        print(f"  {chunk.title or chunk.topic or 'بلا عنوان'} (v{chunk.content_version})")
    return 0


def cmd_show(args) -> int:
    store = _store(args)
    chunk = store.get_chunk(args.chunk_id)
    if chunk is None:
        print(f"لا يوجد مقطع بالمعرّف {args.chunk_id}", file=sys.stderr)
        return 2
    print(f"المعرّف   : {chunk.id}")
    print(f"المصدر    : {chunk.source_id}")
    print(f"الحالة    : {chunk.status.value} — {STATUS_HELP[chunk.status]}")
    print(f"اللغة     : {chunk.language} | الإصدار: {chunk.content_version}")
    print(f"راجعه     : {chunk.reviewed_by or '—'} | بتاريخ {chunk.reviewed_at or '—'}")
    print(f"العنوان   : {chunk.title}")
    print(f"الموضوع   : {chunk.topic}")
    print(f"بصمة      : {chunk.content_hash[:16]}…")
    print("\n--- المحتوى ---")
    print(chunk.content)
    print("\n--- مراجع يجب التحقق منها قبل الاعتماد ---")
    if chunk.source_refs_to_verify:
        for ref in chunk.source_refs_to_verify:
            print("  • " + json.dumps(ref, ensure_ascii=False))
    else:
        print("  لا شيء مسجّل — تأكّدي أن النص مدعوم بمصدر، وسجّلي المرجع هنا.")
    print("\n--- قائمة تحقق قبل الاعتماد ---")
    print("1) هل راجع طبيب النسا النص نفسه (لا ملخصًا عنه)؟")
    print("2) هل كل رقم/حدّ في النص مطابق للمصدر المذكور؟")
    print("3) هل النص خالٍ من أي إيحاء تشخيصي أو جرعة دواء؟")
    print("4) هل رخصة المصدر تسمح بإعادة الاستخدام؟ (approved_for_ingest في السجل)")
    return 0


def cmd_set_status(args) -> int:
    store = _store(args)
    try:
        decision = ReviewDecision(
            chunk_id=args.chunk_id, new_status=ChunkStatus(args.status),
            reviewer=args.reviewer, reviewed_at=args.date or date.today().isoformat(),
            note=args.note or "",
        )
    except ValueError as exc:
        print(f"خطأ في المدخلات: {exc}", file=sys.stderr)
        return 2
    try:
        chunk = store.set_status(decision.chunk_id, decision.new_status,
                                 decision.reviewer, decision.reviewed_at, decision.note)
    except (ValueError, TransitionError) as exc:
        print(f"مرفوض: {exc}", file=sys.stderr)
        return 3
    print(f"{chunk.id}: {chunk.status.value} — {chunk.reviewed_by} — {chunk.reviewed_at}")
    if chunk.status == ChunkStatus.APPROVED:
        print("صار المقطع قابلًا للاسترجاع في الإنتاج.")
    return 0


def cmd_stats(args) -> int:
    store = _store(args)
    chunks = store.list_chunks()
    counts = {s.value: 0 for s in ChunkStatus}
    for chunk in chunks:
        counts[chunk.status.value] += 1
    print(f"المقاطع: {len(chunks)}")
    for status, count in counts.items():
        print(f"  {status:20} {count:4}   {STATUS_HELP[ChunkStatus(status)]}")
    sources = store.list_sources()
    approved = [s for s in sources if s.approved_for_ingest]
    print(f"\nالمصادر: {len(sources)} | معتمدة للاستيراد: {len(approved)}")
    if chunks and counts["approved"] == 0:
        print("\nتنبيه: لا مقاطع معتمدة — المساعد سيقول «لا أملك مصدرًا موثوقًا»"
              " عن كل سؤال غير طارئ بدل أن يجيب من معرفة عامة.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="مراجعة مقاطع قاعدة المعرفة")
    parser.add_argument("--db", default=str(settings.kb_db_path),
                        help="مسار SQLite (يُتجاهل عند KB_BACKEND=postgres)")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list", help="عرض المقاطع")
    lst.add_argument("--status", choices=[s.value for s in ChunkStatus])
    lst.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="عرض مقطع كامل مع مراجعه")
    show.add_argument("chunk_id")
    show.set_defaults(func=cmd_show)

    setst = sub.add_parser("set-status", help="تغيير الحالة (يتطلب اسم مراجع)")
    setst.add_argument("chunk_id")
    setst.add_argument("status", choices=[s.value for s in ChunkStatus])
    setst.add_argument("--reviewer", required=True)
    setst.add_argument("--date", default=None)
    setst.add_argument("--note", default=None)
    setst.set_defaults(func=cmd_set_status)

    sub.add_parser("stats", help="إحصاء الحالات والمصادر").set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
