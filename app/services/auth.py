"""مصادقة الحسابات الحقيقية وتشفير كلمات المرور ورموز PIN وتوليد التوكنات.

المبادئ الأمنية:
- كلمات المرور ورموز PIN تُشتق عبر PBKDF2-HMAC-SHA256 مع ملح عشوائي (16 بايت)
  و200,000 تكرار، وتُقارن بزمن ثابت (hmac.compare_digest).
- توكن الوصول (Access Token) موقّع بـ HMAC-SHA256 ويحمل معرّف المستخدمة وتاريخ
  الانتهاء، بينما توكن التجديد (Refresh Token) عشوائي عالي الإنتروبيا يُخزَّن
  بصمته (SHA-256) فقط في قاعدة البيانات لدعم التدوير والإبطال الفوري.
- يبقى الوضع المحلي بلا حساب (Anonymous Local-First) متاحًا لمن تفضّل عدم
  إنشاء حساب، دون المساس بمسار الطوارئ الذي لا يُحجب أبدًا.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

PBKDF2_ITERATIONS = 200_000
ACCESS_TOKEN_TTL_SECONDS = 12 * 3600          # 12 ساعة
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600    # 30 يومًا


class AuthError(ValueError):
    """خطأ في بيانات الاعتماد أو صلاحية التوكن."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    username: str
    user_key: str
    exp: int


def _secret_bytes(explicit_secret: str | None = None) -> bytes:
    raw = (explicit_secret or os.environ.get("AUTH_SECRET") or "").strip()
    if not raw:
        # مفتاح افتراضي محلي للتطوير والاختبارات؛ في الإنتاج يُضبط AUTH_SECRET
        raw = "cyclecare-local-dev-secret-change-in-production"
    return raw.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def hash_secret(secret_text: str, salt_hex: str | None = None) -> tuple[str, str]:
    """يُرجع (hash_hex, salt_hex) باستخدام PBKDF2-HMAC-SHA256."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        secret_text.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return derived.hex(), salt.hex()


def verify_secret(secret_text: str, expected_hash_hex: str, salt_hex: str) -> bool:
    """مقارنة ثابتة الزمن لكلمة المرور أو رمز PIN."""
    if not secret_text or not expected_hash_hex or not salt_hex:
        return False
    try:
        candidate_hash, _ = hash_secret(secret_text, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(candidate_hash, expected_hash_hex)


def create_access_token(
    user_id: int,
    username: str,
    *,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
    secret: str | None = None,
    now: int | None = None,
) -> str:
    """يولّد توكن وصول موقّع بصيغة base64url(payload).base64url(signature)."""
    issued = int(time.time()) if now is None else int(now)
    payload = {
        "sub": int(user_id),
        "username": str(username),
        "user_key": f"user:{int(user_id)}",
        "iat": issued,
        "exp": issued + max(1, int(ttl_seconds)),
    }
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify_access_token(
    token: str,
    *,
    secret: str | None = None,
    now: int | None = None,
) -> TokenClaims:
    """يتحقق من توقيع وصلاحية توكن الوصول ويُرجع TokenClaims."""
    if not token or "." not in token:
        raise AuthError("invalid_token")
    body, sig_b64 = token.split(".", 1)
    expected_sig = hmac.new(
        _secret_bytes(secret), body.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
        payload: dict[str, Any] = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise AuthError("invalid_token") from exc

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise AuthError("invalid_signature")

    current = int(time.time()) if now is None else int(now)
    exp = int(payload.get("exp", 0))
    if exp <= current:
        raise AuthError("token_expired")

    user_id = int(payload["sub"])
    username = str(payload.get("username", ""))
    user_key = str(payload.get("user_key") or f"user:{user_id}")
    return TokenClaims(user_id=user_id, username=username, user_key=user_key, exp=exp)


def generate_refresh_token() -> tuple[str, str]:
    """يولّد توكن تجديد عشوائي ويُرجع (raw_token, sha256_hash)."""
    raw = secrets.token_urlsafe(36)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").strip().encode("utf-8")).hexdigest()
