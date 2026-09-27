from datetime import timedelta

from app.tests.conftest import today


def _add_cycles(client, headers, lengths, days_after_last=5, period_days=5):
    start = today() - timedelta(days=sum(lengths) + days_after_last)
    d = start
    for n in [*lengths, None]:
        end = d + timedelta(days=period_days - 1)
        r = client.post("/api/v1/cycles", json={"start_date": d.isoformat(), "end_date": min(end, today()).isoformat()}, headers=headers)
        assert r.status_code == 201, r.text
        if n:
            d += timedelta(days=n)
    return start


def test_start_period_quick_log(client, auth):
    h, _ = auth()
    d = today().isoformat()
    r = client.post("/api/v1/cycles", json={"start_date": d, "flow_level": "medium", "pain_level": 4, "symptoms": ["bloating"]}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["data"]["start_date"] == d
    assert body["medical"]["overall_severity"] == "NORMAL"
    assert len(client.get("/api/v1/bleeding", headers=h).json()) == 1
    assert client.get("/api/v1/symptoms", headers=h).json()[0]["symptoms"] == ["bloating"]


def test_cycle_length_derived(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [29, 34])
    rows = client.get("/api/v1/cycles", headers=h).json()
    lengths = [r["cycle_length"] for r in sorted(rows, key=lambda r: r["start_date"])]
    assert lengths == [29, 34, None]


def test_overlapping_periods_rejected(client, auth):
    h, _ = auth()
    client.post("/api/v1/cycles", json={"start_date": "2026-08-01", "end_date": "2026-08-07"}, headers=h)
    r = client.post("/api/v1/cycles", json={"start_date": "2026-08-05"}, headers=h)
    assert r.status_code == 409


def test_invalid_dates_rejected(client, auth):
    h, _ = auth()
    future = (today() + timedelta(days=10)).isoformat()
    assert client.post("/api/v1/cycles", json={"start_date": future}, headers=h).status_code == 422
    assert client.post("/api/v1/cycles", json={"start_date": "2026-08-10", "end_date": "2026-08-01"}, headers=h).status_code == 422


def test_complete_cycle(client, auth):
    h, _ = auth()
    start = today() - timedelta(days=4)
    cid = client.post("/api/v1/cycles", json={"start_date": start.isoformat()}, headers=h).json()["data"]["id"]
    r = client.post(f"/api/v1/cycles/{cid}/complete", json={"end_date": today().isoformat()}, headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["duration_days"] == 5


def test_bleeding_one_per_day_and_patch(client, auth):
    h, _ = auth()
    d = today().isoformat()
    r = client.post("/api/v1/bleeding", json={"log_date": d, "flow_level": "light"}, headers=h)
    assert r.status_code == 201
    assert client.post("/api/v1/bleeding", json={"log_date": d, "flow_level": "heavy"}, headers=h).status_code == 409
    bid = r.json()["data"]["id"]
    r = client.patch(f"/api/v1/bleeding/{bid}", json={"flow_level": "heavy", "leakage": True}, headers=h)
    assert r.json()["data"]["flow_level"] == "heavy"


def test_invalid_enum_rejected(client, auth):
    h, _ = auth()
    assert client.post("/api/v1/bleeding", json={"log_date": today().isoformat(), "flow_level": "torrential"}, headers=h).status_code == 422
    assert client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "symptoms": ["made_up"]}, headers=h).status_code == 422
    assert client.post("/api/v1/symptoms", json={"log_date": today().isoformat(), "pain_level": 11}, headers=h).status_code == 422


def test_analytics_and_prediction(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [29, 34, 41, 27, 38, 31])
    ov = client.get("/api/v1/analytics/overview", headers=h).json()
    assert ov["cycle_count"] == 6
    assert ov["min_cycle_length"] == 27 and ov["max_cycle_length"] == 41
    assert ov["pattern"] == "variable"
    p = client.get("/api/v1/predictions/next-period", headers=h).json()
    assert p["available"] is True
    assert p["is_estimate"] is True
    assert p["confidence"] in ("low", "medium")


def test_prediction_unavailable_for_new_user(client, auth):
    h, _ = auth()
    p = client.get("/api/v1/predictions/next-period", headers=h).json()
    assert p["available"] is False
    assert p["estimated_start"] is None


def test_dashboard(client, auth):
    h, _ = auth()
    _add_cycles(client, h, [28, 30, 29, 31], days_after_last=10)
    d = client.get("/api/v1/dashboard", headers=h).json()
    assert d["cycle_day"] == 11
    assert d["last_cycle_lengths"] == [28, 30, 29, 31]
    assert d["overall_severity"] == "NORMAL"
    assert d["prediction"]["available"]


def test_health_profile_onboarding(client, auth):
    h, _ = auth()
    r = client.put(
        "/api/v1/users/me/health-profile",
        json={"contraception_type": "none", "pregnancy_possibility": "no", "typical_cycle_length": 30, "known_pcos": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["known_pcos"] is True
    assert client.get("/api/v1/users/me/health-profile", headers=h).json()["typical_cycle_length"] == 30


def test_settings(client, auth):
    h, _ = auth()
    assert client.get("/api/v1/users/me/settings", headers=h).json()["period_reminders"] is True
    r = client.patch("/api/v1/users/me/settings", json={"weekly_summary": False, "reminder_time": "21:30:00"}, headers=h)
    assert r.json()["weekly_summary"] is False


def test_period_symptoms_back_pain_and_pms_accepted(client, auth):
    h, _ = auth()
    body = {
        "log_date": today().isoformat(),
        "pain_level": 5,
        "pain_location": ["lower_back", "lower_abdomen", "thighs_legs"],
        "symptoms": [
            "vomiting",
            "muscle_joint_aches",
            "hot_flashes",
            "swelling",
            "food_cravings",
            "loss_of_appetite",
            "low_mood",
            "crying_spells",
            "difficulty_concentrating",
            "low_libido",
        ],
    }
    r = client.post("/api/v1/symptoms", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["medical"]["overall_severity"] == "NORMAL"  # ordinary period symptoms are not alerts
    saved = client.get("/api/v1/symptoms", headers=h).json()[0]
    assert "lower_back" in saved["pain_location"] and "vomiting" in saved["symptoms"]
