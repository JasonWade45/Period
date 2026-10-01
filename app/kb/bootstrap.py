"""تحميل بذرة المعرفة تلقائيًا عند الإقلاع — ليعمل المساعد من أول تشغيل.

الوضع الافتراضي للتطبيق: المقاطع التثقيفية في `kb/knowledge/knowledge_seed.jsonl`
تُحمَّل كمسودات `draft_unreviewed` (لا اسم مراجِع، ولا ادّعاء مراجعة)، وتُسترجَع
لأن `KB_ALLOW_DRAFT=1`، وتُعرَض للمستخدمة موسومة «مقال تثقيفي عام غير مراجَع طبيًا».
لا شيء هنا يُرقّي مقطعًا أو يضع اسم مراجِع، وإعادة التحميل لا تمسّ مقطعًا لم
يتغيّر نصه (فأي ترقية يدوية لاحقة تبقى).

الإيقاف: `KB_AUTOLOAD_SEED=0`. الصرامة الكاملة (معتمد فقط): `KB_ALLOW_DRAFT=0`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from .pack import SEED_SOURCE_ID, ingest_seed_as_drafts, load_knowledge_seed
from .schemas import KbSource

log = logging.getLogger("cyclecare.kb.bootstrap")




def autoload_seed(store, embedder, *, root: Path | str | None = None):
    """يحمّل البذرة إلى `store`. يعيد تقرير الاستيراد أو None إن لم يُنفَّذ."""
    if not settings.kb_autoload_seed:
        return None
    try:
        seed = load_knowledge_seed(root=root)
    except Exception as exc:  # noqa: BLE001 — غياب الملف لا يُسقط الإقلاع
        log.warning("تعذّر قراءة بذرة المعرفة: %s: %s", type(exc).__name__, exc)
        return None
    if not seed.records:
        log.info("بذرة المعرفة فارغة أو غائبة — لا شيء للتحميل.")
        return None
    try:
        # المخزون يشترط وجود المصدر (مفتاح خارجي في PostgreSQL)
        store.upsert_source(KbSource(
            id=SEED_SOURCE_ID, name="مقالات تثقيفية عامة (غير مراجَعة طبيًا)",
            notes="بذرة kb/knowledge/knowledge_seed.jsonl — مكتوبة آليًا، بلا مراجعة طبية"))
        report = ingest_seed_as_drafts(store, seed, embedder=embedder)
    except Exception as exc:  # noqa: BLE001
        log.error("فشل تحميل بذرة المعرفة: %s: %s", type(exc).__name__, exc)
        return None
    log.info("بذرة المعرفة: %s", report.summary())
    return report
