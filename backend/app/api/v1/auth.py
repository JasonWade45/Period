from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, audit, client_ip
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.models import RefreshToken, User, UserSettings
from app.schemas.auth import DeleteAccountIn, LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Used to equalize timing when the email doesn't exist.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _rate_limit(request: Request, bucket: str) -> None:
    s = get_settings()
    limiter.check(f"{bucket}:{client_ip(request)}", s.auth_rate_limit, s.auth_rate_window_seconds)


def _issue_tokens(db, user: User, family_id: uuid.UUID | None = None) -> tuple[TokenPair, RefreshToken]:
    s = get_settings()
    access, ttl = create_access_token(user.id)
    raw = new_refresh_token()
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_ttl_days),
    )
    db.add(rt)
    db.flush()
    return TokenPair(access_token=access, refresh_token=raw, expires_in=ttl), rt


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, db: DB) -> TokenPair:
    _rate_limit(request, "register")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        date_of_birth=body.date_of_birth,
        timezone=body.timezone,
        language=body.language.value,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from None
    db.add(UserSettings(user_id=user.id))
    tokens, _ = _issue_tokens(db, user)
    audit(db, request, "auth.register", user_id=user.id)
    db.commit()
    return tokens


@router.post("/login", response_model=TokenPair)
def login(body: LoginIn, request: Request, db: DB) -> TokenPair:
    _rate_limit(request, "login")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(body.password, user.password_hash):
        audit(db, request, "auth.login_failed", user_id=user.id)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    tokens, _ = _issue_tokens(db, user)
    audit(db, request, "auth.login", user_id=user.id)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshIn, request: Request, db: DB) -> TokenPair:
    _rate_limit(request, "refresh")
    invalid = HTTPException(status_code=401, detail="Invalid refresh token")
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)).with_for_update())
    if rt is None:
        raise invalid
    now = datetime.now(UTC)
    if rt.revoked_at is not None:
        # Reuse of a rotated/revoked token → likely theft. Revoke the whole family.
        db.execute(
            update(RefreshToken).where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
        )
        audit(db, request, "auth.refresh_reuse_detected", user_id=rt.user_id)
        db.commit()
        raise invalid
    if rt.expires_at <= now:
        raise invalid
    user = db.get(User, rt.user_id)
    if user is None:
        raise invalid
    tokens, new_rt = _issue_tokens(db, user, family_id=rt.family_id)
    rt.revoked_at = now
    rt.replaced_by_id = new_rt.id
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, request: Request, db: DB) -> Response:
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
    if rt is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        audit(db, request, "auth.logout", user_id=rt.user_id)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(body: DeleteAccountIn, request: Request, user: CurrentUser, db: DB) -> Response:
    """Permanently delete the account and ALL health data (cascade)."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to delete your account")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    audit(db, request, "auth.account_deleted", user_id=None, metadata={"deleted_user": "redacted"})
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
