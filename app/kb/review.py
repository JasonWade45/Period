"""سير مراجعة المقاطع: `python -m app.kb.review <list|show|set-status|bulk-approve|stats>`.

الفكرة الحاكمة: لا يصل نص إلى مستخدمة إلا بعد سلسلة موثّقة باسم إنسان وتاريخ.
مساران للوصول إلى `approved`، وكلاهما بلا قفزة مباشرة من مسودة:

    draft_unreviewed → owner_reviewed    → approved   (مالك — بلا طبيب)
    draft_unreviewed → physician_reviewed → approved  (طبيب — رفعة أعلى)

    python -m app.kb.review list
    python -m app.kb.review list --status draft_unreviewed
    python -m app.kb.review show kb-001
    python -m app.kb.review set-status kb-001 owner_reviewed \\
        --reviewer "JasonWade45" --date 2026-09-30
    python -m app.kb.review bulk-approve --reviewer "JasonWade45"

قرار مقصود (صاحببة المشروع): المحتوى إرشادي مرجعي — لا تشخيص ولا دواء —
ومعه إحالة صريحة لاستشارة الطبيب، فمراجعة المالك تكفي بلا اشتراط طبيب.
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
    ChunkStatus.OWNER_REVIEWED: "راجعها المالك — محتوى إرشادي مرجعي، لم يُعتمد بعد",
    ChunkStatus.PHYSICIAN_REVIEWED: "راجعها طبيب — لم تُعتمد بعد، وغير قابلة للاسترجاع",
    ChunkStatus.APPROVED: "معتمد — قابل للاسترجاع في الإنتاج",
    ChunkStatus.RETIRED: "منسحب — خارج الاستخدام",
}

# ملاحظة التوثيق الافتراضية لمسار المالك — تُخزَّن في قرار المراجعة.
OWNER_NOTE_DEFAULT = ("مراجعة مالك: محتوى إرشادي مرجعي — لا تشخيص ولا دواء — "
                      "ويُنصح باستشارة الطبيب.")


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


def cmd_bulk_approve(args) -> int:
    """ترقية كل المقاطع المكدّسة عبر مسار المالك: draft → owner_reviewed → approved.

    اسم المراجع شرط (الكود يرفضه)، ولا قفزة مباشرة إلى `approved` — المسار
    يمرّ بالحالة الوسيطة المسجّلة حتى يبقى السجل صادقًا عن مَن راجع ومتى.
    """
    store = _store(args)
    reviewer = (args.reviewer or "").strip()
    if not reviewer:
        print("اسم المراجع مطلوب — لا ترقية بلا توثيق.", file=sys.stderr)
        return 2
    reviewed_at = args.date or date.today().isoformat()
    note = args.note or OWNER_NOTE_DEFAULT
    drafts = store.list_chunks([ChunkStatus.DRAFT_UNREVIEWED])
    if not drafts:
        print("لا مقاطع مسودة — لا شيء لترقيته.")
        return 0

    approved: list[str] = []
    failed: list[tuple[str, str]] = []
    for chunk in drafts:
        try:
            store.set_status(chunk.id, ChunkStatus.OWNER_REVIEWED,
                             reviewer, reviewed_at, note)
            store.set_status(chunk.id, ChunkStatus.APPROVED,
                             reviewer, reviewed_at, note)
            approved.append(chunk.id)
        except (ValueError, TransitionError) as exc:
            failed.append((chunk.id, str(exc)))

    print(f"المعتمَد ملكيًا: {len(approved)} من {len(drafts)} — المراجع: {reviewer}"
          f" — {reviewed_at}")
    for chunk_id in approved:
        print(f"  [تم] {chunk_id}")
    for chunk_id, error in failed:
        print(f"  [فشل] {chunk_id}: {error}", file=sys.stderr)
    if approved:
        print(f"\nملاحظة التوثيق: {note}")
        print("صارت هذه المقاطع قابلة للاسترجاع في الإنتاج.")
    return 1 if failed else 0


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

    bulk = sub.add_parser(
        "bulk-approve",
        help="ترقية كل المكدّسات عبر مسار المالك: draft → owner_reviewed → approved")
    bulk.add_argument("--reviewer", required=True,
                      help="اسم المراجع (مالك المنتج) — يُسجَّل في كل قرار")
    bulk.add_argument("--date", default=None)
    bulk.add_argument("--note", default=None,
                      help=f"ملاحظة التوثيق (الافتراضي: {OWNER_NOTE_DEFAULT})")
    bulk.set_defaults(func=cmd_bulk_approve)

    sub.add_parser("stats", help="إحصاء الحالات والمصادر").set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
