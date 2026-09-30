"""اختبارات نقاط الـ API عبر HTTP — خاصة سلوك الرد الآمن بدون مفتاح موديل."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main

CTX = {
    "age": 27,
    "cycles_recorded": 5,
    "avg_cycle_days": 41,
    "last_cycles": [
        {"start_date": "2026-06-05", "length_days": 38},
        {"start_date": "2026-07-13", "length_days": 41},
        {"start_date": "2026-08-23", "length_days": 44},
    ],
}


@pytest.fixture(autouse=True)
def _disable_audit(monkeypatch):
    import app.services.audit as audit
    monkeypatch.setattr(audit, "write", lambda entry: None)


@pytest.fixture
def client():
    with TestClient(main.app) as c:  # with → يشغّل lifespan
        yield c


def test_health_reports_runtime_flags(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["prompt_version"] == main.settings.prompt_version
    assert body["chunks_loaded"] > 0
    # أعلام فقط — لا مفاتيح ولا أرقام
    assert set(body) >= {
        "llm_configured", "emergency_number_is_default", "crisis_line_configured",
    }
    assert "groq_api_key" not in {k.lower() for k in body}


def test_chat_without_model_key_returns_safe_fallback(client, monkeypatch) -> None:
    """غياب المفتاح ليس خطأ خادم: ترجع إجابة آمنة بحالة 200."""
    monkeypatch.setattr(main, "_llm", None)
    r = client.post("/v1/chat", json={"message": "إيه أعراض ما قبل الدورة؟", "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == main.NOT_CONFIGURED_ANSWER
    assert body["emergency"] is False
    assert body["prompt_version"]
    assert body["rule_codes"]


def test_summary_without_model_key_returns_safe_fallback(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "_llm", None)
    r = client.post("/v1/chat", json={"message": "ملخص لدوراتي", "mode": "summary",
                                      "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["overview"] == main.NOT_CONFIGURED_ANSWER
    assert body["what_this_does_not_mean"]


def test_emergency_path_works_without_model_key(client, monkeypatch) -> None:
    """الأهم: طبقة الطوارئ لا تعتمد على الموديل ولا على مفتاحه."""
    monkeypatch.setattr(main, "_llm", None)
    r = client.post("/v1/chat", json={"message": "بنزف كل ساعة وبرمي جلطات كبيرة",
                                      "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"] is True
    assert body["needs_doctor"] is True
    assert "رعاية طبية عاجلة" in body["answer"]
    assert main.settings.emergency_number in body["answer"]


def test_crisis_path_reports_crisis(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "_llm", None)
    r = client.post("/v1/chat", json={"message": "مش عايزة أعيش", "user_context": CTX})
    body = r.json()
    assert body["crisis"] is True
    assert body["emergency"] is True


def test_empty_message_is_rejected(client) -> None:
    r = client.post("/v1/chat", json={"message": "   ", "user_context": CTX})
    assert r.status_code == 422


def test_frontend_is_served(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
