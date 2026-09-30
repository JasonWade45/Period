"""المصادقة وتحديد معدّل الطلبات.

قرارات أمنية مقصودة:
1) تحديد المعدّل **لا يُطبَّق** على الرسائل التي يُفعّل فيها فلتر الطوارئ. منع
   طلب طوارئ بسبب حصة استهلاك أسوأ من عدم وجود تحديد أصلًا. المكسب الأمني من
   تحديد المعدّل (حماية حصة المزوّد) لا يستحق مخاطرة بسلامة مستخدمة.
2) المصادقة اختيارية: إن كانت API_KEY فارغة فالتطبيق مفتوح (للتطوير المحلي
   فقط) ويُطبع تحذير عند الإقلاع. أي نشر حقيقي يجب أن يضبطها.
3) لا نخزّن مفتاح المستخدمة في السجلات، بل بصمة مختصرة عند الحاجة للتدقيق.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Header, HTTPException

from ..config import settings


def key_fingerprint(value: str) -> str:
    """بصمة غير عكوسة لمعرّف الجهاز/المفتاح — للتدقيق دون تخزين القيمة."""
    if not value or not value.strip():
        return ""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:12]


def check_api_key(x_api_key: str | None) -> None:
    """يتحقّق من المفتاح إن كان مضبوطًا في البيئة."""
    if not settings.api_key:
        return  # وضع التطوير: مفتوح
    if not x_api_key or not _constant_time_equal(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="مفتاح API غير صحيح أو مفقود")


def _constant_time_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def api_key_header(x_api_key: str | None = Header(default=None)) -> None:
    """اعتماد FastAPI لجميع مسارات /v1 ما عدا الطوارئ (انظر chat)."""
    check_api_key(x_api_key)


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


class RateLimiter:
    """نافذة زمنية منزلقة بسيطة في الذاكرة (لكل عملية).

    كافية لعملية واحدة؛ أي نشر بعدة عمّال (workers) يحتاج مخزنًا مشتركًا
    (Redis) وإلا صار الحد الفعلي مضاعفًا بعدد العمال.
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = max(1, limit)
        self.window = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, identity: str, now: float | None = None) -> RateLimitDecision:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window
        with self._lock:
            bucket = self._hits.setdefault(identity, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry = max(1, int(bucket[0] + self.window - current) + 1)
                return RateLimitDecision(False, retry_after_seconds=retry, remaining=0)
            bucket.append(current)
            return RateLimitDecision(True, remaining=self.limit - len(bucket))

    def reset(self, identity: str | None = None) -> None:
        with self._lock:
            if identity is None:
                self._hits.clear()
            else:
                self._hits.pop(identity, None)


limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
