"""استيراد مقاطع المعرفة: `python -m app.kb.ingest --path <jsonl>`.

بوابتان قبل أي كتابة:

1. **بوابة الرخصة** — لا يُستورد نص من مصدر إلا إذا ضبط إنسانٌ
   `approved_for_ingest=true` في السجل. البريف يمنع لصق نصوص محمية بحقوق
   نشر، والنتيجة ليست تحذيرًا في اللوج بل رفضٌ للمقطع.
2. **بوابة المحتوى** — كل سجل يُتحقق منه (معرّفات، محتوى غير فارغ) قبل التضمين.

الاستيراد متكرر بأمان (idempotent): المحتوى نفسه لا يغيّر الحالة ولا الإصدار،
والمحتوى المتغيّر يُعاد إلى `draft_unreviewed`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from ..services.arabic import normalize_for_search
from .embedding import Embedder, build_embedder
from .schemas import ChunkStatus, IngestRecord, KbChunk, KbSource
from .store import KbStore


@dataclass
class IngestReport:
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    content_changed: list[str] = field(default_factory=list)
    skipped_unapproved_source: list[tuple[str, str]] = field(default_factory=list)
    invalid: list[tuple[str, str]] = field(default_factory=list)
    embedded: int = 0

    def summary(self) -> str:
        return (
            f"جديد {len(self.created)} | بلا تغيير {len(self.unchanged)} | "
            f"محتوى تغيّر {len(self.content_changed)} | "
            f"مصدر غير معتمد {len(self.skipped_unapproved_source)} | "
            f"سجل غير صالح {len(self.invalid)}"
        )


def load_registry(path: Path) -> dict[str, KbSource]:
    """يقرأ سجل المصادر. غياب الملف = لا مصدر معتمد (وهذا سلوك آمن)."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("sources", raw) if isinstance(raw, dict) else raw
    registry: dict[str, KbSource] = {}
    for item in items:
        source = KbSource(**{k: v for k, v in item.items()
                             if k in KbSource.model_fields})
        registry[source.id] = source
    return registry


def read_records(path: Path) -> tuple[list[IngestRecord], list[tuple[str, str]]]:
    """يقرأ JSONL ويتحقق من كل سجل على حدة بلا إسقاط الملف كله."""
    records: list[IngestRecord] = []
    invalid: list[tuple[str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            invalid.append((f"سطر {lineno}", f"JSON غير صالح: {exc}"))
            continue
        try:
            records.append(IngestRecord(**raw))
        except Exception as exc:  # noqa: BLE001 — نُبلغ عن السجل لا نُسقط الملف
            invalid.append((raw.get("id", f"سطر {lineno}"), str(exc).splitlines()[0]))
    return records, invalid


def ingest_records(store: KbStore, records: list[IngestRecord],
                   registry: dict[str, KbSource], *, embedder: Embedder | None = None,
                   skip_unapproved: bool = False) -> IngestReport:
    """يستورد المقاطع مع احترام بوابة الرخصة. لا يكتب أي مقطع غير مسموح."""
    report = IngestReport()
    embedder = embedder or build_embedder()

    for record in records:
        source = registry.get(record.source_id)
        if source is None or not source.approved_for_ingest:
            report.skipped_unapproved_source.append((record.id, record.source_id))
            continue

        chunk = KbChunk(
            id=record.id, source_id=record.source_id, title=record.title,
            topic=record.topic, language=record.language or source.language,
            content=record.content,
            source_refs_to_verify=[r if isinstance(r, dict) else {"ref": r}
                                   for r in record.source_refs_to_verify],
        )
        outcome = store.upsert_chunk(chunk)
        getattr(report, {"created": "created", "unchanged": "unchanged",
                         "content_changed": "content_changed"}[outcome]).append(record.id)

    # تضمين المقاطع التي تغيّر محتواها أو أُضيفت (الاسترجاع المتجهي يحتاج متجهًا)
    pending = report.created + report.content_changed
    if pending and hasattr(store, "set_embedding"):
        vectors = embedder.embed([
            f"{store.get_chunk(cid).title} {store.get_chunk(cid).content}"  # type: ignore[union-attr]
            for cid in pending
        ])
        for chunk_id, vector in zip(pending, vectors):
            store.set_embedding(chunk_id, vector)  # type: ignore[attr-defined]
        report.embedded = len(pending)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="استيراد مقاطع قاعدة المعرفة")
    parser.add_argument("--path", required=True, help="ملف JSONL")
    parser.add_argument("--registry", default=str(settings.kb_source_registry_path))
    parser.add_argument("--db", default=str(settings.kb_db_path),
                        help="مسار SQLite (يُتجاهل عند KB_BACKEND=postgres)")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None,
                        help="مصدر البيانات؛ الافتراضي من KB_BACKEND")
    parser.add_argument("--skip-unapproved", action="store_true",
                        help="تابع بما هو معتمد وأبلغ عن المتخطّى (بدل الرفض الكامل)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"خطأ: الملف غير موجود — {path}", file=sys.stderr)
        return 2

    registry = load_registry(Path(args.registry))
    if not registry:
        print("تحذير: سجل المصادر فارغ أو غير موجود — لا شيء معتمد للاستيراد.",
              file=sys.stderr)

    records, invalid = read_records(path)
    approved = sum(1 for r in records
                   if registry.get(r.source_id) and registry[r.source_id].approved_for_ingest)

    print(f"سجلات صالحة: {len(records)} | غير صالحة: {len(invalid)}")
    print(f"مصادر معتمدة في السجل: {sum(1 for s in registry.values() if s.approved_for_ingest)}"
          f" | مقاطع من مصادر معتمدة: {approved}")

    if approved == 0 and records:
        print("\nلا يوجد أي مقطع من مصدر معتمد (approved_for_ingest=true).",
              file=sys.stderr)
        for cid, sid in [(r.id, r.source_id) for r in records[:5]]:
            print(f"  - {cid} ← المصدر {sid}", file=sys.stderr)
        if not args.skip_unapproved:
            print("\nالاستيراد مرفوض. راجعي رخصة المصدر واضبطي approved_for_ingest يدويًا،"
                  " أو استخدمي --skip-unapproved لاستيراد المعتمد فقط.", file=sys.stderr)
            return 3

    if args.dry_run:
        print("(تجربة بلا كتابة)")
        return 0

    from .postgres import build_store
    store = build_store(backend=args.backend, sqlite_path=args.db)
    print(f"قاعدة المعرفة: {type(store).__name__}")
    for source in registry.values():
        store.upsert_source(source)

    report = ingest_records(store, records, registry, skip_unapproved=args.skip_unapproved)
    report.invalid = invalid

    print(f"\n{report.summary()}")
    if report.embedded:
        print(f"مقاطع مُضمَّنة: {report.embedded}")
    for chunk_id, source_id in report.skipped_unapproved_source[:10]:
        print(f"  متخطّى (مصدر غير معتمد): {chunk_id} ← {source_id}")
    for chunk_id, reason in report.invalid[:10]:
        print(f"  غير صالح: {chunk_id} — {reason}")

    return 0 if not report.invalid else 1


if __name__ == "__main__":
    raise SystemExit(main())
