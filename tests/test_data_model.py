"""نموذج البيانات الجديد: هجرة القوائم، توافق `length_days`، الملف، الموافقات، حق الحذف.

- الهجرة تعيد تسمية قوائم النسخة القديمة **مع بياناتها ومعرّفاتها** — لا إنشاء
  لجدول جديد بجانب القديم الممتلئ.
- الاسم القديم `length_days` ما زال مقبولًا في الإدخال ويظهر في الإخراج (توافق
  لا كسر).
- الملف الصحي والموافقات: تخزين فقط، والاختيار يُسجَّل ولا يغيّر السلوك بعد.
- حق الحذف: `/v1/data` يمحو جداول التطبيق ويترك سجل التدقيق، و`/v1/account`
  يمحو بصمة التدقيق نفسها (لا يمكن عكسها).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import app.services.audit as audit_mod
from app.schemas import CycleStat
from app.services.security import key_fingerprint
from app.services.store import Store

# ------------------------------------------------------------- قاعدة قديمة

LEGACY_SCHEMA = """
CREATE TABLE cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    start_date TEXT NOT NULL,
    length_days INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (user_key, start_date)
);
CREATE INDEX idx_cycles_user ON cycles (user_key, start_date);
CREATE TABLE symptoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    log_date TEXT NOT NULL,
    symptom TEXT NOT NULL,
    severity INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_symptoms_user ON symptoms (user_key, log_date);
"""


def _make_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO cycles (user_key, start_date, length_days, created_at)"
        " VALUES ('u1', '2026-06-05', 5, '2026-06-05T00:00:00+00:00')")
    conn.execute(
        "INSERT INTO symptoms (user_key, log_date, symptom, severity, note, created_at)"
        " VALUES ('u1', '2026-06-06', 'صداع', 3, NULL, '2026-06-06T00:00:00+00:00')")
    conn.commit()
    conn.close()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _indexes(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()


# ------------------------------------------------------------- الهجرة

def test_migration_renames_legacy_tables_and_keeps_data(tmp_path):
    path = tmp_path / "legacy.db"
    _make_legacy_db(path)

    store = Store(path)

    rows = store.list_bleeding_logs("u1")
    assert rows == [{"id": 1, "start_date": "2026-06-05",
                     "bleeding_days": 5, "length_days": 5}]
    symptoms = store.list_symptom_logs("u1")
    assert symptoms == [{"id": 1, "log_date": "2026-06-06", "symptom": "صداع",
                         "severity": 3, "note": None}]

    tables = _tables(path)
    assert {"bleeding_logs", "symptom_logs"} <= tables
    assert "cycles" not in tables and "symptoms" not in tables
    # الفهارس أُعيد تسميتها لا أُهجرت
    indexes = _indexes(path)
    assert "idx_bleeding_logs_user" in indexes and "idx_cycles_user" not in indexes
    assert "idx_symptom_logs_user" in indexes and "idx_symptoms_user" not in indexes

    # المعرّف والمقيّد (تواريخ مكرّرة) ما زالا يعملان بعد الهجرة
    added = store.add_bleeding_log("u1", "2026-07-13", 4)
    assert added["id"] == 2
    from app.services.store import StoreError
    with pytest.raises(StoreError):
        store.add_bleeding_log("u1", "2026-06-05", 6)


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    _make_legacy_db(path)
    Store(path)
    store = Store(path)   # تشغيل ثانٍ بلا ضرر وبلا تكرار
    assert len(store.list_bleeding_logs("u1")) == 1
    assert store.list_symptom_logs("u1")[0]["symptom"] == "صداع"


def test_fresh_database_starts_on_the_new_model(tmp_path):
    path = tmp_path / "fresh.db"
    store = Store(path)

    tables = _tables(path)
    assert {"bleeding_logs", "symptom_logs", "health_profile", "consents"} <= tables
    assert "cycles" not in tables and "symptoms" not in tables

    assert store.list_bleeding_logs("u1") == []
    assert store.get_health_profile("u1") is None
    assert store.list_consents("u1") == []


# ------------------------------------------------------------- توافق الميدان

def test_api_accepts_old_and_new_field_names(client):
    old = client.post("/v1/cycles?user_key=device-x",
                      json={"start_date": "2026-06-05", "length_days": 5})
    assert old.status_code == 200, old.text
    assert old.json()["bleeding_days"] == 5
    assert old.json()["length_days"] == 5      # الاسم القديم في الخرج دائمًا

    new = client.post("/v1/cycles?user_key=device-x",
                      json={"start_date": "2026-07-10", "bleeding_days": 4})
    assert new.status_code == 200, new.text
    assert new.json()["bleeding_days"] == 4
    assert new.json()["length_days"] == 4

    listed = client.get("/v1/cycles?user_key=device-x").json()
    assert {row["bleeding_days"] for row in listed} == {5, 4}
    assert all(row["length_days"] == row["bleeding_days"] for row in listed)


def test_new_name_wins_when_both_are_sent(client):
    r = client.post("/v1/cycles?user_key=device-x",
                    json={"start_date": "2026-06-05",
                          "bleeding_days": 4, "length_days": 9})
    assert r.status_code == 200, r.text
    assert r.json()["bleeding_days"] == 4      # AliasChoices: الجديد أولًا


def test_cycle_stat_accepts_both_names_and_emits_both():
    from_old = CycleStat.model_validate({"start_date": "2026-06-05",
                                         "length_days": 5})
    from_new = CycleStat.model_validate({"start_date": "2026-06-05",
                                         "bleeding_days": 6})
    assert from_old.bleeding_days == 5
    assert from_new.bleeding_days == 6
    assert from_old.model_dump() == {"start_date": "2026-06-05",
                                     "bleeding_days": 5, "length_days": 5}


# ------------------------------------------------------------- الملف الصحي

def test_profile_roundtrip_and_user_isolation(client):
    empty = client.get("/v1/profile?user_key=device-x")
    assert empty.status_code == 200
    assert empty.json()["age"] is None
    assert empty.json()["conditions"] == []

    put = client.put("/v1/profile?user_key=device-x", json={
        "locale": "ar", "country_code": "fr", "digits_style": "western",
        "age": 27, "pregnancy_status": "غير حامل",
        "conditions": [{"name": "PCOS", "status": "مشخّصة"}],
    })
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["country_code"] == "FR"         # يُوحَّد تلقائيًا
    assert body["age"] == 27
    assert body["conditions"] == [{"name": "PCOS", "status": "مشخّصة"}]
    assert body["updated_at"]

    again = client.get("/v1/profile?user_key=device-x").json()
    assert again["locale"] == "ar" and again["age"] == 27

    other = client.get("/v1/profile?user_key=device-y").json()
    assert other["age"] is None                 # عزل تام بين المفاتيح


def test_profile_rejects_invalid_values(client):
    bad_payloads = [
        {"locale": "de"},
        {"digits_style": "roman"},
        {"week_start": "funday"},
        {"country_code": "EGY"},
        {"age": -1},
        {"age": 121},
    ]
    for payload in bad_payloads:
        r = client.put("/v1/profile?user_key=device-x", json=payload)
        assert r.status_code == 422, (payload, r.text)


def test_profile_put_replaces_not_merges(client):
    client.put("/v1/profile?user_key=device-x",
               json={"age": 27, "locale": "ar"})
    r = client.put("/v1/profile?user_key=device-x", json={"age": 30})
    body = r.json()
    assert body["age"] == 30
    assert body["locale"] is None               # الحقل الغائب يُمسح (استبدال كامل)


# ------------------------------------------------------------- الموافقات

def test_consents_record_replace_and_isolate(client):
    r = client.post("/v1/consents?user_key=device-x",
                    json={"consent_key": "ai_model_processing", "granted": True,
                          "version": "2026-10"})
    assert r.status_code == 200, r.text
    assert r.json()["granted"] is True
    assert r.json()["decided_at"]

    again = client.post("/v1/consents?user_key=device-x",
                        json={"consent_key": "ai_model_processing",
                              "granted": False})
    assert again.json()["granted"] is False     # استبدال القرار السابق لا مضاعفة

    rows = client.get("/v1/consents?user_key=device-x").json()
    assert [row["consent_key"] for row in rows] == ["ai_model_processing"]
    assert client.get("/v1/consents?user_key=device-y").json() == []


def test_consent_rejects_unknown_activity(client):
    r = client.post("/v1/consents?user_key=device-x",
                    json={"consent_key": "ads_tracking", "granted": True})
    assert r.status_code == 422


# ------------------------------------------------------------- حق الحذف

def _seed_user(client, user_key: str = "device-x") -> None:
    assert client.post(
        f"/v1/cycles?user_key={user_key}",
        json={"start_date": "2026-06-05", "bleeding_days": 5}).status_code == 200
    assert client.post(
        f"/v1/symptoms?user_key={user_key}",
        json={"log_date": "2026-06-06", "symptom": "صداع",
              "severity": 3}).status_code == 200
    assert client.put(
        f"/v1/profile?user_key={user_key}",
        json={"age": 27, "locale": "ar"}).status_code == 200
    assert client.post(
        f"/v1/consents?user_key={user_key}",
        json={"consent_key": "local_audit_log", "granted": True}
    ).status_code == 200


def _write_audit(tmp_path, monkeypatch, *user_keys: str) -> Path:
    """سجل تدقيق حقيقي المحتوى على مسار مؤقت — لا نلمس ملف التشغيل."""
    audit_path = tmp_path / "audit" / "responses.jsonl"
    monkeypatch.setattr(
        audit_mod, "settings",
        replace(audit_mod.settings, audit_log_path=audit_path))
    records = [{"user_id": key_fingerprint(k), "mode": "chat",
                "prompt_version": "v1.2"} for k in user_keys]
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    return audit_path


def test_delete_data_clears_store_but_keeps_audit(client, tmp_path, monkeypatch):
    _seed_user(client)
    audit_path = _write_audit(tmp_path, monkeypatch, "device-x", "device-y")

    r = client.delete("/v1/data?user_key=device-x")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"bleeding_logs_deleted": 1, "symptom_logs_deleted": 1,
                    "health_profile_deleted": 1, "consents_deleted": 1}
    assert "audit_entries_deleted" not in body  # /v1/data لا يمس التدقيق

    assert client.get("/v1/cycles?user_key=device-x").json() == []
    assert client.get("/v1/profile?user_key=device-x").json()["age"] is None
    assert client.get("/v1/consents?user_key=device-x").json() == []
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 2


def test_delete_account_purges_store_and_own_audit_only(
        client, tmp_path, monkeypatch):
    _seed_user(client)
    _seed_user(client, user_key="device-y")
    audit_path = _write_audit(tmp_path, monkeypatch, "device-x", "device-y")

    r = client.delete("/v1/account?user_key=device-x")
    assert r.status_code == 200, r.text
    assert r.json() == {"bleeding_logs_deleted": 1, "symptom_logs_deleted": 1,
                        "health_profile_deleted": 1, "consents_deleted": 1,
                        "audit_entries_deleted": 1}

    assert client.get("/v1/cycles?user_key=device-x").json() == []
    assert client.get("/v1/profile?user_key=device-x").json()["age"] is None
    assert client.get("/v1/consents?user_key=device-x").json() == []

    kept = [json.loads(line) for line in
            audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [rec["user_id"] for rec in kept] == [key_fingerprint("device-y")]

    # بيانات المستخدمة الأخرى لم تُمس
    assert len(client.get("/v1/cycles?user_key=device-y").json()) == 1


# ------------------------------------------------------------- الملف يغذي المسار

def test_profile_locale_fills_missing_chat_language(client):
    control = client.post("/api/v1/ai/chat",
                          json={"message": "إيه أعراض ما قبل الدورة؟",
                                "user_key": "device-x"})
    assert control.status_code == 200
    assert control.json()["language"] == "ar"   # بلا ملف: لغة الرسالة

    client.put("/v1/profile?user_key=device-x", json={"locale": "en"})
    r = client.post("/api/v1/ai/chat",
                    json={"message": "إيه أعراض ما قبل الدورة؟",
                          "user_key": "device-x"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"         # الملف يملأ الفراغ ثم لغة الرسالة


def test_profile_country_fills_missing_emergency_country(client):
    client.put("/v1/profile?user_key=device-x", json={"country_code": "fr"})
    r = client.post("/api/v1/ai/chat",
                    json={"message": "بنزف كل ساعة وبرمي جلطات كبيرة",
                          "user_key": "device-x"})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"] is True
    assert body["emergency_payload"]["country_code"] == "FR"
