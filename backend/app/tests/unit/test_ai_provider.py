"""Provider selection + request shape (no network: httpx.MockTransport)."""

import json

import httpx
import pytest

from app.ai.grok_client import AIUnavailable, GrokClient
from app.core.config import Settings

FAKE_GROQ = "gsk_" + "x" * 40
FAKE_XAI = "xai-" + "x" * 40


def _settings(**kw):
    return Settings(_env_file=None, **kw)


def test_groq_key_switches_defaults():
    s = _settings(grok_api_key=FAKE_GROQ)
    assert s.grok_base_url == "https://api.groq.com/openai/v1"
    assert s.grok_model == "openai/gpt-oss-120b"
    assert s.ai_provider == "groq"


def test_xai_key_keeps_prd_defaults():
    s = _settings(grok_api_key=FAKE_XAI)
    assert s.grok_base_url == "https://api.x.ai/v1" and s.grok_model == "grok-4.7" and s.ai_provider == "xai"


def test_explicit_model_wins_over_groq_default():
    s = _settings(grok_api_key=FAKE_GROQ, grok_model="llama-3.3-70b-versatile")
    assert s.grok_model == "llama-3.3-70b-versatile" and s.ai_provider == "groq"


def test_groq_api_key_env_alias(monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    assert _settings().grok_api_key == FAKE_GROQ


def test_request_shape_for_groq_gpt_oss():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " أهلًا "}}]})

    client = GrokClient(_settings(grok_api_key=FAKE_GROQ), transport=httpx.MockTransport(handler))
    assert client.complete([{"role": "user", "content": "hi"}], max_tokens=900) == "أهلًا"
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == f"Bearer {FAKE_GROQ}"
    body = seen["body"]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
    assert body["max_tokens"] == 900 + 1024


def test_xai_request_has_no_groq_only_params():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    GrokClient(_settings(grok_api_key=FAKE_XAI), transport=httpx.MockTransport(handler)).complete([{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in seen["body"] and "include_reasoning" not in seen["body"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_become_unavailable(status):
    client = GrokClient(_settings(grok_api_key=FAKE_GROQ), transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(AIUnavailable):
        client.complete([{"role": "user", "content": "x"}])
