"""تهيئة مشتركة: عزل قاعدة البيانات وتحديد المعدّل بين الاختبارات."""
from __future__ import annotations

import os
import tempfile

# قبل أي استيراد للتطبيق (الإعدادات تُقرأ عند الاستيراد): الاختبارات تحافظ على
# السلوك الصارم الأصلي (معتمد فقط، بلا تحميل بذرة) وبقاعدة معرفة مؤقتة، ما لم
# يختبر اختبارٌ التشغيل الافتراضي صراحةً. هكذا لا تُكتب data/kb.db أثناء الاختبارات.
os.environ.setdefault("KB_ALLOW_DRAFT", "0")
os.environ.setdefault("KB_AUTOLOAD_SEED", "0")
os.environ.setdefault("KB_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="kbtest-"), "kb.db"))

import pytest

import app.main as main
from app.services.security import limiter
from app.services.store import Store


@pytest.fixture(autouse=True)
def _isolate_limiter():
    """تحديد المعدّل حالة عامة بين الاختبارات — نُفرغه دائمًا."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """قاعدة بيانات مؤقتة: لا نلمس ملف المستخدمة الحقيقي."""
    isolated = Store(tmp_path / "test.db")
    monkeypatch.setattr(main, "_store", isolated)
    return isolated


@pytest.fixture
def no_audit(monkeypatch):
    import app.services.audit as audit
    monkeypatch.setattr(audit, "write", lambda entry: None)


@pytest.fixture
def client(store, no_audit):
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:  # with → يشغّل lifespan
        yield c


@pytest.fixture
def secured_client(store, no_audit, monkeypatch):
    """عميل مع مصادقة مفعّلة عبر رقعة على إعدادات الأمان."""
    import app.services.security as security
    from dataclasses import replace
    from fastapi.testclient import TestClient

    monkeypatch.setattr(security, "settings", replace(security.settings, api_key="secret-test-key"))
    with TestClient(main.app) as c:
        c.headers.update({"X-API-Key": "secret-test-key"})
        yield c


@pytest.fixture
def verified_sources(tmp_path):
    """مصادر اختبار مُتحقَّقة: نص مكتوب للاختبار فقط.

    لماذا لا تعتمد الاختبارات على `app/data/sources.json`: ملف الشحن أصبح **فارغًا
    عن قصد** (المقاطع الستة نُقلت إلى المسودات بانتظار ترخيص). اختبار يعتمد على
    بيانات شحن ينكسر عند كل تعديل محتوى، ويخلط بين «اختبار الكود» و«اختبار
    البيانات».
    """
    import json as _json

    path = tmp_path / "sources_test.json"
    path.write_text(_json.dumps([
        {
            "id": "test-cycle-length",
            "source_name": "مصدر اختباري",
            "section": "قسم اختباري",
            "reviewed_at": "2026-01-01",
            "reviewer": "مراجع اختباري",
            "status": "verified",
            # كلمات تغطي استعلامات اختبارات الـe2e (وراثة/متلازمات/تأخر) —
            # بيانات اختبار لا محتوى طبي.
            "keywords": ["الدورة", "طول الدورة", "ما قبل الدورة", "أعراض", "PMS",
                         "cycle length", "ملخص", "دوري", "تأخر", "تأخرت", "بتتأخر",
                         "تكيس", "تكيّس", "المبايض", "ألم", "التبويض", "حالة", "حالات",
                         "غريبة", "مدرجة", "القاموس", "صياغة", "60", "يوم"],
            "text": "نص اختباري عن طول الدورة وأعراض ما قبل الدورة. ليس نصًا طبيًا.",
        },
        {
            "id": "test-heavy-bleeding",
            "source_name": "مصدر اختباري",
            "section": "قسم اختباري",
            "reviewed_at": "2026-01-01",
            "reviewer": "مراجع اختباري",
            "status": "verified",
            "keywords": ["نزيف", "نزيف غزير", "heavy bleeding"],
            "text": "نص اختباري عن النزيف الغزير. ليس نصًا طبيًا.",
        },
    ], ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def test_drafts(tmp_path):
    """مسودة اختبارية واحدة — لاختبار أن المسودة لا تُستشهد ولا تُسترجع."""
    import json as _json

    path = tmp_path / "drafts_test.json"
    path.write_text(_json.dumps([{
        "id": "test-draft-1",
        "source_name": "مسودة اختبارية غير مُراجَعة",
        "section": "قسم اختباري",
        "status": "draft",
        "drafted_at": "2026-09-30",
        "derived_from": ["مصدر اختباري"],
        "keywords": ["الدورة", "التكيس"],
        "text": "نص اختباري غير مُراجَع. ليس نصًا طبيًا.",
    }], ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def test_rag(verified_sources, test_drafts, monkeypatch):
    """يستبدل مخزون المعرفة في التطبيق بمصادر اختبارية (لاختبارات HTTP)."""
    from app.services.rag import KeywordRag

    rag = KeywordRag(verified_sources, drafts_path=test_drafts)
    monkeypatch.setattr(main, "_rag", rag)
    return rag
