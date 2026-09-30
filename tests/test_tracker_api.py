"""مسارات التتبّع والرؤى: دورات، أعراض، دمج البيانات في المحادثة، وحق الحذف."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import app.main as main

USER = "device-abc"


def _day(offset: int) -> str:
    return (date.today() - timedelta(days=offset)).isoformat()


# -------------------------------------------------------------------- الدورات

def test_add_and_list_cycles(client):
    r = client.post(f"/v1/cycles?user_key={USER}",
                    json={"start_date": "2026-06-05", "length_days": 38})
    assert r.status_code == 200, r.text
    assert r.json()["start_date"] == "2026-06-05"

    rows = client.get(f"/v1/cycles?user_key={USER}").json()
    assert len(rows) == 1


def test_cycle_validation_error_is_422(client):
    r = client.post(f"/v1/cycles?user_key={USER}",
                    json={"start_date": "2026-06-05", "length_days": 500})
    assert r.status_code == 422
    assert "length_days" in r.json()["detail"]


def test_delete_cycle_and_404(client):
    row = client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-06-05"}).json()
    assert client.delete(f"/v1/cycles/{row['id']}?user_key={USER}").status_code == 200
    assert client.delete(f"/v1/cycles/{row['id']}?user_key={USER}").status_code == 404


# -------------------------------------------------------------------- الأعراض

def test_add_list_delete_symptom(client):
    r = client.post(f"/v1/symptoms?user_key={USER}",
                    json={"log_date": _day(1), "symptom": "تقلصات", "severity": 4})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    assert client.get(f"/v1/symptoms?user_key={USER}").json()[0]["symptom"] == "تقلصات"
    assert client.delete(f"/v1/symptoms/{sid}?user_key={USER}").status_code == 200
    assert client.get(f"/v1/symptoms?user_key={USER}").json() == []


def test_symptom_severity_out_of_range_is_422(client):
    r = client.post(f"/v1/symptoms?user_key={USER}",
                    json={"log_date": _day(1), "symptom": "صداع", "severity": 9})
    assert r.status_code == 422  # يمنعه schema


# --------------------------------------------------------------------- الرؤى

def test_insights_work_with_no_model_and_no_data(client, monkeypatch):
    """الرؤى تعمل بلا موديل: هذا مقصود حتى لا يتوقف التحليل على مزوّد خارجي."""
    monkeypatch.setattr(main, "_llm", None)
    body = client.get(f"/v1/insights?user_key={USER}").json()
    assert body["rule_codes"] == ["INSUFFICIENT_DATA"]
    assert body["needs_doctor"] is False
    assert body["prompt_version"] == main.settings.prompt_version


def test_insights_compute_from_logged_cycles(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    for offset in (60, 88, 116):  # فواصل 28 يومًا
        client.post(f"/v1/cycles?user_key={USER}", json={"start_date": _day(offset)})

    body = client.get(f"/v1/insights?user_key={USER}").json()
    assert body["cycles_recorded"] == 3
    assert body["avg_cycle_days"] == 28
    assert body["rule_codes"] == ["NO_ALERT_PATTERN"]
    assert body["needs_doctor"] is False


def test_insights_flag_long_cycles_and_explain_them(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-01-01"})
    client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-03-15"})  # 73 يومًا
    client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-06-01"})

    body = client.get(f"/v1/insights?user_key={USER}").json()
    assert "LONG_CYCLE" in body["rule_codes"]
    assert body["needs_doctor"] is True
    # الشرح موجود ومأخوذ من المسرد لا مخترع
    assert "LONG_CYCLE" in body["glossary"] and body["glossary"]["LONG_CYCLE"]


def test_insights_use_symptom_journal(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    for offset, name in ((5, "ألم شديد"), (20, "نزيف غزير"), (40, "دوخة")):
        client.post(f"/v1/symptoms?user_key={USER}",
                    json={"log_date": _day(offset), "symptom": name, "severity": 5})

    body = client.get(f"/v1/insights?user_key={USER}").json()
    assert "REPEATED_SEVERE_SYMPTOMS" in body["rule_codes"]
    assert body["needs_doctor"] is False  # MONITOR لا MEDICAL_REVIEW


def test_old_symptoms_outside_window_do_not_flag(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    for offset in (200, 300, 400):
        client.post(f"/v1/symptoms?user_key={USER}",
                    json={"log_date": _day(offset), "symptom": "ألم", "severity": 5})
    body = client.get(f"/v1/insights?user_key={USER}").json()
    assert "REPEATED_SEVERE_SYMPTOMS" not in body["rule_codes"]


# ------------------------------------------------- البيانات المسجّلة في المحادثة

def test_logged_cycles_drive_the_chat_pipeline(client, monkeypatch):
    """الأرقام في الرد تأتي من البيانات المخزّنة لا من إدخال يدوي في الواجهة."""
    monkeypatch.setattr(main, "_llm", None)
    for offset in (0, 30, 60):
        client.post(f"/v1/cycles?user_key={USER}", json={"start_date": _day(offset)})

    body = client.post("/v1/chat", json={
        "message": "إيه أعراض ما قبل الدورة؟",
        "user_key": USER,
        # سياق يدوي مغلوط عدد دوراته 0 — يجب أن تتغلّب عليه البيانات المسجّلة
        "user_context": {"cycles_recorded": 0},
    }).json()

    assert body["rule_codes"] == ["NO_ALERT_PATTERN"]  # 3 دورات مسجّلة فعلًا


def test_chat_emergency_path_ignores_rate_limit(client, monkeypatch):
    """طلب طوارئ لا يُحجب أبدًا بسبب الحصة."""
    monkeypatch.setattr(main, "_llm", None)
    limit = main.settings.rate_limit_requests
    for _ in range(limit):
        client.post("/v1/chat", json={"message": "سؤال", "user_key": USER})

    # الحصة استُهلكت: الطلب العادي يُرفض
    assert client.post("/v1/chat", json={"message": "سؤال", "user_key": USER}).status_code == 429
    # لكن الطوارئ تمرّ
    r = client.post("/v1/chat", json={"message": "بنزف كل ساعة وبرمي جلطات كبيرة",
                                      "user_key": USER})
    assert r.status_code == 200
    assert r.json()["emergency"] is True


def test_rate_limited_response_has_retry_after(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    for _ in range(main.settings.rate_limit_requests):
        client.post("/v1/chat", json={"message": "سؤال", "user_key": USER})
    r = client.post("/v1/chat", json={"message": "سؤال", "user_key": USER})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


# ----------------------------------------------------------------- حق الحذف

def test_delete_all_data_endpoint(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", None)
    client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-06-05"})
    client.post(f"/v1/symptoms?user_key={USER}",
                json={"log_date": _day(1), "symptom": "صداع", "severity": 3})

    body = client.delete(f"/v1/data?user_key={USER}").json()
    assert body == {"cycles_deleted": 1, "symptoms_deleted": 1}
    assert client.get(f"/v1/cycles?user_key={USER}").json() == []
    assert client.get(f"/v1/symptoms?user_key={USER}").json() == []


def test_store_error_becomes_422_not_500(client):
    client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-06-05"})
    r = client.post(f"/v1/cycles?user_key={USER}", json={"start_date": "2026-06-05"})
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/v1/cycles", "/v1/symptoms", "/v1/insights"])
def test_user_key_is_required(client, path):
    assert client.get(path).status_code == 422


def test_user_key_is_required_for_delete_all(client):
    assert client.delete("/v1/data").status_code == 422
