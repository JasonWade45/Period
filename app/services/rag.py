from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..schemas import SourceChunk

_AR_DIACRITICS = re.compile(r"[ً-ْٰـ]")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _AR_DIACRITICS.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return text.lower().strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 1}


class RagRetriever:
    """واجهة الاسترجاع — يمكن استبدالها بـ vector DB لاحقًا."""

    def retrieve(self, query: str, top_k: int) -> list[SourceChunk]:
        raise NotImplementedError


class KeywordRag(RagRetriever):
    """استرجاع بكلمات مفتاحية ثنائية اللغة (عربي/إنجليزي).

    score = 2 × تداخل مع keywords + 1 × تداخل مع نص المقطع.
    كافٍ للهيكل الأولي؛ يُستبدل بـ embeddings عند الإطلاق.
    """

    def __init__(self, sources_path: Path):
        self.chunks: list[SourceChunk] = []
        if sources_path.exists():
            raw = json.loads(Path(sources_path).read_text(encoding="utf-8"))
            self.chunks = [SourceChunk(**c) for c in raw]
        self._kw_tokens: list[set[str]] = [
            _tokens(" ".join(c.keywords)) for c in self.chunks
        ]
        self._text_tokens: list[set[str]] = [_tokens(c.text) for c in self.chunks]

    def retrieve(self, query: str, top_k: int) -> list[SourceChunk]:
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scored: list[tuple[float, SourceChunk]] = []
        for i, chunk in enumerate(self.chunks):
            kw_score = len(q_tokens & self._kw_tokens[i])
            text_score = len(q_tokens & self._text_tokens[i])
            if kw_score == 0 and text_score == 0:
                continue
            scored.append((2.0 * kw_score + text_score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]
