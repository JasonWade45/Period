"""اختبارات المرحلة 1: مصادقة الحسابات الحقيقية، حفظ البيانات، ترحيل السجلات، قفل PIN، والملف الشخصي."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.services.auth import (
    AuthError,
    create_access_token,
    hash_secret,
    verify_access_token,
    verify_secret,
)
from app.services.store import Store, StoreError


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "stage1.db")
    monkeypatch.setattr(main_mod, "_store", store)
    return TestClient(main_mod.app)


# ========================================================= التشفير والتوكنات

def test_password_hash_and_constant_time_verification():
    pwd_hash, salt = hash_secret("MyStrongPass#2026")
    assert verify_secret("MyStrongPass#2026", pwd_hash, salt) is True
    assert verify_secret("WrongPass#2026", pwd_hash, salt) is False
    assert verify_secret("", pwd_hash, salt) is False


def test_access_token_roundtrip_and_expiry():
    token = create_access_token(42, "sara@example.com", ttl_seconds=600, now=1_700_000_000)
    claims = verify_access_token(token, now=1_700_000_100)
    assert claims.user_id == 42
    assert claims.username == "sara@example.com"
    assert claims.user_key == "user:42"

    with pytest.raises(AuthError):
        verify_access_token(token, now=1_700_000_700)


def test_tampered_access_token_is_rejected():
    token = create_access_token(7, "mona")
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    with pytest.raises(AuthError):
        verify_access_token(tampered)


# ==================================================== التسجيل والدخول والترحيل

def test_register_login_refresh_and_data_persistence(isolated_client: TestClient):
    # 1) تسجيل بيانات محلية أولًا (وضع بلا حساب)
    anon_key = "anon-device-12345"
    isolated_client.post(
        f"/v1/cycles?user_key={anon_key}",
        json={"start_date": "2026-08-01", "length_days": 5},
    )
    isolated_client.post(
        f"/v1/symptoms?user_key={anon_key}",
        json={"log_date": "2026-08-02", "symptom": "تقلصات", "severity": 4},
    )

    # 2) إنشاء حساب حقيقي مع ترحيل البيانات المحلية تلقائيًا
    reg = isolated_client.post(
        "/v1/auth/register",
        json={
            "username": "Nour@Example.com",
            "password": "SecretPassword123",
            "display_name": "نور",
            "migrate_user_key": anon_key,
        },
    )
    assert reg.status_code == 200
    body = reg.json()
    assert body["user"]["username"] == "nour@example.com"
    assert body["user"]["display_name"] == "نور"
    assert body["migrated"]["cycles_migrated"] == 1
    assert body["migrated"]["symptoms_migrated"] == 1

    access = body["access_token"]
    refresh = body["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # 3) البيانات صارت محفوظة تحت الحساب وتُجلب بالتوكن مباشرة دون user_key
    cycles = isolated_client.get("/v1/cycles", headers=headers).json()
    assert len(cycles) == 1
    assert cycles[0]["start_date"] == "2026-08-01"

    symptoms = isolated_client.get("/v1/symptoms", headers=headers).json()
    assert len(symptoms) == 1
    assert symptoms[0]["symptom"] == "تقلصات"

    # 4) حفظ الملف الشخصي على الخادم واسترجاعه عند تسجيل الدخول من جديد
    prof_res = isolated_client.put(
        "/v1/profile",
        headers=headers,
        json={
            "goal": "ttc",
            "age": 28,
            "country_code": "EG",
            "contraception": "لا يوجد",
            "conditions": [{"name": "نقص الحديد", "status": "مشخّصة"}],
            "onboarding_completed": True,
        },
    )
    assert prof_res.status_code == 200
    assert prof_res.json()["age"] == 28
    assert prof_res.json()["goal"] == "ttc"
    assert prof_res.json()["onboarding_completed"] is True

    # 5) تجديد التوكن (Refresh Token Rotation)
    ref_res = isolated_client.post("/v1/auth/refresh", json={"refresh_token": refresh})
    assert ref_res.status_code == 200
    new_access = ref_res.json()["access_token"]
    new_refresh = ref_res.json()["refresh_token"]
    assert new_refresh != refresh

    # التوكن القديم أُبطل ولا يصلح للاستخدام مرة ثانية
    reuse_res = isolated_client.post("/v1/auth/refresh", json={"refresh_token": refresh})
    assert reuse_res.status_code == 401

    # 6) تسجيل الدخول من جلسة جديدة يستعيد كل البيانات المحفوظة
    login_res = isolated_client.post(
        "/v1/auth/login",
        json={"username": "nour@example.com", "password": "SecretPassword123"},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert login_data["profile"]["age"] == 28
    assert login_data["profile"]["conditions"][0]["name"] == "نقص الحديد"

    # 7) تسجيل الخروج يبطل توكن التجديد
    out_res = isolated_client.post("/v1/auth/logout", json={"refresh_token": new_refresh})
    assert out_res.status_code == 200
    assert out_res.json()["revoked"] is True


def test_duplicate_username_and_wrong_password(isolated_client: TestClient):
    r1 = isolated_client.post(
        "/v1/auth/register",
        json={"username": "layla", "password": "Password123"},
    )
    assert r1.status_code == 200

    # تكرار اسم المستخدم يُرفض بـ 409
    r2 = isolated_client.post(
        "/v1/auth/register",
        json={"username": "LAYLA", "password": "Password123"},
    )
    assert r2.status_code == 409

    # كلمة مرور خاطئة تُرفض بـ 401
    bad_login = isolated_client.post(
        "/v1/auth/login",
        json={"username": "layla", "password": "WrongPassword"},
    )
    assert bad_login.status_code == 401


def test_pin_lock_and_disguise_mode(isolated_client: TestClient):
    reg = isolated_client.post(
        "/v1/auth/register",
        json={"username": "mariam", "password": "Password123"},
    ).json()
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    # ضبط PIN من 4 أرقام وتفعيل وضع التمويه
    pin_res = isolated_client.post(
        "/v1/auth/pin",
        headers=headers,
        json={"pin": "2580", "disguise_mode": True},
    )
    assert pin_res.status_code == 200
    assert pin_res.json()["user"]["has_pin"] is True
    assert pin_res.json()["user"]["disguise_mode"] is True

    # التحقق من PIN الصحيح والخاطئ
    ok_pin = isolated_client.post("/v1/auth/verify-pin", headers=headers, json={"pin": "2580"})
    assert ok_pin.status_code == 200
    assert ok_pin.json()["verified"] is True

    bad_pin = isolated_client.post("/v1/auth/verify-pin", headers=headers, json={"pin": "0000"})
    assert bad_pin.status_code == 401

    # إلغاء PIN
    clear_res = isolated_client.post(
        "/v1/auth/pin",
        headers=headers,
        json={"clear_pin": True, "disguise_mode": False},
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["user"]["has_pin"] is False
    assert clear_res.json()["user"]["disguise_mode"] is False


def test_consents_grant_and_revoke(isolated_client: TestClient):
    reg = isolated_client.post(
        "/v1/auth/register",
        json={"username": "hana", "password": "Password123"},
    ).json()
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    c1 = isolated_client.put(
        "/v1/consents",
        headers=headers,
        json={"consent_type": "ai_assistant", "granted": True},
    )
    assert c1.status_code == 200
    assert c1.json()["granted"] is True

    c2 = isolated_client.put(
        "/v1/consents",
        headers=headers,
        json={"consent_type": "ai_assistant", "granted": False},
    )
    assert c2.status_code == 200
    assert c2.json()["granted"] is False
    assert c2.json()["revoked_at"] is not None
