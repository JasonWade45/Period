import json
from datetime import timedelta

from app.ai.grok_client import AIUnavailable
from app.tests.conftest import today
from app.tests.integration.test_tracking import _add_cycles


def _context_from_call(call) -> dict:
    ctx_msg = next(m for m in call if m["role"] == "system" and m["content"].startswith("CycleCare server-supplied"))
    raw = ctx_msg["content"].split("\n", 1)[1].rsplit("\n\n", 1)[0]
    return json.loads(raw)


def test_chat_happy_path_uses_minimal_context(client, auth, fake_ai):
    h, _ = auth(display_name="Mariam Secret", date_of_birth="2000-05-05")
    client.put("/api/v1/users/me/health-profile", json={"contraception_type": "none", "notes": "private note about my life"}, headers=h)
    _add_cycles(client, h, [40, 42, 38, 41])
    r = client.post("/api/v1/ai/chat", json={"message": "Why are my cycles long?"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_generated"] is True
    assert body["language"] == "en"
    assert body["overall_severity"] == "MEDICAL_REVIEW"

    call = fake_ai.calls[-1]
    assert call[0]["role"] == "system" and "CycleCare AI" in call[0]["content"]
    serialized = json.dumps(call, ensure_ascii=False)
    # Data minimization: no identifiers or free-text notes leave the server.
    for secret in ("user@example.com", "Mariam Secret", "2000-05-05", "private note", "password", "Bearer"):
        assert secret not in serialized
    ctx = _context_from_call(call)
    assert ctx["user_context"]["age"] >= 25
    assert ctx["medical_findings"][0]["rule_code"] == "MR-001"
    assert "symptom_summary" not in ctx  # unrelated topic not sent


def test_chat_arabic(client, auth, fake_ai):
    h, _ = auth(language="en")
    fake_ai.responses = ["دورتك مسجلة. هذا لا يعني وجود تشخيص."]
    r = client.post("/api/v1/ai/chat", json={"message": "ليه الدورة اتأخرت الشهر ده؟"}, headers=h)
    assert r.json()["language"] == "ar"
    assert "Respond in Arabic" in fake_ai.calls[-1][1]["content"]


def test_ai_unavailable_falls_back(client, auth, fake_ai):
    h, _ = auth()
    fake_ai.responses = [AIUnavailable("down")]
    r = client.post("/api/v1/ai/chat", json={"message": "Summarize my cycle"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ai_generated"] is False
    assert body["fallback_reason"] == "unavailable"
    assert "Medical Alerts" in body["reply"]


def test_diagnostic_response_is_regenerated(client, auth, fake_ai):
    h, _ = auth()
    fake_ai.responses = ["Based on this, you have PCOS.", "Irregular cycles can have several causes; worth discussing with a gynecologist."]
    r = client.post("/api/v1/ai/chat", json={"message": "Do I have PCOS?"}, headers=h).json()
    assert r["ai_generated"] is True
    assert "PCOS" not in r["reply"]
    assert len(fake_ai.calls) == 2


def test_persistent_violation_returns_safe_fallback(client, auth, fake_ai):
    h, _ = auth()
    fake_ai.responses = ["You are not pregnant.", "Don't worry, you don't need a doctor."]
    r = client.post("/api/v1/ai/chat", json={"message": "Am I pregnant?"}, headers=h).json()
    assert r["ai_generated"] is False
    assert r["fallback_reason"] == "validation"


def test_emergency_message_bypasses_ai(client, auth, fake_ai):
    h, _ = auth()
    r = client.post("/api/v1/ai/chat", json={"message": "I'm pregnant and bleeding a lot and feel dizzy"}, headers=h).json()
    assert fake_ai.calls == []
    assert r["fallback_reason"] == "emergency"
    assert r["safety_alert"]["is_emergency"] is True


def test_emergency_arabic_bypasses_ai(client, auth, fake_ai):
    h, _ = auth(language="ar")
    r = client.post("/api/v1/ai/chat", json={"message": "أنا حامل وعندي نزيف ودوخة"}, headers=h).json()
    assert fake_ai.calls == []
    assert r["language"] == "ar"
    assert "الطوارئ" in r["reply"]


def test_emergency_finding_bypasses_ai(client, auth, fake_ai):
    h, _ = auth()
    client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "pain_level": 10, "symptoms": ["fainting"]}, headers=h)
    r = client.post("/api/v1/ai/chat", json={"message": "what does my pain mean?"}, headers=h).json()
    assert fake_ai.calls == []
    assert r["overall_severity"] == "EMERGENCY"
    assert r["safety_alert"]["severity"] == "EMERGENCY"


def test_urgent_finding_attaches_safety_alert_but_still_answers(client, auth, fake_ai):
    h, _ = auth()
    client.post(
        "/api/v1/symptoms", json={"log_date": today().isoformat(), "pain_level": 9, "symptoms": ["painkillers_not_helping"]}, headers=h
    )
    r = client.post("/api/v1/ai/chat", json={"message": "what can help period pain?"}, headers=h).json()
    assert r["safety_alert"]["severity"] == "URGENT"
    assert r["ai_generated"] is True


def test_conversation_history_and_delete(client, auth, fake_ai):
    h, _ = auth()
    r1 = client.post("/api/v1/ai/chat", json={"message": "hello"}, headers=h).json()
    cid = r1["conversation_id"]
    client.post("/api/v1/ai/chat", json={"message": "and my cycle?", "conversation_id": cid}, headers=h)
    # Previous turns are sent as history.
    assert any(m["content"] == "hello" for m in fake_ai.calls[-1])
    conv = client.get(f"/api/v1/ai/conversations/{cid}", headers=h).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
    assert client.delete(f"/api/v1/ai/conversations/{cid}", headers=h).status_code == 204
    assert client.get(f"/api/v1/ai/conversations/{cid}", headers=h).status_code == 404


def test_summary_with_and_without_ai(client, auth, fake_ai):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41], days_after_last=10)
    r = client.post("/api/v1/ai/summary", json={"period": "6_cycles"}, headers=h).json()
    assert r["ai_generated"] is True
    assert r["findings"][0]["rule_code"] == "MR-001"
    assert r["questions_for_doctor"]

    fake_ai.responses = [AIUnavailable("down")]
    r = client.post("/api/v1/ai/summary", json={"period": "6_cycles"}, headers=h).json()
    assert r["ai_generated"] is False
    assert "38" in r["summary"] and "42" in r["summary"]  # deterministic summary still useful
    assert r["findings"]


def test_ai_rate_limit(client, auth, fake_ai):
    from app.core.config import get_settings

    h, _ = auth()
    limit = get_settings().ai_rate_limit
    codes = [client.post("/api/v1/ai/chat", json={"message": "hi"}, headers=h).status_code for _ in range(limit + 1)]
    assert codes[-1] == 429


def test_grok_client_without_key_raises_unavailable():
    import pytest

    from app.ai.grok_client import GrokClient
    from app.core.config import Settings

    with pytest.raises(AIUnavailable):
        GrokClient(Settings(grok_api_key=None)).complete([{"role": "user", "content": "hi"}])


def test_grok_client_http_contract():
    import httpx

    from app.ai.grok_client import GrokClient
    from app.core.config import Settings

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    c = GrokClient(Settings(grok_api_key="xai-test", grok_model="grok-4.7"), transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert seen["url"] == "https://api.x.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer xai-test"
    assert seen["body"]["model"] == "grok-4.7"

    bad = GrokClient(Settings(grok_api_key="k"), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    import pytest

    with pytest.raises(AIUnavailable):
        bad.complete([{"role": "user", "content": "hi"}])


_ = timedelta
