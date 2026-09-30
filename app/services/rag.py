"""استرجاع المقاطع الطبية (RAG) مع بوابة «معرفة مُراجَعة» فقط.

قاعدة المشروع: الموديل لا يستشهد إلا بمقطع مُتحقَّق الإسناد له
(`source_name` + `section` + `reviewed_at`)، ولا يُستشهد بمسودة إطلاقًا.
المسودات تبقى على القرص قابلة للمراجعة، وتُحمَّل فقط عند تشغيل علم
`KNOWLEDGE_INCLUDE_DRAFTS` (للاختبار الداخلي) مع تحذير في اللوج.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path

from ..schemas import SourceChunk

log = logging.getLogger("cyclecare.rag")

VERIFIED, DRAFT = "verified", "draft"
STATUSES = {VERIFIED, DRAFT}

_AR_DIACRITICS = re.compile(r"[ً-ْٰـ]")


class KnowledgeError(ValueError):
    """خلل في ملفات المعرفة: إسناد ناقص أو مقطع غير صالح."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _AR_DIACRITICS.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return text.lower().strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 1}


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def load_chunks(paths: list[Path]) -> list[SourceChunk]:
    """يحمّل المقاطع من ملف أو أكثر ويرفض أي مقطع ناقص الإسناد.

    الرفض مقصود: مقطع بلا اسم مصدر أو قسم أو تاريخ مراجعة لا يصلح للاستشهاد،
    وإدخاله بصمت يعني أن الموديل قد ينسب معلومة إلى جهة لم تقلها.
    """
    chunks: list[SourceChunk] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise KnowledgeError(f"{path.name}: JSON غير صالح — {exc}") from exc
        if not isinstance(raw, list):
            raise KnowledgeError(f"{path.name}: الجذر يجب أن يكون قائمة مقاطع")

        for i, item in enumerate(raw):
            where = f"{path.name}[{i}]"
            chunk = SourceChunk(**item)
            if not chunk.id.strip():
                raise KnowledgeError(f"{where}: id فارغ")
            if chunk.id in seen:
                raise KnowledgeError(f"{where}: id مكرّر — {chunk.id}")
            if not chunk.source_name.strip():
                raise KnowledgeError(f"{where} ({chunk.id}): source_name فارغ")
            if not chunk.section.strip():
                raise KnowledgeError(f"{where} ({chunk.id}): section فارغ")
            if not chunk.text.strip():
                raise KnowledgeError(f"{where} ({chunk.id}): text فارغ")
            if not chunk.keywords:
                raise KnowledgeError(f"{where} ({chunk.id}): لا توجد keywords للاسترجاع")
            if chunk.status not in STATUSES:
                raise KnowledgeError(f"{where} ({chunk.id}): status غير معروف — {chunk.status}")

            if chunk.status == VERIFIED:
                if not _valid_iso_date(chunk.reviewed_at):
                    raise KnowledgeError(
                        f"{where} ({chunk.id}): مقطع مُتحقَّق بلا reviewed_at صالح "
                        "(YYYY-MM-DD) — أضيفيه أولًا أو اجعليه draft"
                    )
                if not chunk.reviewer.strip():
                    raise KnowledgeError(
                        f"{where} ({chunk.id}): مقطع مُتحقَّق بلا reviewer — اجعليه draft "
                        "حتى يُراجَع فعليًا"
                    )
            else:
                if not _valid_iso_date(chunk.drafted_at):
                    raise KnowledgeError(f"{where} ({chunk.id}): مسودة بلا drafted_at صالح")
                if not chunk.derived_from:
                    raise KnowledgeError(
                        f"{where} ({chunk.id}): مسودة بلا derived_from — سجّلي المراجع "
                        "التي استُخلصت منها"
                    )
                if chunk.reviewed_at:
                    raise KnowledgeError(
                        f"{where} ({chunk.id}): مسودة لها reviewed_at — إما تُرقّى إلى "
                        "verified بطريقة صريحة أو يُفرَّغ الحقل"
                    )
            seen.add(chunk.id)
            chunks.append(chunk)
    return chunks


class RagRetriever:
    """واجهة الاسترجاع — يمكن استبدالها بـ vector DB لاحقًا."""

    def retrieve(self, query: str, top_k: int) -> list[SourceChunk]:
        raise NotImplementedError


class KeywordRag(RagRetriever):
    """استرجاع بكلمات مفتاحية ثنائية اللغة (عربي/إنجليزي).

    score = 2 × تداخل مع keywords + 1 × تداخل مع نص المقطع.
    كافٍ للهيكل الأولي؛ يُستبدل بـ embeddings عند الإطلاق.

    `include_drafts=False` (الافتراضي) يعني أن كل ما يصل إلى الموديل مُتحقَّق
    الإسناد. المسودات تبقى محمّلة للعدّ والأدوات، لكن لا تُسترجع.
    """

    def __init__(self, sources_path: Path, drafts_path: Path | None = None,
                 include_drafts: bool = False):
        self.include_drafts = include_drafts
        paths = [Path(sources_path)]
        if drafts_path is not None:
            paths.append(Path(drafts_path))
        self.chunks: list[SourceChunk] = load_chunks(paths)
        self.retrievable: list[SourceChunk] = [
            c for c in self.chunks if include_drafts or c.status == VERIFIED
        ]
        self._kw_tokens = [_tokens(" ".join(c.keywords)) for c in self.retrievable]
        self._text_tokens = [_tokens(c.text) for c in self.retrievable]
        if not include_drafts and any(c.status == DRAFT for c in self.chunks):
            log.info(
                "المعرفة: %d مقطعًا مُتحقَّقًا متاحًا للاستشهاد، و%d مسودة محجوبة "
                "(تُرقّى بـ tools/review_sources.py)",
                len(self.retrievable), len(self.draft_chunks),
            )

    @property
    def draft_chunks(self) -> list[SourceChunk]:
        return [c for c in self.chunks if c.status == DRAFT]

    def is_citable(self, chunk_id: str) -> bool:
        return any(c.id == chunk_id for c in self.retrievable)

    def retrieve(self, query: str, top_k: int) -> list[SourceChunk]:
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scored: list[tuple[float, SourceChunk]] = []
        for i, chunk in enumerate(self.retrievable):
            kw_score = len(q_tokens & self._kw_tokens[i])
            text_score = len(q_tokens & self._text_tokens[i])
            if kw_score == 0 and text_score == 0:
                continue
            scored.append((2.0 * kw_score + text_score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]
