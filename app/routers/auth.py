"""مسارات `/v1/auth/*` — إنشاء حساب، دخول، خروج، وهوية الجلسة.

- إنشاء الحساب ينقل بيانات معرّف الجهاز القديم إلى الحساب تلقائيًا
  (الدورات، الأعراض، الملف، الموافقات) — لا يضيع سجلّ موجود.
- الجلسة كوكي `httpOnly` (`cc_sid`): يُرسل تلقائيًا في كل طلب من نفس الأصل،
  ورقم الصفحة لا يراه (httpOnly) فلا تسرقه سكربتات XSS.
- دخول برددين موحّدَي النص للبريد غير المسجَّل وكلمة المرور الخاطئة
  (لا تسريب وجود الحساب)، مع حدّ معدّل لكل بريد.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..i18n import get_translator, resolve_locale
from ..schemas import AuthLoginIn, AuthOut, AuthRegisterIn, AuthRegisterOut
from ..services.auth import (
    COOKIE_NAME,
    AuthError,
    account_for_request,
    check_password,
    clear_session_cookie,
    hash_password,
    new_session_token,
    normalize_email,
    session_expiry,
    set_session_cookie,
    store_for,
    timing_equalizer,
    token_hash,
    verify_password,
)
from ..services.security import api_key_header, limiter
from ..services.store import StoreError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _error(request: Request, code: str, status_code: int) -> HTTPException:
    """خطأ بكود مستقر + نص مترجم من ملف الموارد — لا نص عربي مكتوب هنا."""
    locale = resolve_locale(request.headers.get("accept-language"))
    return HTTPException(status_code=status_code,
                         detail={"code": code,
                                 "message": get_translator().error(code, locale)})


def _bad_request(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _start_session(request: Request, response: Response, account_id: str) -> str:
    token = new_session_token()
    store_for(request).create_session(token_hash(token), account_id, session_expiry())
    set_session_cookie(request, response, token)
    return token


@router.post("/register", response_model=AuthRegisterOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(api_key_header)])
def register(payload: AuthRegisterIn, request: Request, response: Response) -> dict:
    """إنشاء حساب + جلسة فورية + نقل بيانات الجهاز القديم إلى الحساب."""
    try:
        email = normalize_email(payload.email)
        password = check_password(payload.password)
        account = store_for(request).create_account(email, hash_password(password))
        migrated = store_for(request).migrate_device_data(payload.device_key or "", account["id"])
    except AuthError as exc:
        raise _bad_request(exc) from exc
    except StoreError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _start_session(request, response, account["id"])
    return {"authenticated": True, "email": email, "migrated": migrated}


@router.post("/login", response_model=AuthOut, dependencies=[Depends(api_key_header)])
def login(payload: AuthLoginIn, request: Request, response: Response) -> dict:
    """دخول ببريد + كلمة مرور — الرد نفسه لمن لا حساب له ولمن كلمة مرورها خاطئة."""
    try:
        email = normalize_email(payload.email)
    except AuthError as exc:
        raise _bad_request(exc) from exc

    identity = f"auth-login:{request.client.host if request.client else 'unknown'}:{email}"
    decision = limiter.check(identity)
    if not decision.allowed:
        exc = _error(request, "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS)
        exc.headers = {"Retry-After": str(decision.retry_after_seconds)}
        raise exc

    account = store_for(request).get_account_by_email(email)
    if account is None:
        timing_equalizer(payload.password)
        raise _error(request, "invalid_credentials", status.HTTP_401_UNAUTHORIZED)
    if not verify_password(payload.password, account["password_hash"]):
        raise _error(request, "invalid_credentials", status.HTTP_401_UNAUTHORIZED)

    _start_session(request, response, account["id"])
    return {"authenticated": True, "email": account["email"]}


@router.post("/logout", response_model=AuthOut, dependencies=[Depends(api_key_header)])
def logout(request: Request, response: Response) -> dict:
    """إنهاء الجلسة الحالية ومسح الكوكي — آمن حتى لو لم تكن هناك جلسة."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        store_for(request).delete_session(token_hash(token))
    clear_session_cookie(response)
    return {"authenticated": False, "email": None}


@router.get("/me", response_model=AuthOut, dependencies=[Depends(api_key_header)])
def me(request: Request, response: Response) -> dict:
    """هل هذا الزائر حساب مسجَّل؟ يستخدمها الواجهة قبل رسم الشاشات."""
    account_id = account_for_request(request)
    if not account_id:
        return {"authenticated": False, "email": None}
    account = store_for(request).get_account(account_id)
    if account is None:
        # جلسة تعود لحساب محذوف (.Cascade كان يجب أن يمسحها — احترازًا نظّفها)
        clear_session_cookie(response)
        return {"authenticated": False, "email": None}
    return {"authenticated": True, "email": account["email"]}
