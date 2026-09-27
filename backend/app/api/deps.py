from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token, hash_ip
from app.models import AuditLog, User
from app.schemas.tracking import MedicalSnapshot
from app.services.findings_service import evaluate_and_sync

bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_access_token: Annotated[str | None, Header(include_in_schema=False, max_length=4096)] = None,
) -> User:
    """Accepts `Authorization: Bearer <jwt>` (standard) or `X-Access-Token: <jwt>`.

    The custom header exists because some reverse proxies (e.g. authenticated preview proxies)
    consume or rewrite the Authorization header. If present it takes precedence.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if x_access_token:
        token = x_access_token.removeprefix("Bearer ").strip()
    elif creds is not None and creds.scheme.lower() == "bearer":
        token = creds.credentials
    else:
        raise unauthorized
    user_id = decode_access_token(token)
    if user_id is None:
        raise unauthorized
    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_ip(request: Request) -> str | None:
    # Behind a trusted reverse proxy, configure uvicorn --proxy-headers so request.client is the real client.
    return request.client.host if request.client else None


def audit(
    db: Session,
    request: Request | None,
    action: str,
    *,
    user_id: uuid.UUID | None,
    resource: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a security-relevant action. Never pass passwords, tokens or medical free text."""
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            resource=resource,
            ip_hash=hash_ip(client_ip(request)) if request else None,
            metadata_=metadata,
        )
    )


def medical_snapshot(db: Session, user: User) -> MedicalSnapshot:
    """Re-run the rules engine after a health-data write and persist findings."""
    _, evaluation = evaluate_and_sync(db, user)
    return MedicalSnapshot(
        overall_severity=evaluation.overall_severity.value,
        requires_immediate_attention=evaluation.requires_immediate_attention,
        alert=evaluation.alert.to_dict() if evaluation.alert else None,
    )


def get_owned(db: Session, model, obj_id: uuid.UUID, user: User):
    """Fetch a row owned by the current user or 404 (never reveal other users' rows)."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    return obj
