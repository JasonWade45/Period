"""حزمة `kb/` كما وصلت من صاحبة المشروع: قراءة صارمة بثوابت غير قابلة للتفاوض.

الحزمة أربعة ملفات:

    kb/knowledge/knowledge_seed.jsonl      مقاطع معرفة مكتوبة آليًا (بذرة)
    kb/eval/eval_questions_seed.jsonl      أسئلة تقييم (مجموعة صاحبة المشروع)
    kb/knowledge/glossary_ar.csv           قاموس المصطلحات (مصدر الحقيقة)
    kb/knowledge/sources_registry.json     سجل المصادر وحالة الاعتماد

هذا الملف لا «يقرأ ملفات» فقط، بل **يفرض ثوابت** على أي محتوى يمرّ منه، حتى لو
قال ملف الاستيراد عكسها:

1. كل مقطع بذرة: `status="draft_unreviewed"`، `authored_by="ai_draft"`،
   `reviewed_by=null`، `reviewed_at=null`، وملاحظة ترخيص إلزامية. أي ادّعاء
   بمراجعة سابقة في الملف يُتجاهَل ويُبلَّغ عنه — لا يُصدَّق أبدًا.
2. كل سؤال تقييم: `status="needs_physician_review"`.
3. كل مصدر في السجل: `approved_for_ingest=false`. القيمة لا تُقلب هنا مطلقًا:
   الاعتماد قرار بشري يُكتب في الملف نفسه بعد مراجعة الرخصة، وأي `true` قادم من
   مسار آلي يُسجَّل كتحذير ويُبقى على `false` في الذاكرة.

غياب ملف = حزمة ناقصة، وهذا يُبلَّغ عنه صراحة (لا محتوى مُخمَّن، ولا «إعادة بناء»
لنص لم يصل).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from .schemas import IngestRecord

# --------------------------------------------------------------------- الثوابت
SEED_STATUS = "draft_unreviewed"
SEED_AUTHORED_BY = "ai_draft"
SEED_REVIEWED_BY: None = None
SEED_REVIEWED_AT: None = None
SEED_LICENSE_NOTE = (
    "AI-written summary; verify against source_refs_to_verify; "
    "never present refs as verbatim source"
)
EVAL_SEED_STATUS = "needs_physician_review"
REGISTRY_APPROVED_FOR_INGEST = False
# بذرة صاحبة المشروع لا تحمل `source_id` لكل مقطع: الإسناد محفوظ لكل مقطع في
# `source_refs_to_verify`. نستخدم معرّفًا محايدًا للفهرسة الداخلية فقط — ولا
# ننسب أي مقطع إلى جهة بناءً على اسم مذكور في قائمة «للتحقق».
SEED_SOURCE_ID = "kb-seed"

SEED_FILE = "knowledge/knowledge_seed.jsonl"
EVAL_FILE = "eval/eval_questions_seed.jsonl"
GLOSSARY_FILE = "knowledge/glossary_ar.csv"
REGISTRY_FILE = "knowledge/sources_registry.json"

PACK_FILES = (SEED_FILE, EVAL_FILE, GLOSSARY_FILE, REGISTRY_FILE)


class PackError(ValueError):
    """ملف حزمة ناقص أو غير صالح — رسالة صريحة بدل تخمين محتوى."""


def kb_root(root: Path | str | None = None) -> Path:
    """جذر الحزمة. الافتراضي `kb/` في جذر المستودع (قابل للضبط للاختبارات)."""
    if root is not None:
        return Path(root)
    return Path(getattr(settings, "kb_dir", "kb"))


def _path(name: str, root: Path | str | None = None) -> Path:
    return kb_root(root) / name


def pack_report(root: Path | str | None = None) -> dict[str, Any]:
    """حالة الملفات الأربعة: موجود/غائب — بلا أي افتراض عن المحتوى."""
    base = kb_root(root)
    files = {}
    for name in PACK_FILES:
        path = base / name
        files[name] = {"path": str(path), "exists": path.exists(),
                       "size_bytes": path.stat().st_size if path.exists() else 0}
    return {"kb_root": str(base), "complete": all(f["exists"] for f in files.values()),
            "files": files}


# ------------------------------------------------------------------ بذرة المعرفة
@dataclass
class SeedLoad:
    """نتيجة قراءة البذرة: مقاطع جاهزة للاستيراد + ما رُفض وما أُعيد ضبطه."""

    records: list[IngestRecord] = field(default_factory=list)
    # ادّعاءات مراجعة/حالة وردت في الملف وأُلغيت: (المعرّف، الحقل، القيمة المدَّعاة)
    ignored_claims: list[tuple[str, str, str]] = field(default_factory=list)
    # مقاطع بلا source_id في الملف ⇒ أخذت المعرّف الداخلي المحايد SEED_SOURCE_ID
    placeholder_source_ids: list[str] = field(default_factory=list)
    invalid: list[tuple[str, str]] = field(default_factory=list)

    @property
    def license_note(self) -> str:
        return SEED_LICENSE_NOTE


def load_knowledge_seed(path: Path | str | None = None,
                        *, root: Path | str | None = None) -> SeedLoad:
    """يقرأ مقاطع البذرة ويثبّت عليها ثوابت المراجعة.

    الحقول المسموح بقراءتها من الملف: المحتوى والإسناد (`id`, `source_id`,
    `title`, `topic`, `language`, `content`, `source_refs_to_verify`). أما
    الحالة والمراجعة والكاتب فتُضبط هنا، وأي محاولة لتثبيتها في الملف تُسجَّل في
    `ignored_claims` لأنها ادّعاء مراجعة غير موثوق.
    """
    target = Path(path) if path is not None else _path(SEED_FILE, root)
    if not target.exists():
        raise PackError(
            f"ملف البذرة غير موجود: {target}. لم يُخمَّن أي محتوى بديل.")

    out = SeedLoad()
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            out.invalid.append((f"سطر {lineno}", f"JSON غير صالح: {exc}"))
            continue
        if not isinstance(raw, dict):
            out.invalid.append((f"سطر {lineno}", "السجل ليس كائنًا"))
            continue

        allowed_values = {"status": {SEED_STATUS}, "authored_by": {SEED_AUTHORED_BY},
                          "reviewed_by": {"", None}, "reviewed_at": {"", None}}
        for claimed in ("status", "reviewed_by", "reviewed_at", "authored_by"):
            value = raw.get(claimed)
            if value in (None, ""):
                continue
            if claimed in allowed_values and value not in allowed_values[claimed]:
                out.ignored_claims.append(
                    (str(raw.get("id", f"سطر {lineno}")), claimed, repr(value))
                )
        payload = {k: v for k, v in raw.items() if k in IngestRecord.model_fields}
        if not str(payload.get("source_id", "") or "").strip():
            payload["source_id"] = SEED_SOURCE_ID
            out.placeholder_source_ids.append(str(raw.get("id", f"سطر {lineno}")))
        try:
            record = IngestRecord(**payload)
        except Exception as exc:  # noqa: BLE001
            out.invalid.append((str(raw.get("id", f"سطر {lineno}")),
                                str(exc).splitlines()[0]))
            continue
        # الثوابت الإلزامية: تُثبَّت هنا ولا تُقرأ من الملف
        out.records.append(record.model_copy(update={
            "authored_by": SEED_AUTHORED_BY, "license_note": SEED_LICENSE_NOTE}))
    return out


# ------------------------------------------------------------------ أسئلة التقييم
def load_eval_seed(path: Path | str | None = None,
                   *, root: Path | str | None = None) -> list[dict[str, Any]]:
    """يقرأ أسئلة التقييم ويضع على كل سجل `status="needs_physician_review"`.

    المجموعة تبقى **منفصلة** عن مجموعة الوكلاء: لا دمج، ولا إعادة ترقيم، ولا
    استبدال. تُكتب تقاريرها في ملف تقرير خاص بها.
    """
    target = Path(path) if path is not None else _path(EVAL_FILE, root)
    if not target.exists():
        raise PackError(f"ملف أسئلة التقييم غير موجود: {target}.")

    items: list[dict[str, Any]] = []
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PackError(f"{target.name} سطر {lineno}: JSON غير صالح — {exc}") from exc
        if not isinstance(raw, dict):
            raise PackError(f"{target.name} سطر {lineno}: السجل ليس كائنًا")
        if not str(raw.get("question", "")).strip():
            raise PackError(f"{target.name} سطر {lineno}: لا يوجد حقل question")
        record = dict(raw)
        record["status"] = EVAL_SEED_STATUS      # ثابت: لا يُقبل ما في الملف
        items.append(record)
    return items


# ------------------------------------------------------------------ سجل المصادر
@dataclass
class RegistryLoad:
    sources: list[dict[str, Any]] = field(default_factory=list)
    rejected_auto_approvals: list[str] = field(default_factory=list)


def load_sources_registry(path: Path | str | None = None,
                          *, root: Path | str | None = None) -> RegistryLoad:
    """يقرأ سجل المصادر ويُبقي كل `approved_for_ingest` على `false`.

    هذا هو موضع البوابة: حتى لو جاء في الملف `true`، لا يمرّ إلى الاستيراد من
    هذا المسار. رفضُ القيمة يُسجَّل باسم المصدر ليظهر في التقرير بدل أن يمرّ صامتًا.
    """
    target = Path(path) if path is not None else _path(REGISTRY_FILE, root)
    if not target.exists():
        raise PackError(f"سجل المصادر غير موجود: {target}.")

    raw = json.loads(target.read_text(encoding="utf-8"))
    items = raw.get("sources", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise PackError(f"{target.name}: الجذر يجب أن يكون قائمة مصادر أو {{sources: [...]}}")

    out = RegistryLoad()
    for item in items:
        if not isinstance(item, dict):
            raise PackError(f"{target.name}: مصدر ليس كائنًا — {item!r}")
        source = dict(item)
        if bool(source.get("approved_for_ingest")):
            out.rejected_auto_approvals.append(str(source.get("id", "?")))
        source["approved_for_ingest"] = REGISTRY_APPROVED_FOR_INGEST
        out.sources.append(source)
    return out


# ------------------------------------------------------------------ الاستيراد
@dataclass
class DraftIngestReport:
    """نتيجة استيراد البذرة كمسودات — لا شيء هنا قابل للاستشهاد."""

    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    content_changed: list[str] = field(default_factory=list)
    embedded: int = 0

    def summary(self) -> str:
        return (f"جديد {len(self.created)} | بلا تغيير {len(self.unchanged)} | "
                f"محتوى تغيّر {len(self.content_changed)} | مُضمَّن {self.embedded}")


def ingest_seed_as_drafts(store, seed: SeedLoad, *, embedder=None) -> DraftIngestReport:
    """يُدخل مقاطع البذرة كمسودات (`draft_unreviewed`) لا كمقاطع معتمدة.

    لماذا لا تمرّ من بوابة الرخصة في `app.kb.ingest`؟ لأن تلك البوابة تحمي من
    **نسخ نص من مصدر** بلا إذن. البذرة هنا ملخّصات مكتوبة آليًا، والخطر فيها ليس
    النسخ بل أن تُقرأ كأنها معتمدة — وهذا ما يمنعه المخزون نفسه: الحالة تُفرض
    `draft_unreviewed`، والمراجع تُحفظ في `source_refs_to_verify` للتحقق لاحقًا،
    والمقطع يبقى غير قابل للاستشهاد حتى تعتمده طبيبة.
    """
    from .schemas import KbChunk

    report = DraftIngestReport()
    for record in seed.records:
        chunk = KbChunk(
            id=record.id, source_id=record.source_id, title=record.title,
            topic=record.topic, language=record.language or "ar", content=record.content,
            authored_by=SEED_AUTHORED_BY, license_note=SEED_LICENSE_NOTE,
            source_refs_to_verify=[r if isinstance(r, dict) else {"ref": r}
                                   for r in record.source_refs_to_verify],
        )
        outcome = store.upsert_chunk(chunk)
        getattr(report, outcome).append(record.id)

    pending = report.created + report.content_changed
    if pending and hasattr(store, "set_embedding"):
        vectors = (embedder or _build_default_embedder()).embed(
            [f"{store.get_chunk(cid).title} {store.get_chunk(cid).content}" for cid in pending])
        for chunk_id, vector in zip(pending, vectors):
            store.set_embedding(chunk_id, vector)
        report.embedded = len(pending)
    return report


def _build_default_embedder():
    from .embedding import build_embedder
    return build_embedder()


def to_kb_source(item: dict[str, Any]):
    """يحوّل سطر سجل صاحبة المشروع إلى نموذج المخزون بلا فقدان حقول الرخصة.

    سجلها يستخدم `license_status` و`languages` و`type` و`ingestion_method`؛
    تُحفظ كلها (الرخصة كما هي، والنوع وطريقة الاستيراد في notes).
    """
    from .schemas import KbSource

    extra = [str(item.get("type", "") or ""), str(item.get("ingestion_method", "") or "")]
    notes = str(item.get("notes", "") or "") or "; ".join(x for x in extra if x)
    return KbSource(
        id=str(item["id"]), name=str(item.get("name", "")),
        language=str(item.get("language") or item.get("languages") or "ar"),
        url=str(item.get("url", "") or ""),
        licence=str(item.get("licence") or item.get("license_status") or ""),
        approved_for_ingest=REGISTRY_APPROVED_FOR_INGEST,   # لا تُقلب أبدًا هنا
        notes=notes,
    )


# ----------------------------------------------------------------------- القاموس
def load_glossary(path: Path | str | None = None,
                  *, root: Path | str | None = None) -> list[dict[str, str]]:
    """يقرأ قاموس المصطلحات (مصدر الحقيقة للمصطلحات الطبية)."""
    target = Path(path) if path is not None else _path(GLOSSARY_FILE, root)
    if not target.exists():
        raise PackError(f"قاموس المصطلحات غير موجود: {target}.")
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [{k: (v or "") for k, v in row.items()} for row in rows]


# -------------------------------------------------------------------------- CLI
def _cmd_check(args: argparse.Namespace) -> int:
    report = pack_report(args.root)
    print(f"جذر الحزمة: {report['kb_root']}")
    for name, info in report["files"].items():
        mark = "موجود" if info["exists"] else "غائب"
        print(f"  [{mark}] {name}  ({info['size_bytes']} بايت)")
    if not report["complete"]:
        print("\nالحزمة ناقصة: لا يُنشأ أي محتوى بالنيابة عن صاحبة المشروع. "
              "أعيدي لصق الملفات الناقصة كما هي.")
        return 1

    seed = load_knowledge_seed(root=args.root)
    registry = load_sources_registry(root=args.root)
    questions = load_eval_seed(root=args.root)
    glossary = load_glossary(root=args.root)
    print(f"\nمقاطع البذرة: {len(seed.records)} (كلها {SEED_STATUS}، "
          f"authored_by={SEED_AUTHORED_BY})")
    if seed.placeholder_source_ids:
        print(f"  مقاطع بلا source_id في الملف ⇒ معرّف داخلي محايد "
              f"«{SEED_SOURCE_ID}»: {len(seed.placeholder_source_ids)}")
    print(f"  ادّعاءات مراجعة أُلغيت: {len(seed.ignored_claims)}")
    print(f"  سجلات غير صالحة: {len(seed.invalid)}")
    print(f"أسئلة التقييم: {len(questions)} (كلها {EVAL_SEED_STATUS})")
    print(f"مصادر السجل: {len(registry.sources)} | "
          f"محاولات اعتماد آلي رُفضت: {len(registry.rejected_auto_approvals)}")
    print(f"مصطلحات القاموس: {len(glossary)}")
    print("\nلا شيء ممّا سبق قابل للاستشهاد: البذرة مسودات، والاعتماد بشري.")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """يستورد البذرة كمسودات (الافتراضي) أو عبر بوابة الرخصة الصارمة عند الطلب."""
    from .ingest import ingest_records
    from .postgres import build_store

    seed = load_knowledge_seed(root=args.root)
    registry = load_sources_registry(root=args.root)

    store = build_store(backend=args.backend, sqlite_path=args.db)
    sources = {s["id"]: to_kb_source(s) for s in registry.sources if s.get("id")}
    for source in sources.values():
        # السجل يُكتب للتوثيق، وكل approved_for_ingest فيه false كما وصل
        store.upsert_source(source)

    print(f"البذرة: {len(seed.records)} مقطعًا")

    if args.require_approved_source:
        # المسار الصارم: نسخ نص من مصدر لا يمرّ إلا بموافقة بشرية على الرخصة
        approved = {sid: s for sid, s in sources.items() if s.approved_for_ingest}
        report = ingest_records(store, seed.records, approved)
        print(f"  {report.summary()}")
        print(f"  ادّعاءات مراجعة أُلغيت: {len(seed.ignored_claims)}")
        if report.skipped_unapproved_source:
            print("  لم يُستورد أي مقطع: لا مصدر بـ approved_for_ingest=true في السجل.")
            print("  هذا هو السلوك المقصود (بوابة الرخصة)، وليس عطلًا.")
        return 0

    report = ingest_seed_as_drafts(store, seed)
    print(f"  {report.summary()}")
    print(f"  ادّعاءات مراجعة أُلغيت: {len(seed.ignored_claims)}")
    print(f"  محاولات اعتماد آلي رُفضت: {len(registry.rejected_auto_approvals)}")
    print("\nالمقاطع دخلت كمسودات draft_unreviewed (غير مراجَعة طبيًا).")
    print("مع KB_ALLOW_DRAFT=1 (الافتراضي) يعرضها المساعد موسومة «غير مراجَعة»؛")
    print("ومع KB_ALLOW_DRAFT=0 لا يصل منها شيء لمستخدمة حتى تُرقّى بـ app.kb.review set-status.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            # ويندوز: وحدة تحكم cp1256 لا تحمل ⇒؛ نخرج UTF-8
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — بيئة بلا reconfigure
            pass
    parser = argparse.ArgumentParser(description="حزمة المعرفة kb/ — فحص واستيراد")
    parser.add_argument("command", choices=["check", "ingest"])
    parser.add_argument("--root", default=None, help="جذر الحزمة (افتراضي kb/)")
    parser.add_argument("--db", default=None, help="مسار قاعدة معرفة SQLite للاستيراد")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None,
                        help="مصدر البيانات؛ الافتراضي من KB_BACKEND")
    parser.add_argument("--require-approved-source", action="store_true",
                        help="المسار الصارم: لا يستورد إلا من مصدر اعتمده إنسان "
                             "(مناسب للنصوص المنسوخة من مصادر خارجية)")
    args = parser.parse_args(argv)
    try:
        return _cmd_check(args) if args.command == "check" else _cmd_ingest(args)
    except PackError as exc:
        # رسالة صريحة بدل تتبّع: السبب «لم يصل ملف» لا «عطل في الكود»
        print(f"لا يمكن إكمال الأمر: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
