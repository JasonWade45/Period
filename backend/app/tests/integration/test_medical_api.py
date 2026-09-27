from datetime import timedelta

from app.tests.conftest import today
from app.tests.integration.test_tracking import _add_cycles


def test_long_cycles_create_medical_review_finding(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41])
    ev = client.post("/api/v1/medical/evaluate", headers=h).json()
    assert ev["overall_severity"] == "MEDICAL_REVIEW"
    active = client.get("/api/v1/medical/findings/active", headers=h).json()
    mr1 = next(f for f in active if f["rule_code"] == "MR-001")
    assert mr1["severity"] == "MEDICAL_REVIEW"
    assert mr1["rule_version"] == "1.0"
    assert mr1["evidence"]["cycle_lengths"] == [40, 42, 38, 41]


def test_urgent_symptom_surfaces_immediately_on_write(client, auth):
    h, _ = auth()
    r = client.post(
        "/api/v1/symptoms",
        json={"log_date": today().isoformat(), "pain_level": 9, "symptoms": ["fainting"]},
        headers=h,
    )
    assert r.status_code == 201
    med = r.json()["medical"]
    assert med["overall_severity"] == "EMERGENCY"
    assert med["requires_immediate_attention"] is True
    assert med["alert"]["is_emergency"] is True


def test_pregnancy_bleeding_flow(client, auth):
    h, _ = auth()
    client.post("/api/v1/pregnancy-tests", json={"test_date": (today() - timedelta(days=10)).isoformat(), "result": "positive"}, headers=h)
    r = client.post("/api/v1/bleeding", json={"log_date": today().isoformat(), "flow_level": "spotting"}, headers=h)
    assert r.json()["medical"]["overall_severity"] == "URGENT"
    r = client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "symptoms": ["shoulder_tip_pain"]}, headers=h)
    assert r.json()["medical"]["overall_severity"] == "EMERGENCY"


def test_acknowledge_but_never_modify_severity(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41])
    client.post("/api/v1/medical/evaluate", headers=h)
    f = client.get("/api/v1/medical/findings/active", headers=h).json()[0]
    r = client.post(f"/api/v1/medical/findings/{f['id']}/acknowledge", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    # Acknowledgement persists across re-evaluation while severity is unchanged.
    client.post("/api/v1/medical/evaluate", headers=h)
    again = next(x for x in client.get("/api/v1/medical/findings/active", headers=h).json() if x["id"] == f["id"])
    assert again["status"] == "acknowledged"
    # No endpoint accepts a severity from the client.
    assert client.patch(f"/api/v1/medical/findings/{f['id']}", json={"severity": "NORMAL"}, headers=h).status_code in (404, 405)
    assert client.put(f"/api/v1/medical/findings/{f['id']}", json={"severity": "NORMAL"}, headers=h).status_code in (404, 405)


def test_findings_resolve_when_data_changes(client, auth):
    h, _ = auth()
    r = client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "symptoms": ["bleeding_after_sex"]}, headers=h)
    sid = r.json()["data"]["id"]
    assert any(f["rule_code"] == "MR-005" for f in client.get("/api/v1/medical/findings/active", headers=h).json())
    client.delete(f"/api/v1/symptoms/{sid}", headers=h)
    assert not any(f["rule_code"] == "MR-005" for f in client.get("/api/v1/medical/findings/active", headers=h).json())
    history = client.get("/api/v1/medical/findings?status=resolved", headers=h).json()
    assert any(f["rule_code"] == "MR-005" for f in history)  # kept for audit, not deleted


def test_multiple_findings_consolidated(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [40, 42, 38, 41], days_after_last=10)
    d = (today() - timedelta(days=10)).isoformat()
    client.post("/api/v1/bleeding", json={"log_date": d, "flow_level": "heavy", "frequent_changes": True}, headers=h)
    r = client.post("/api/v1/symptoms", json={"log_date": d, "pain_level": 8, "affects_daily_activity": True}, headers=h)
    alert = r.json()["medical"]["alert"]
    assert alert["rule_code"] == "MR-010"
    assert {"MR-001", "MR-003", "MR-006"} <= set(alert["evidence"]["component_rule_codes"])


def test_arabic_user_gets_arabic_findings(client, auth):
    h, _ = auth(language="ar")
    _add_cycles(client, h, [40, 42, 38, 41])
    active = client.get("/api/v1/medical/findings/active", headers=h).json()
    assert active[0]["language"] == "ar"
    assert "غير منتظمة" in active[0]["title"]


def test_rules_endpoint(client, auth):
    h, _ = auth()
    cfg = client.get("/api/v1/medical/rules", headers=h).json()
    assert cfg["rules"]["MR-001"]["thresholds"] == {
        "min_days": 21,
        "max_days": 35,
        "adolescent_age_below": 18,
        "adolescent_max_days": 45,
        "window_cycles": 6,
        "min_out_of_range": 2,
    }
