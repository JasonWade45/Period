import io
import json
import zipfile

from sqlalchemy import func, select

from app.tests.conftest import today
from app.tests.integration.test_tracking import _add_cycles


def test_export_json_contains_everything_but_secrets(client, auth, fake_ai):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41])
    client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "symptoms": ["headache"]}, headers=h)
    client.post("/api/v1/ai/chat", json={"message": "hi"}, headers=h)
    r = client.get("/api/v1/export/health-data?format=json", headers=h)
    assert r.status_code == 200
    doc = r.json()
    assert len(doc["cycles"]) == 5
    assert doc["symptom_logs"][0]["symptoms"] == ["headache"]
    assert doc["medical_findings"]
    assert doc["ai_conversations"][0]["messages"]
    assert "password_hash" not in json.dumps(doc)


def test_export_csv_zip(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [29, 30])
    r = client.get("/api/v1/export/health-data?format=csv", headers=h)
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "cycles.csv" in zf.namelist()
    assert zf.read("cycles.csv").decode("utf-8-sig").count("\n") == 4  # header + 3 rows


def test_doctor_report(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41], days_after_last=5)
    rep = client.get("/api/v1/export/doctor-report", headers=h).json()
    assert rep["cycle_lengths"] == [42, 38, 41] or rep["cycle_lengths"] == [40, 42, 38, 41]
    assert "does not constitute a medical diagnosis" in rep["disclaimer"]
    txt = client.get("/api/v1/export/doctor-report?format=text", headers=h).text
    assert "CycleCare Health Summary" in txt and "Questions" in txt


def test_doctor_report_arabic(client, auth):
    h, _ = auth(language="ar")
    client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "pain_level": 5}, headers=h)
    txt = client.get("/api/v1/export/doctor-report?format=text", headers=h).text
    assert "ملخص CycleCare الصحي" in txt
    assert "لا يُعد تشخيصًا طبيًا" in txt
    assert "متوسط" in txt and "moderate" not in txt


def test_account_deletion_removes_all_data(client, auth, fake_ai, db_session):
    from app.models import AIMessage, BleedingLog, Cycle, MedicalFinding, SymptomLog, User

    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41])
    client.post("/api/v1/bleeding", json={"log_date": today().isoformat(), "flow_level": "light"}, headers=h)
    client.post("/api/v1/ai/chat", json={"message": "hi"}, headers=h)

    assert (
        client.request("DELETE", "/api/v1/auth/account", json={"password": "wrong-password", "confirm": True}, headers=h).status_code == 401
    )
    assert (
        client.request("DELETE", "/api/v1/auth/account", json={"password": "correct-horse-battery", "confirm": True}, headers=h).status_code
        == 204
    )

    db = db_session
    for model in (User, Cycle, BleedingLog, SymptomLog, MedicalFinding, AIMessage):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_audit_log_has_no_raw_ip_or_secrets(client, auth, db_session):
    from app.models import AuditLog

    auth()
    db = db_session
    rows = db.scalars(select(AuditLog)).all()
    assert rows and rows[0].action == "auth.register"
    for r in rows:
        assert r.ip_hash is None or ("." not in r.ip_hash and len(r.ip_hash) == 32)
        assert "password" not in json.dumps(r.metadata_ or {})


def test_security_headers(client, auth):
    h, _ = auth()
    r = client.get("/api/v1/cycles", headers=h)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_health_endpoint(client):
    body = client.get("/health").json()
    assert body["database"] is True
    assert body["ruleset_version"] == "1.0.0"


def test_preview_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "CycleCare" in r.text and 'dir="rtl"' in r.text
