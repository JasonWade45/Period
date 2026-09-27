"""Server-side Grok (xAI) client. The API key never leaves the backend.

xAI exposes an OpenAI-compatible Chat Completions API at {GROK_BASE_URL}/chat/completions.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings

log = logging.getLogger("cyclecare.ai")


class AIUnavailable(Exception):
    """Raised when the AI provider can't produce a response (no key, timeout, HTTP error)."""


class ChatClient(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 900) -> str: ...


class GrokClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.grok_model
        self._transport = transport

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 900) -> str:
        key = self.settings.grok_api_key
        if not key:
            raise AIUnavailable("GROK_API_KEY is not configured")
        url = self.settings.grok_base_url.rstrip("/") + "/chat/completions"
        payload = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        try:
            with httpx.Client(timeout=self.settings.grok_timeout_seconds, transport=self._transport) as client:
                resp = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            log.warning("Grok request failed: %s", type(exc).__name__)
            raise AIUnavailable("AI provider request failed") from exc
        if resp.status_code != 200:
            # Never log the request body (contains health context) or the key.
            log.warning("Grok returned HTTP %s", resp.status_code)
            raise AIUnavailable(f"AI provider returned HTTP {resp.status_code}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIUnavailable("Unexpected AI provider response") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIUnavailable("Empty AI response")
        return content.strip()


_client: ChatClient | None = None


def get_ai_client() -> ChatClient:
    """FastAPI dependency (overridable in tests)."""
    global _client
    if _client is None:
        _client = GrokClient()
    return _client
