"""الاسترجاع الهجين: متجهات + كلمات مفتاحية، دمج بـRRF، ثم تصفية.

لماذا RRF (Reciprocal Rank Fusion): درجات المتجهات (تشابه جيبي) ودرجات البحث
الكلمي (تداخل كلمات) غير متجانسة — جمعهما مباشرة يعني أن مقياسًا يطغى على
الآخر بلا سبب. RRF يتجاهل القيم ويستخدم الرُّتب فقط:

    score(d) = Σ 1 / (k + rank(d))       k = 60 (القيمة الشائعة في الأدبيات)

قرارات مقصودة:
- التصفية بالحالة أولًا وقبل أي ترتيب: نص لم يُعتمد لا يجوز أن يظهر في نتائج،
  ولا حتى بدرجة منخفضة.
- عتبة صلة دنيا: الفشل يعني قائمة فارغة، والمساعد يقول «لا أملك مصدرًا موثوقًا»
  بدل أن يجيب من معرفة عامة (ده معيار قبول صريح).
- خلفية إنجليزية: إن لم يُرجع البحث بلغة المستخدمة شيئًا، نجرّب الإنجليزية.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import settings
from ..services.arabic import normalize_for_search
from .embedding import Embedder, to_pgvector_literal
from .schemas import ChunkStatus, RetrievedChunk
from .store import KbStore

log = logging.getLogger("cyclecare.kb")

RRF_K = 60
VECTOR_CANDIDATES = 20
KEYWORD_CANDIDATES = 20


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    vector_hits: int = 0
    keyword_hits: int = 0
    used_language: str = ""
    fell_back_to_english: bool = False
    reason: str = ""            # سبب الفراغ إن كان فارغًا — للتشخيص لا للمستخدمة


def producible_statuses(allow_draft: bool | None = None) -> list[ChunkStatus]:
    """الحالات القابلة للاسترجاع.

    الإنتاج: `approved` فقط. في بيئة تجريبية (KB_ALLOW_DRAFT=true) يُضاف
    `draft_unreviewed` للاختبار الداخلي. `physician_reviewed` غير قابل
    للاسترجاع: المراجعة الطبية خطوة في الطريق إلى الاعتماد لا حالة نهائية.
    """
    allow = settings.kb_allow_draft if allow_draft is None else allow_draft
    statuses = [ChunkStatus.APPROVED]
    if allow:
        statuses.append(ChunkStatus.DRAFT_UNREVIEWED)
    return statuses


class HybridRetriever:
    """مُسترجع هجين فوق أي تنفيذ تخزين يلتزم واجهة KbStore."""

    def __init__(self, store: KbStore, embedder: Embedder, *,
                 top_k: int | None = None,
                 min_similarity: float | None = None,
                 min_keyword_score: float | None = None,
                 allow_draft: bool | None = None,
                 language: str | None = None):
        self.store = store
        self.embedder = embedder
        self.top_k = top_k if top_k is not None else settings.kb_top_k
        self.min_similarity = (min_similarity if min_similarity is not None
                               else settings.kb_min_similarity)
        self.min_keyword_score = (min_keyword_score if min_keyword_score is not None
                                  else settings.kb_min_keyword_score)
        self.allow_draft = settings.kb_allow_draft if allow_draft is None else allow_draft
        self.language = language or settings.default_locale

    # ------------------------------------------------------------------ vector
    def _vector_hits(self, query: str, statuses: list[ChunkStatus],
                     language: str | None) -> list[tuple[str, float]]:
        """أفضل المقاطع بالتشابه المتجهي مع تطبيق العتبة والتصفية.

        البحث يُفوَّض إلى المخزن إن كان يوفّر `vector_search` (PostgreSQL/pgvector
        يستخدم فهرس HNSW)، وإلا يُحسب في بايثون للمقاطع المرشّحة.
        """
        vector = self.embedder.embed([query])[0]

        search = getattr(self.store, "vector_search", None)
        if callable(search):
            hits = search(vector, VECTOR_CANDIDATES, statuses, language,
                          self.min_similarity)
            if not hits and language:
                hits = search(vector, VECTOR_CANDIDATES, statuses, None,
                              self.min_similarity)
            return hits

        candidates = self.store.list_chunks(statuses)
        if language:
            localized = [c for c in candidates if c.language == language]
            candidates = localized or candidates     # خلفية تلقائية إن كان النوع فارغًا
        if not candidates:
            return []

        # المتجهات المخزّنة تُقدَّم على الحساب الفوري (وهي المصدر في PostgreSQL
        # عبر فهرس HNSW). الحساب الفوري يبقى مسارًا احتياطيًا للاختبار المحلي.
        stored = getattr(self.store, "all_embeddings", lambda: {})()
        scored: list[tuple[str, float]] = []
        if stored and all(c.id in stored for c in candidates):
            for chunk in candidates:
                similarity = sum(a * b for a, b in zip(vector, stored[chunk.id]))
                if similarity >= self.min_similarity:
                    scored.append((chunk.id, similarity))
        else:
            texts = [f"{c.title} {c.topic} {c.content}" for c in candidates]
            chunk_vectors = self.embedder.embed(texts)
            for chunk, vec in zip(candidates, chunk_vectors):
                similarity = sum(a * b for a, b in zip(vector, vec))
                if similarity >= self.min_similarity:
                    scored.append((chunk.id, similarity))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:VECTOR_CANDIDATES]

    # ----------------------------------------------------------------- keyword
    def _keyword_hits(self, query: str, statuses: list[ChunkStatus],
                      language: str | None) -> list[tuple[str, float]]:
        hits = self.store.keyword_search(query, KEYWORD_CANDIDATES, statuses, language)
        if not hits and language:
            hits = self.store.keyword_search(query, KEYWORD_CANDIDATES, statuses, "en")
        return [(cid, score) for cid, score in hits if score >= self.min_keyword_score]

    # ------------------------------------------------------------------- merge
    @staticmethod
    def _rrf(vector_hits: list[tuple[str, float]],
             keyword_hits: list[tuple[str, float]]) -> list[tuple[str, float]]:
        """دمج الرُّتب — يعمل بنفس الطريقة لأي مُقياس."""
        scores: dict[str, float] = {}
        for rank, (chunk_id, _) in enumerate(vector_hits, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        for rank, (chunk_id, _) in enumerate(keyword_hits, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def retrieve(self, query: str, *, language: str | None = None,
                 top_k: int | None = None) -> RetrievalResult:
        """يرجع مقاطع مُعتمَدة فقط، أو قائمة فارغة (بلا تخمين)."""
        statuses = producible_statuses(self.allow_draft)
        lang = (language or self.language or "").strip() or None
        limit = top_k or self.top_k

        if not normalize_for_search(query):
            return RetrievalResult([], reason="استعلام فارغ بعد التطبيع")

        vector_hits = self._vector_hits(query, statuses, lang)
        keyword_hits = self._keyword_hits(query, statuses, lang)

        merged = self._rrf(vector_hits, keyword_hits)
        result = RetrievalResult(
            [], vector_hits=len(vector_hits), keyword_hits=len(keyword_hits),
            used_language=lang or "",
        )

        if not merged and lang and lang != "en":
            # خلفية إنجليزية: قد لا يوجد مقطع عربي معتمد بعد
            en = self.retrieve(query, language="en", top_k=limit)
            en.fell_back_to_english = True
            return en

        if not merged:
            result.reason = (
                f"لا مقاطع مطابقة (الحالات: {[s.value for s in statuses]}، "
                f"اللغة: {lang or 'أي'}، العتبة: {self.min_similarity})"
            )
            return result

        rank_vector = {cid: i for i, (cid, _) in enumerate(vector_hits, start=1)}
        rank_keyword = {cid: i for i, (cid, _) in enumerate(keyword_hits, start=1)}

        chunks: list[RetrievedChunk] = []
        for chunk_id, score in merged[:limit]:
            chunk = self.store.get_chunk(chunk_id)
            if chunk is None:
                continue
            chunks.append(RetrievedChunk(
                id=chunk.id, source_id=chunk.source_id, title=chunk.title,
                topic=chunk.topic, language=chunk.language, content=chunk.content,
                score=round(score, 6),
                vector_rank=rank_vector.get(chunk_id),
                keyword_rank=rank_keyword.get(chunk_id),
            ))
        result.chunks = chunks
        if not chunks:
            result.reason = "لا نتائج بعد الدمج"
        return result


def build_retriever(store: KbStore, embedder: Embedder | None = None) -> HybridRetriever:
    from .embedding import build_embedder
    return HybridRetriever(store, embedder or build_embedder())


__all__ = [
    "HybridRetriever", "RetrievalResult", "build_retriever",
    "producible_statuses", "to_pgvector_literal", "RRF_K",
]
