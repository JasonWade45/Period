"""مسارات الحسابات والجلسات: إنشاء، دخول، خروج، نقل بيانات الجهاز، ومنع الانتحال."""
from __future__ import annotations

import app.main as main
from app.services import auth as auth_service


EMAIL = "sara@example.com"
PASSWORD = "strong-pass-9"


def _register(client, email=EMAIL, password=PASSWORD, device_key=None):
    return client.post("/v1/auth/register", json={
        "email": email, "password": password, "device_key": device_key,
    })


# ------------------------------------------------------------- إنشاء الحساب

def test_register_sets_cookie_and_stores_hashed_password(client, store):
    r = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["authenticated"] is True and body["email"] == EMAIL
    assert client.cookies.get("cc_sid"), "يجب أن تُضبط جلسة كوكي بعد التسجيل"

    account = store.get_account_by_email(EMAIL)
    assert account is not None
    assert account["password_hash"].startswith("pbkdf2_sha256$")
    assert PASSWORD not in account["password_hash"]
    assert auth_service.verify_password(PASSWORD, account["password_hash"])


def test_register_rejects_bad_email_and_short_password(client):
    bad_email = client.post("/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD})
    assert bad_email.status_code == 422 and "صالح" in bad_email.json()["detail"]

    short = client.post("/v1/auth/register", json={"email": EMAIL, "password": "short"})
    assert short.status_code == 422 and "8" in short.json()["detail"]


def test_register_duplicate_email_is_409(client):
    assert _register(client).status_code == 201
    again = _register(client, email=EMAIL.upper())  # الحساب يوحّد حالة البريد
    assert again.status_code == 409 and "مسجَّل" in again.json()["detail"]


# ------------------------------------------------------------ نقل بيانات الجهاز

def test_register_migrates_device_data_to_account(client, store):
    assert client.post("/v1/cycles?user_key=dev-old",
                       json={"start_date": "2026-01-01", "bleeding_days": 4}).status_code == 200
    assert client.post("/v1/symptoms?user_key=dev-old",
                       json={"log_date": "2026-01-02", "symptom": "صداع",
                             "severity": 2}).status_code == 200

    r = _register(client, device_key="dev-old")
    assert r.status_code == 201
    migrated = r.json()["migrated"]
    assert migrated["cycles"] == 1 and migrated["symptoms"] == 1

    assert store.list_bleeding_logs("dev-old") == []
    assert store.list_symptom_logs("dev-old") == []
    account_id = store.get_account_by_email(EMAIL)["id"]
    assert len(store.list_bleeding_logs(account_id)) == 1

    # المتصفّح نفسه يرى بياناته الحالية فورًا (الجلسة تتغلّب على مفتاح الجهاز)
    seen = client.get("/v1/cycles?user_key=dev-old").json()
    assert [c["start_date"] for c in seen] == ["2026-01-01"]


# ------------------------------------------------------------- منع الانتحال

def test_session_identity_beats_client_supplied_key(client, store):
    _register(client)
    account_id = store.get_account_by_email(EMAIL)["id"]

    # حساب مُوثَّق يكتب تحت هويته مهما أرسل في user_key
    client.post("/v1/cycles?user_key=attacker-key",
                json={"start_date": "2026-02-01", "bleeding_days": None})
    assert store.list_bleeding_logs("attacker-key") == []
    assert len(store.list_bleeding_logs(account_id)) == 1

    # وقراءته تأتي من حسابه لا من المفتاح المُرسل
    rows = client.get("/v1/cycles?user_key=attacker-key").json()
    assert [c["start_date"] for c in rows] == ["2026-02-01"]

    # بلا جلسة (خروج) يعود السلوك القديم: مفتاح العميل يُستخدم كما هو
    client.post("/v1/auth/logout")
    assert client.get("/v1/cycles?user_key=attacker-key").json() == []


def test_effective_user_key_prefers_session(client, store):
    _register(client)
    account_id = store.get_account_by_email(EMAIL)["id"]
    token = client.cookies.get("cc_sid")

    def make_request(with_cookie: bool):
        headers = [(b"cookie", f"cc_sid={token}".encode())] if with_cookie else []
        scope = {"type": "http", "method": "GET", "path": "/", "headers": headers,
                 "query_string": b"", "app": main.app}
        from starlette.requests import Request
        return Request(scope)

    assert auth_service.effective_user_key(make_request(True), "device-x") == account_id
    assert auth_service.effective_user_key(make_request(False), "device-x") == "device-x"


# ------------------------------------------------------------- الدخول والخروج

def test_login_flow(client):
    _register(client)
    client.post("/v1/auth/logout")
    assert client.cookies.get("cc_sid") is None

    wrong_pw = client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong-pass-1"})
    unknown = client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong_pw.status_code == 401 and unknown.status_code == 401
    assert wrong_pw.json()["detail"] == unknown.json()["detail"]  # لا تسريب وجود الحساب

    ok = client.post("/v1/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["authenticated"] is True
    assert client.cookies.get("cc_sid")


def test_me_reports_session_state(client):
    anonymous = client.get("/v1/auth/me")
    assert anonymous.status_code == 200
    assert anonymous.json() == {"authenticated": False, "email": None}

    _register(client)
    me = client.get("/v1/auth/me").json()
    assert me == {"authenticated": True, "email": EMAIL}

    out = client.post("/v1/auth/logout")
    assert out.status_code == 200 and out.json()["authenticated"] is False
    assert client.get("/v1/auth/me").json()["authenticated"] is False
    assert client.cookies.get("cc_sid") is None


def test_logout_without_session_is_safe(client):
    r = client.post("/v1/auth/logout")
    assert r.status_code == 200 and r.json()["authenticated"] is False


# --------------------------------------------------------- تجزئة كلمة المرور

def test_password_hashing_helpers():
    stored = auth_service.hash_password(PASSWORD)
    assert stored.startswith(f"pbkdf2_sha256${auth_service.PBKDF2_ITERATIONS}$")
    assert auth_service.verify_password(PASSWORD, stored)
    assert not auth_service.verify_password("other-pass-9", stored)
    assert not auth_service.verify_password(PASSWORD, "garbage")
    assert not auth_service.verify_password(PASSWORD, "")

    try:
        auth_service.normalize_email("  USER@Example.COM ")
    except auth_service.AuthError:
        raise AssertionError("يجب توحيد البريد قبل المقارنة")
    assert auth_service.normalize_email("  USER@Example.COM ") == "user@example.com"
