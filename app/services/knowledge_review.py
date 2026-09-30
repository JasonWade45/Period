"""سير مراجعة المعرفة: ترقية مسودة إلى معرفة قابلة للاستشهاد.

الفكرة: المسودة تُكتب في sources_draft.json، ولا تصل للموديل أبدًا. بعد أن
تُراجع طبيبيًا يُسجَّل اسم المراجع وتاريخ المراجعة، وتُنقل إلى sources.json
كـ«مُتحقَّقة». بذلك لا تكون هناك لحظة يمكن فيها لمقطع غير مُراجَع أن يُستشهد به.

الفصل عبر ملفين مقصود: حتى لو حدث خطأ في الترقية، لا يمكن أن «تُنسى» مسودة
وتصبح مُتحقَّقة ضمنيًا.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..schemas import SourceChunk
from .rag import DRAFT, VERIFIED, KnowledgeError, load_chunks, _valid_iso_date


@dataclass
class PendingChunk:
    id: str
    section: str
    derived_from: list[str]
    drafted_at: str
    preview: str


def list_pending(drafts_path: Path | str) -> list[PendingChunk]:
    """المسودات التي تنتظر مراجعة طبية."""
    path = Path(drafts_path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        PendingChunk(
            id=item["id"],
            section=item.get("section", ""),
            derived_from=item.get("derived_from", []),
            drafted_at=item.get("drafted_at", ""),
            preview=(item.get("text") or "")[:160],
        )
        for item in raw
    ]


def promote(
    drafts_path: Path | str,
    sources_path: Path | str,
    chunk_id: str,
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> SourceChunk:
    """يرقّي مقطعًا واحدًا من مسودة إلى معرفة مُتحقَّقة.

    يرفض الترقية بلا اسم مراجع: التوثيق شرط الاستشهاد، لا خطوة اختيارية.
    """
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise KnowledgeError("اسم المراجع مطلوب: لا يُرقّى مقطع بلا توثيق")
    review_date = (reviewed_at or date.today().isoformat()).strip()
    if not _valid_iso_date(review_date):
        raise KnowledgeError(f"تاريخ المراجعة غير صالح: {review_date!r}")

    d_path, s_path = Path(drafts_path), Path(sources_path)
    drafts = json.loads(d_path.read_text(encoding="utf-8")) if d_path.exists() else []
    sources = json.loads(s_path.read_text(encoding="utf-8")) if s_path.exists() else []

    if any(c.get("id") == chunk_id for c in sources):
        raise KnowledgeError(f"المقطع {chunk_id} مُرقّى بالفعل في {s_path.name}")

    target = next((c for c in drafts if c.get("id") == chunk_id), None)
    if target is None:
        raise KnowledgeError(f"لا توجد مسودة بالمعرّف {chunk_id}")

    promoted = {
        **target,
        "status": VERIFIED,
        "reviewer": reviewer,
        "reviewed_at": review_date,
        "drafted_at": "",
    }
    # التحقق قبل الكتابة: لا نكتب مقطعًا لا يجتاز بوابة الإسناد
    validated = SourceChunk(**promoted)

    sources.append(json.loads(validated.model_dump_json()))
    drafts = [c for c in drafts if c.get("id") != chunk_id]

    s_path.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    d_path.write_text(json.dumps(drafts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # تحقّق نهائي من أن الملفين لا يزالان صالحين معًا
    load_chunks([s_path, d_path])
    return validated


def stats(sources_path: Path | str, drafts_path: Path | str) -> dict:
    """أعداد المعرفة: مُتحقَّق مقابل قيد المراجعة."""
    chunks = load_chunks([Path(sources_path), Path(drafts_path)])
    verified = [c for c in chunks if c.status == VERIFIED]
    drafts = [c for c in chunks if c.status == DRAFT]
    return {
        "verified": len(verified),
        "draft": len(drafts),
        "verified_ids": [c.id for c in verified],
        "draft_ids": [c.id for c in drafts],
    }
