from __future__ import annotations

import time

from groq import Groq

from ..config import settings


class LLMClient:
    def __init__(self):
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self.client = Groq(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            max_retries=settings.llm_sdk_max_retries,
        )

    def _create(self, messages: list[dict]) -> tuple[str, int]:
        started = time.monotonic()
        max_attempts = max(1, settings.llm_max_attempts)
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = self.client.chat.completions.create(
                    model=settings.groq_model,
                    messages=messages,
                    temperature=settings.llm_temperature,
                    max_tokens=settings.llm_max_tokens,
                    response_format={"type": "json_object"},
                )
                elapsed = int((time.monotonic() - started) * 1000)
                return resp.choices[0].message.content or "", elapsed
            except Exception as exc:  # noqa: BLE001
                # 413/429 = تجاوز حد أو حجم: إعادة المحاولة مفيدة.
                status = getattr(exc, "status_code", None)
                if status in (413, 429) and attempt < max_attempts - 1:
                    last_error = exc
                    time.sleep(max(0.0, settings.llm_retry_backoff_seconds))
                    continue
                raise
        raise last_error  # type: ignore[misc]

    def complete(self, system: str, user: str, *, retry_feedback: str | None = None) -> tuple[str, int]:
        """يُرجع (نص_الرد, زمن_الاستجابة_ms)."""
        messages = [{"role": "system", "content": system}]
        if retry_feedback:
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": retry_feedback})
            messages.append({
                "role": "user",
                "content": "كان في خطأ في الصيغة. أعيدي الإخراج JSON فقط تصحيحًا، بنفس المحتوى ومعالجة الخطأ المذكور.",
            })
        else:
            messages.append({"role": "user", "content": user})
        return self._create(messages)
