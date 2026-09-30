"""تهيئة مشتركة: عزل قاعدة البيانات وتحديد المعدّل بين الاختبارات."""
from __future__ import annotations

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
