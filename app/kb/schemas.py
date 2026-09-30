"""نماذج قاعدة المعرفة ودورة حياة حالة المراجعة.

الحالة ليست تسمية تجميلية: هي البوابة التي تمنع نصًا بلا مراجعة بشرية موثّقة
من الوصول إلى مستخدمة. لذلك الانتقالات محدودة صراحةً، وأي انتقال غير مسموح
يرفضه الكود بدل أن يمرّ صامتًا.

مساران مسجّلان، والفرق مقصود وموثّق في السجل:
- `owner_reviewed`: مراجعة مالك المنتج — قرار مقصود بعدم اشتراط طبيب، لأن
  المحتوى إرشادي مرجعي (لا تشخيص ولا دواء) ومعه إحالة صريحة لاستشارة الطبيب.
- `physician_reviewed`: مراجعة طبية — يبقى متاحًا إن أُريد رفعة أعلى لاحقًا.
في الحالتين: لا قفزة من مسودة إلى «معتمد» مباشرة.
"""
from __future__ import annotations

import hashlib
from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ..services.arabic import normalize_for_search


class ChunkStatus(str, Enum):
    DRAFT_UNREVIEWED = "draft_unreviewed"
    OWNER_REVIEWED = "owner_reviewed"
    PHYSICIAN_REVIEWED = "physician_reviewed"
    APPROVED = "approved"
    RETIRED = "retired"


# الانتقالات المسموحة. الترتيب مقصود: لا يمكن القفز من مسودة إلى «معتمد»
# بلا مراجعة بشرية مسجّلة (مالك أو طبيب)، ولا إعادة شيء منسحب للاعتماد
# بلا مراجعة جديدة. مسار المالك: draft → owner_reviewed → approved.
ALLOWED_TRANSITIONS: dict[ChunkStatus, set[ChunkStatus]] = {
    ChunkStatus.DRAFT_UNREVIEWED: {ChunkStatus.OWNER_REVIEWED,
                                   ChunkStatus.PHYSICIAN_REVIEWED,
                                   ChunkStatus.RETIRED},
    ChunkStatus.OWNER_REVIEWED: {ChunkStatus.APPROVED, ChunkStatus.RETIRED,
                                 ChunkStatus.DRAFT_UNREVIEWED,
                                 ChunkStatus.PHYSICIAN_REVIEWED},
    ChunkStatus.PHYSICIAN_REVIEWED: {ChunkStatus.APPROVED, ChunkStatus.RETIRED,
                                     ChunkStatus.DRAFT_UNREVIEWED,
                                     ChunkStatus.OWNER_REVIEWED},
    ChunkStatus.APPROVED: {ChunkStatus.RETIRED, ChunkStatus.PHYSICIAN_REVIEWED,
                           ChunkStatus.DRAFT_UNREVIEWED},
    ChunkStatus.RETIRED: {ChunkStatus.DRAFT_UNREVIEWED},
}


class TransitionError(ValueError):
    """انتقال حالة غير مسموح."""


def can_transition(old: ChunkStatus, new: ChunkStatus) -> bool:
    return old == new or new in ALLOWED_TRANSITIONS.get(old, set())


class KbSource(BaseModel):
    """مصدر في السجل — لا يُستورد منه نص إلا بموافقة بشرية صريحة على الرخصة."""

    id: str
    name: str
    language: str = "ar"
    url: str = ""
    licence: str = ""
    approved_for_ingest: bool = False     # قرار بشري فقط، لا يُضبط آليًا
    notes: str = ""

    @field_validator("approved_for_ingest")
    @classmethod
    def _not_auto_approved(cls, v: bool) -> bool:
        return bool(v)


class KbChunk(BaseModel):
    id: str
    source_id: str
    title: str = ""
    topic: str = ""
    language: str = "ar"
    content: str
    status: ChunkStatus = ChunkStatus.DRAFT_UNREVIEWED
    reviewed_by: str = ""
    reviewed_at: str = ""
    # من كتب المقطع. "ai_draft" لمحتوى حزمة kb/ المكتوب بالذكاء الاصطناعي:
    # لا يحمل اسم طبيب، ولا يُقدَّم كمراجَع حتى لو ادّعى ملف الاستيراد ذلك.
    authored_by: str = ""
    # ملاحظة الترخيص الإلزامية للمحتوى المكتوب آليًا (تحذير استخدام لا نص طبي).
    license_note: str = ""
    content_version: int = 1
    source_refs_to_verify: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str = ""

    @property
    def search_text(self) -> str:
        """نص البحث المُطبَّع — مصدر الفهرس الكلمي و`tsv`."""
        return normalize_for_search(f"{self.title} {self.topic} {self.content}")

    def compute_hash(self) -> str:
        """بصمة المحتوى: تغيّرها يعني أن النص تغيّر فعلًا.

        تُبنى على المحتوى والعنوان والموضوع بعد التطبيع، حتى لا تُعدّ إعادة
        الاستيراد نفس النص «تغييرًا» بسبب مسافة أو تشكيل.
        """
        payload = "\u241f".join([
            normalize_for_search(self.title),
            normalize_for_search(self.topic),
            normalize_for_search(self.content),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IngestRecord(BaseModel):
    """سجل خام من ملف JSONL — يُتحقق منه قبل أي كتابة."""

    id: str
    source_id: str
    content: str
    title: str = ""
    topic: str = ""
    language: str = "ar"
    source_refs_to_verify: list[Any] = Field(default_factory=list)
    # تُملأ إلزاميًا عند قراءة حزمة kb/ (كاتب آلي + ملاحظة ترخيص)، ولا تُقرأ
    # من الملف إن جاءت فيه.
    authored_by: str = ""
    license_note: str = ""

    @field_validator("content")
    @classmethod
    def _content_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("content فارغ")
        return v

    @field_validator("id", "source_id")
    @classmethod
    def _ids_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("المعرّف مطلوب")
        return v.strip()


class ReviewDecision(BaseModel):
    chunk_id: str
    new_status: ChunkStatus
    reviewer: str
    reviewed_at: str = Field(default_factory=lambda: date.today().isoformat())
    note: str = ""

    @field_validator("reviewer")
    @classmethod
    def _reviewer_required(cls, v: str) -> str:
        """لا انتقال حالة بلا اسم مراجع — التوثيق شرط، لا خطوة اختيارية."""
        if not (v or "").strip():
            raise ValueError("اسم المراجع مطلوب")
        return v.strip()


class RetrievedChunk(BaseModel):
    """مقطع عائد من الاسترجاع مع درجة الصلة وسبب ظهوره."""

    id: str
    source_id: str
    title: str
    topic: str
    language: str
    content: str
    score: float = 0.0
    vector_rank: Optional[int] = None
    keyword_rank: Optional[int] = None
