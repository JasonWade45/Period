"""توليد المتجهات (embeddings) — واجهة واحدة بثلاثة تنفيذات.

المطلوب في البريف: نموذج متعدد اللغات جيّد في العربية، مع مقارنة بين
`BAAI/bge-m3` و`intfloat/multilingual-e5-large` على مجموعة التقويم، واسم النموذج
في الإعدادات.

حالة هذه البيئة: `huggingface.co` محجوب، فلا يمكن تنزيل أي نموذج ولا تشغيل
المقارنة هنا. لذلك:
- `SentenceTransformerEmbedder` تنفيذ حقيقي جاهز للتشغيل حيث تتوفّر النماذج
  (يقرأ اسم النموذج من الإعدادات، ويرفع خطأً واضحًا إن لم يكن مثبّتًا/متاحًا
  بدل أن يفشل بصمت).
- `DeterministicLocalEmbedder` تنفيذ محلي حتمي بلا شبكة، يُستخدم للاختبار
  ولتشغيل خط الأنابيب كاملًا في هذه البيئة. **ليس نموذجًا دلاليًا**: يشابه
  بالنحو المعجمي (تداخل مقاطع الحروف)، فلا يجوز الاعتماد على جودة استرجاعه.
- `tools/benchmark_embeddings.py` يشغّل المقارنة المطلوبة على أي جهاز فيه
  النماذج، ويكتب النتيجة في تقرير.

تحذير معماري: استبدال النموذج يغيّر فضاء المتجهات، فتجب إعادة تضمين كل المقاطع
(`ingest --reembed`) وإلا صار البحث يعقّر بين فضاءين مختلفين.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol, Sequence

from ..config import settings
from ..services.arabic import normalize_for_search

EMBEDDING_DIM = 1024   # مطابق لـ VECTOR(1024) في الترحيل

# النموذجان المطلوب مقارنتهما، مع ملاحظة كل واحد
CANDIDATE_MODELS = {
    "BAAI/bge-m3": "متعدد اللغات، أداء عالٍ في العربية، متجهات 1024",
    "intfloat/multilingual-e5-large": "متعدد اللغات، 1024 بُعد، يتطلب بادئات query:/passage:",
}


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class DeterministicLocalEmbedder:
    """متجهات حتمية من تداخل مقاطع الحروف — للاختبار والتشغيل بلا شبكة فقط.

    الخصائص المطلوبة في الاختبارات: نفس النص → نفس المتجه دائمًا، والنصوص
    المتقاربة معجميًا → تشابه جيبي أعلى. لا تدّعي أي فهم دلالي.
    """

    def __init__(self, name: str = "deterministic-local", dim: int = EMBEDDING_DIM,
                 ngram: int = 3):
        self.name = name
        self.dim = dim
        self.ngram = ngram

    def _vector(self, text: str) -> list[float]:
        normalized = normalize_for_search(text)
        vec = [0.0] * self.dim
        tokens = normalized.split()
        for token in tokens:
            grams = [token[i:i + self.ngram] for i in range(max(1, len(token) - self.ngram + 1))]
            for gram in grams:
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[index] += sign
        return _l2_normalize(vec)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class SentenceTransformerEmbedder:
    """تنفيذ حقيقي بنموذج متعدد اللغات — يتطلب توفّر النموذج محليًا."""

    def __init__(self, name: str | None = None, *, query_prefix: str = "",
                 passage_prefix: str = ""):
        self.name = name or settings.kb_embedding_model
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - يعتمد على البيئة
            raise RuntimeError(
                "sentence-transformers غير مثبّت. ثبّتيه ثم أعد المحاولة، أو استخدم"
                " KB_EMBEDDING_BACKEND=local للتشغيل بلا نموذج (اختبار فقط)."
            ) from exc
        try:
            self._model = SentenceTransformer(self.name)
        except Exception as exc:  # pragma: no cover - يعتمد على الشبكة/الكاش
            raise RuntimeError(
                f"تعذّر تحميل النموذج {self.name}: {type(exc).__name__}: {exc}. "
                "النموذج يحتاج وصولًا إلى Hugging Face أو كاشًا محليًا."
            ) from exc
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        prefixed = [f"{self.passage_prefix}{t}" for t in texts]
        vectors = model.encode(prefixed, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        vector = model.encode([f"{self.query_prefix}{text}"], normalize_embeddings=True)[0]
        return list(map(float, vector))


def build_embedder() -> Embedder:
    """اختيار التنفيذ من الإعدادات. الافتراضي: محلي حتمي (بلا شبكة)."""
    backend = settings.kb_embedding_backend.lower()
    if backend == "local":
        return DeterministicLocalEmbedder()
    if backend in ("sentence-transformers", "st"):
        prefix = "query: " if "e5" in settings.kb_embedding_model.lower() else ""
        passage = "passage: " if "e5" in settings.kb_embedding_model.lower() else ""
        return SentenceTransformerEmbedder(query_prefix=prefix, passage_prefix=passage)
    raise ValueError(f"KB_EMBEDDING_BACKEND غير معروف: {backend}")


def to_pgvector_literal(vector: Sequence[float]) -> str:
    """صيغة pgvector النصية: '[1,2,3]' — تُستخدم في الاستعلامات المُعاملة."""
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
