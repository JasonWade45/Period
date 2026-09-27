"""Password hashing (Argon2id), JWT access tokens and opaque refresh tokens."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

# argon2-cffi defaults to Argon2id with OWASP-recommended parameters.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=s.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), int(ttl.total_seconds())


def decode_access_token(token: str) -> uuid.UUID | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None


def new_refresh_token() -> str:
    """Opaque, high-entropy refresh token. Only its SHA-256 hash is stored."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salt = get_settings().audit_ip_salt.encode()
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()[:32]
