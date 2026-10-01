"""حسابات المستخدمات والجلسات — مصادقة بريد + كلمة مرور.

قرارات أمنية وتصميمية:
- تشفير كلمة المرور: PBKDF2-HMAC-SHA256 من المكتبة القياسية (390 ألف تكرار)
  — لا نص خام في القاعدة، ولا حزم خارجية (المستودعات محجوبة في هذه البيئة).
- الجلسة: كوكي `httpOnly` برمز عشوائي، ونخزّن **بصمة SHA-256** منه فقط —
  لو سُرّقت القاعدة لا تُستطفى الجلسات.
- الهوية المؤثّرة (`effective_user_key`): كوكي الجلسة **يتغلّب** على `user_key`
  القادم من العميل — حساب مُوثَّق لا ينتحل حسابًا آخر بإرسال مفتاحه. بلا كوكي
  يبقى سلوك معرّف الجهاز كما هو تمامًا (توافق كامل مع endpoints القديمة:
  غير كاسرة).
- مصادقة موحّدة العبارات: بريد غير مسجَّل وكلمة خاطئة يعطيان الرد نفسه
  (لا تسريب وجود الحساب) وينفّذان التحقق في زمن متساوٍ تقريبًا.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from .store import StoreError

COOKIE_NAME = "cc_sid"
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 390_000
PASSWORD_MIN, PASSWORD_MAX = 8, 200
EMAIL_MAX = 254
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# تحقق وهمي لموازنة زمن فشل البريد غير المسجَّل مع فشل كلمة المرور الخاطئة.
_DUMMY_HASH: str | None = None


class AuthError(ValueError):
    """سبب فشل مفهوم بالعربية يُعرض للمستخدمة (422/401/409)."""


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not email or len(email) > EMAIL_MAX or not _EMAIL_RE.match(email):
        raise AuthError("بريد إلكتروني غير صالح")
    return email


def check_password(raw: str) -> str:
    if raw is None or not PASSWORD_MIN <= len(raw) <= PASSWORD_MAX:
        raise AuthError(f"كلمة المرور يجب أن تكون {PASSWORD_MIN} أحرف على الأقل")
    return raw


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"),
            bytes.fromhex(salt_hex), int(iterations),
        )
        return hmac.compare_digest(digest.hex().encode("utf-8"), digest_hex.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    return _DUMMY_HASH


def timing_equalizer(password: str) -> None:
    """يستهلك نفس زمن التحقق كأن البريد كان مسجَّلًا — لا تسريب بالتفرّق الزمني."""
    verify_password(password, _dummy_hash())


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()


def account_for_request(request: Request) -> str | None:
    """معرّف الحساب من كوكي الجلسة — أو None (بلا كوكي/كوكي منتهٍ/جلسة محذوفة)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    store = getattr(request.app.state, "store", None)
    if store is None:
        return None
    return store.account_for_session(token_hash(token))


def effective_user_key(request: Request, user_key: str) -> str:
    """اعتماد FastAPI: هوية الجلسة تتغلّب على مفتاح العميل (مضاد للانتحال)."""
    return account_for_request(request) or user_key


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    """كوكي httpOnly: يُرسل تلقائيًا مع كل طلب من نفس الأصل (لأن JS لا يراه)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 24 * 3600,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def store_for(request: Request):
    """مخزن التطبيق — نفس مصدر مسارات التتبّع (يُستبدل في الاختبارات)."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise AuthError("خدمة التخزين غير جاهزة")
    return store


__all__ = [
    "COOKIE_NAME", "AuthError", "StoreError",
    "normalize_email", "check_password", "hash_password", "verify_password",
    "timing_equalizer", "new_session_token", "token_hash", "session_expiry",
    "account_for_request", "effective_user_key",
    "set_session_cookie", "clear_session_cookie", "store_for",
]
