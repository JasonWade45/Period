"""المصادقة وتحديد المعدّل، وسلوك الطوارئ عند تفاعلهما."""
from __future__ import annotations

import pytest

import app.main as main
from app.services.security import RateLimiter, key_fingerprint

USER = "device-sec"


# ------------------------------------------------------------------ المصادقة

def test_tracker_endpoints_require_key_when_configured(secured_client):
    r = secured_client.get(f"/v1/cycles?user_key={USER}", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_tracker_endpoints_accept_correct_key(secured_client):
    assert secured_client.get(f"/v1/cycles?user_key={USER}").status_code == 200
    assert secured_client.get(f"/v1/insights?user_key={USER}").status_code == 200


def test_chat_requires_key_when_configured(secured_client):
    r = secured_client.post("/v1/chat", json={"message": "سؤال"},
                            headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_health_is_always_open(secured_client):
    """فحص الصحة يجب أن يعمل من أدوات المراقبة بلا مفتاح."""
    assert secured_client.get("/health", headers={"X-API-Key": ""}).status_code == 200


def test_emergency_reply_is_never_blocked_by_auth(secured_client):
    """قرار مقصود: مستخدمة في خطر بمفتاح خاطئ يجب أن تصلها أرقام الطوارئ."""
    r = secured_client.post("/v1/chat",
                            json={"message": "بنزف كل ساعة وبرمي جلطات كبيرة"},
                            headers={"X-API-Key": "wrong"})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"] is True
    assert main.settings.emergency_number in body["answer"]


def test_crisis_reply_is_never_blocked_by_auth(secured_client):
    r = secured_client.post("/v1/chat", json={"message": "مش عايزة أعيش"},
                            headers={"X-API-Key": "wrong"})
    assert r.status_code == 200
    assert r.json()["crisis"] is True


def test_open_mode_when_no_key_configured(client):
    """بلا API_KEY التطبيق مفتوح (تطوير محلي) — ويُحذَّر في اللوج عند الإقلاع."""
    assert client.get(f"/v1/cycles?user_key={USER}").status_code == 200


# ------------------------------------------------------------- تحديد المعدّل

def test_limiter_counts_within_window():
    rl = RateLimiter(limit=3, window_seconds=60)
    assert [rl.check("u", now=0).allowed for _ in range(3)] == [True, True, True]
    assert rl.check("u", now=0).allowed is False


def test_limiter_window_slides():
    rl = RateLimiter(limit=2, window_seconds=10)
    rl.check("u", now=0)
    rl.check("u", now=1)
    assert rl.check("u", now=5).allowed is False
    assert rl.check("u", now=11).allowed is True     # خرجت الأولى من النافذة


def test_limiter_is_per_identity():
    rl = RateLimiter(limit=1, window_seconds=60)
    assert rl.check("a", now=0).allowed is True
    assert rl.check("b", now=0).allowed is True      # مستخدمة أخرى غير متأثرة


def test_limiter_reports_retry_after():
    rl = RateLimiter(limit=1, window_seconds=30)
    rl.check("u", now=0)
    decision = rl.check("u", now=1)
    assert decision.allowed is False
    assert 1 <= decision.retry_after_seconds <= 30


def test_limiter_reset():
    rl = RateLimiter(limit=1, window_seconds=60)
    rl.check("u", now=0)
    rl.reset("u")
    assert rl.check("u", now=0).allowed is True


# ----------------------------------------------------------------- بصمة المفتاح

def test_fingerprint_is_stable_and_not_reversible():
    fp = key_fingerprint("device-abc")
    assert fp == key_fingerprint("device-abc")
    assert len(fp) == 12
    assert "device-abc" not in fp


def test_fingerprint_of_empty_is_empty():
    assert key_fingerprint("") == ""


def test_audit_stores_fingerprint_not_user_key(monkeypatch):
    """سجل التدقيق لا يجوز أن يحتفظ بمعرّف الجهاز الخام."""
    import app.main as main_mod
    captured: list = []
    import app.services.audit as audit
    monkeypatch.setattr(audit, "write", captured.append)
    monkeypatch.setattr(main_mod, "_llm", None)

    from app.schemas import ChatRequest
    main_mod._run_pipeline(ChatRequest(message="سؤال", user_key="device-abc"))

    assert captured[0].user_id == key_fingerprint("device-abc")
    assert "device-abc" not in (captured[0].user_id or "")


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_user_key_means_no_fingerprint(bad):
    assert key_fingerprint(bad) == ""
