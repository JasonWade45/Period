"""اختبارات نظام الأسئلة المهيكلة، التسجيل اليومي بلا ضياع بيانات، اشتقاق الدورات، ومحرك القواعد (البند 8)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import CycleStat, Severity, UserContext
from app.services.question_catalog import get_contextual_questions, load_question_catalog
from app.services.rules_engine import (
    compute_all_findings,
    compute_anemia_pattern,
    compute_cycle_lengths,
    compute_heavy_soaking,
    compute_intermenstrual_bleeding,
    compute_post_coital_bleeding,
    compute_pregnancy_warning,
    compute_prolonged_bleeding,
    compute_severe_pain_functional,
    compute_shadow_findings,
)
from app.services.store import Store, StoreError


@pytest.fixture()
def temp_store(tmp_path: Path) -> Store:
    return Store(str(tmp_path / "test_structured.db"))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import app.main as main_mod

    st = Store(str(tmp_path / "api_structured.db"))
    monkeypatch.setattr(main_mod, "_store", st)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. حفظ كامل بلا ضياع (Zero Data Loss)
# ---------------------------------------------------------------------------
def test_zero_data_loss_full_daily_log_roundtrip(temp_store: Store) -> None:
    ukey = "user-zero-loss"
    payload = {
        "bleeding_level": "heavy",
        "pain_score": 4,
        "pain_blocks_life": True,
        "mood": "low",
        "mood_tags": ["anxiety", "sadness"],
        "energy": 2,
        "pad_count": 6,
        "soak_hourly": True,
        "clots": "large",
        "dizzy": True,
        "bleeding_color": "dark_red",
        "discharge": "dry",
        "intermenstrual": False,
        "post_intercourse_bleeding": False,
        "intercourse": True,
        "contraception_used": "condom",
        "ovulation_test": "not_done",
        "pregnancy_test": "negative",
        "bbt": 36.65,
        "sleep_hours": 7.5,
        "water_ml": 2000,
        "exercise": "walk",
        "stress": 4,
        "medications": "حديد + ماغنسيوم",
        "symptoms": [
            {"symptom_id": "cramps", "severity": 5},
            {"symptom_id": "fatigue", "severity": 4},
            {"symptom_id": "headache", "severity": 3},
        ],
        "answered": ["d.bleeding", "d.pain", "d.symptoms", "d.mood", "d.energy"],
        "skipped": ["b.color"],
        "note": "يوم أول غزير",
        "client_id": "pwa-test-1",
        "source": "daily_screen",
    }
    saved = temp_store.upsert_daily_log(ukey, "2026-10-10", payload, today_local="2026-10-20")
    loaded = temp_store.get_daily_log(ukey, "2026-10-10")
    assert loaded is not None
    assert saved["id"] == loaded["id"]
    assert loaded["bleeding_level"] == "heavy"
    assert loaded["pain_score"] == 4
    assert loaded["pain_blocks_life"] is True
    assert loaded["mood"] == "low"
    assert loaded["mood_tags"] == ["anxiety", "sadness"]
    assert loaded["energy"] == 2
    assert loaded["pad_count"] == 6
    assert loaded["soak_hourly"] is True
    assert loaded["clots"] == "large"
    assert loaded["dizzy"] is True
    assert loaded["bleeding_color"] == "dark_red"
    assert loaded["discharge"] == "dry"
    assert loaded["intermenstrual"] is False
    assert loaded["post_intercourse_bleeding"] is False
    assert loaded["intercourse"] is True
    assert loaded["contraception_used"] == "condom"
    assert loaded["bbt"] == pytest.approx(36.65)
    assert loaded["sleep_hours"] == pytest.approx(7.5)
    assert loaded["water_ml"] == 2000
    assert loaded["exercise"] == "walk"
    assert loaded["stress"] == 4
    assert loaded["medications"] == "حديد + ماغنسيوم"
    assert {s["symptom_id"] for s in loaded["symptoms"]} == {"cramps", "fatigue", "headache"}
    assert "d.bleeding" in loaded["answered"]
    assert loaded["skipped"] == ["b.color"]
    assert loaded["client_id"] == "pwa-test-1"


# ---------------------------------------------------------------------------
# 2. عدم التكرار (Idempotent Upsert) + سجل التعديلات (log_edits) + حل التعارض لكل حقل
# ---------------------------------------------------------------------------
def test_idempotent_upsert_and_log_edits_and_conflict_resolution(temp_store: Store) -> None:
    ukey = "user-upsert"
    first = temp_store.upsert_daily_log(
        ukey,
        "2026-10-12",
        {
            "bleeding_level": "light",
            "pain_score": 3,
            "mood": "calm",
            "field_timestamps": {
                "bleeding_level": "2026-10-12T10:00:00+00:00",
                "pain_score": "2026-10-12T10:00:00+00:00",
            },
        },
        today_local="2026-10-20",
    )
    # تحديث لاحق مع طابع زمني أقدم لـ pain_score وأحدث لـ bleeding_level
    second = temp_store.upsert_daily_log(
        ukey,
        "2026-10-12",
        {
            "bleeding_level": "medium",
            "pain_score": 1,
            "field_timestamps": {
                "bleeding_level": "2026-10-12T11:00:00+00:00",
                "pain_score": "2026-10-12T09:00:00+00:00",  # أقدم -> يجب تجاهله
            },
        },
        today_local="2026-10-20",
    )
    assert first["id"] == second["id"]
    logs = temp_store.list_daily_logs(ukey)
    assert len(logs) == 1
    assert second["bleeding_level"] == "medium"
    assert second["pain_score"] == 3  # حافظ على القيمة الأحدث

    edits = temp_store.list_log_edits(ukey, log_date="2026-10-12")
    assert any(
        e["field"] == "bleeding_level" and e["old_val"] == "light" and e["new_val"] == "medium"
        for e in edits
    )


# ---------------------------------------------------------------------------
# 3. التخطّي ≠ «لا» (Skip != No) والتمييز بين 3 حالات لليوم
# ---------------------------------------------------------------------------
def test_skip_is_not_no_and_three_day_states(temp_store: Store) -> None:
    ukey = "user-skip"
    # الحالة 1: يوم لم يُفتَح أصلًا -> لا سجل في daily_logs
    assert temp_store.get_daily_log(ukey, "2026-10-01") is None

    # الحالة 2: يوم فُتح وسُجّل صراحةً بلا أعراض
    day2 = temp_store.upsert_daily_log(
        ukey,
        "2026-10-02",
        {
            "bleeding_level": "none",
            "symptoms": [],
            "answered": ["d.bleeding", "d.symptoms"],
            "skipped": [],
        },
        today_local="2026-10-20",
    )
    assert day2["symptoms"] == []
    assert "d.symptoms" in day2["answered"]
    assert "d.symptoms" not in day2["skipped"]

    # الحالة 3: يوم تم تخطّي سؤال الأعراض وأسئلة النزيف فيه
    day3 = temp_store.upsert_daily_log(
        ukey,
        "2026-10-03",
        {
            "bleeding_level": "heavy",
            "soak_hourly": True,
            "skipped": ["b.soak_hourly", "d.symptoms"],
        },
        today_local="2026-10-20",
    )
    assert "b.soak_hourly" in day3["skipped"]
    # رغم وجود soak_hourly=True، لأن السؤال في skipped يجب ألا يطلق قاعدة HEAVY_SOAKING
    assert compute_heavy_soaking([day3]) is None


# ---------------------------------------------------------------------------
# 4. اشتقاق الدورات (Cycle Derivation) + منع تواريخ النزيف المستقبلية
# ---------------------------------------------------------------------------
def test_cycle_derivation_continuous_and_two_day_gap_and_spotting(temp_store: Store) -> None:
    ukey = "user-derivation"
    # دورة أولى: 3 أيام نزيف متصلة (1 إلى 3 سبتمبر)
    for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
        temp_store.upsert_daily_log(ukey, d, {"bleeding_level": "medium"}, today_local="2026-10-20")
    # يوم واحد بلا نزيف (4 سبتمبر) يليه نزيف في 5 سبتمبر -> يظل ضمن نفس الدورة (الفاصل < يومين)
    temp_store.upsert_daily_log(ukey, "2026-09-04", {"bleeding_level": "none"}, today_local="2026-10-20")
    temp_store.upsert_daily_log(ukey, "2026-09-05", {"bleeding_level": "light"}, today_local="2026-10-20")

    # تبقيع منفرد في منتصف الدورة (15 سبتمبر) -> لا يبدأ دورة جديدة
    temp_store.upsert_daily_log(ukey, "2026-09-15", {"bleeding_level": "spotting"}, today_local="2026-10-20")

    # دورة ثانية بعد فاصل >= يومين بلا نزيف (29 و30 سبتمبر و1 أكتوبر)
    for d in ("2026-09-29", "2026-09-30", "2026-10-01"):
        temp_store.upsert_daily_log(ukey, d, {"bleeding_level": "heavy"}, today_local="2026-10-20")

    cycles = sorted(temp_store.list_cycles(ukey), key=lambda c: c["start_date"])
    assert len(cycles) == 2
    assert cycles[0]["start_date"] == "2026-09-01"
    assert cycles[0]["bleeding_days"] == 4  # أيام النزيف الفعلية (1، 2، 3، 5 سبتمبر)
    assert cycles[0]["length_days"] == 4
    assert cycles[0]["derived_from_logs"] is True

    assert cycles[1]["start_date"] == "2026-09-29"
    assert cycles[1]["bleeding_days"] == 3

    stats = temp_store.cycle_stats(ukey)
    assert stats["cycle_gaps"] == [28]  # من 2026-09-01 إلى 2026-09-29 = 28 يومًا

    # رفض تسجيل نزيف في تاريخ مستقبلي
    with pytest.raises(StoreError):
        temp_store.upsert_daily_log(
            ukey,
            "2026-10-25",
            {"bleeding_level": "medium"},
            today_local="2026-10-20",
        )


# ---------------------------------------------------------------------------
# 5. الأسئلة السياقية (Contextual Questions) والأسئلة الحسّاسة
# ---------------------------------------------------------------------------
def test_contextual_questions_visibility_and_sensitive_toggle() -> None:
    catalog = load_question_catalog()
    assert len(catalog["symptom_catalog"]) == 20

    # يوم نزيف -> تظهر أسئلة النزيف (b.*) ولا تظهر أسئلة غير النزيف (n.*)
    bleed_ctx = get_contextual_questions(
        locale="ar",
        bleeding_level="heavy",
        pregnancy_status="no",
        show_sensitive=False,
    )
    assert len(bleed_ctx["bleeding_questions"]) == 5
    assert len(bleed_ctx["non_bleeding_questions"]) == 0

    # يوم بلا نزيف مع إخفاء الأسئلة الحسّاسة افتراضيًا -> تختفي الأسئلة الموسومة بـ sensitive=true
    non_bleed_default = get_contextual_questions(
        locale="ar",
        bleeding_level="none",
        pregnancy_status="no",
        show_sensitive=False,
    )
    assert len(non_bleed_default["bleeding_questions"]) == 0
    nb_ids_default = [q["id"] for q in non_bleed_default["non_bleeding_questions"]]
    assert "n.discharge" in nb_ids_default
    assert "n.intermenstrual" in nb_ids_default
    assert "n.post_intercourse_bleeding" not in nb_ids_default
    assert "n.intercourse" not in nb_ids_default
    assert "n.pregnancy_test" not in nb_ids_default

    # تفعيل الأسئلة الحسّاسة -> تظهر أسئلة الجماع واختبار الحمل
    non_bleed_sensitive = get_contextual_questions(
        locale="ar",
        bleeding_level="none",
        pregnancy_status="trying",
        show_sensitive=True,
    )
    nb_ids_sens = [q["id"] for q in non_bleed_sensitive["non_bleeding_questions"]]
    assert "n.post_intercourse_bleeding" in nb_ids_sens
    assert "n.intercourse" in nb_ids_sens
    assert "n.ovulation_test" in nb_ids_sens
    assert "n.pregnancy_test" in nb_ids_sens
    assert "n.bbt" in nb_ids_sens


# ---------------------------------------------------------------------------
# 6. محرك القواعد الحتمي + حدود التفعيل + وضع الظل (Shadow Mode)
# ---------------------------------------------------------------------------
def test_rules_boundaries_and_shadow_mode_isolation() -> None:
    # حدود PROLONGED_BLEEDING: 7 أيام (لا تفعيل)، 8 أيام (تفعيل)، 9 أيام (تفعيل)
    ctx_7 = UserContext(last_cycles=[CycleStat(start_date="2026-10-01", length_days=7)])
    ctx_8 = UserContext(last_cycles=[CycleStat(start_date="2026-10-01", length_days=8)])
    ctx_9 = UserContext(last_cycles=[CycleStat(start_date="2026-10-01", length_days=9)])
    assert compute_prolonged_bleeding(ctx_7) is None
    assert compute_prolonged_bleeding(ctx_8) is not None
    assert compute_prolonged_bleeding(ctx_9) is not None

    # حدود طول الدورة: 20 (SHORT_CYCLE)، 21 (طبيعي)، 38 (طبيعي)، 39 (LONG_CYCLE)
    assert [f.rule_code for f in compute_cycle_lengths(UserContext(cycle_gaps=[20]))] == ["SHORT_CYCLE"]
    assert compute_cycle_lengths(UserContext(cycle_gaps=[21])) == []
    assert compute_cycle_lengths(UserContext(cycle_gaps=[38])) == []
    assert [f.rule_code for f in compute_cycle_lengths(UserContext(cycle_gaps=[39]))] == ["LONG_CYCLE"]

    # PREGNANCY_WARNING: نزيف أو ألم شديد مع حمل مؤكد/محتمل أو اختبار إيجابي -> EMERGENCY
    preg_ctx = UserContext(pregnancy_status="confirmed")
    preg_logs = [{"log_date": "2026-10-18", "bleeding_level": "light", "skipped": []}]
    preg_findings = compute_pregnancy_warning(preg_ctx, preg_logs)
    assert len(preg_findings) == 1
    assert preg_findings[0].rule_code == "PREGNANCY_WARNING"
    assert preg_findings[0].severity == Severity.EMERGENCY

    # القواعد المقترحة الجديدة (TODO(clinician)) تعمل في وضع الظل (shadow mode)
    shadow_logs = [
        {
            "log_date": "2026-10-10",
            "bleeding_level": "heavy",
            "soak_hourly": True,
            "pain_score": 4,
            "pain_blocks_life": True,
            "dizzy": True,
            "symptoms": ["fatigue"],
            "skipped": [],
        },
        {
            "log_date": "2026-10-11",
            "bleeding_level": "heavy",
            "pain_score": 5,
            "pain_blocks_life": True,
            "dizzy": True,
            "symptoms": ["fatigue"],
            "skipped": [],
        },
        {
            "log_date": "2026-10-12",
            "bleeding_level": "none",
            "pain_score": 4,
            "pain_blocks_life": True,
            "intermenstrual_bleeding": "bleeding",
            "post_intercourse_bleeding": True,
            "skipped": [],
        },
    ]
    assert compute_heavy_soaking(shadow_logs) is not None
    assert compute_intermenstrual_bleeding(shadow_logs) is not None
    assert compute_post_coital_bleeding(shadow_logs) is not None
    assert compute_severe_pain_functional(shadow_logs) is not None
    assert compute_anemia_pattern(shadow_logs) is not None

    # التحقق من أن compute_all_findings الافتراضي (include_shadow=False) لا يعرض قواعد الظل للمستخدمة
    normal_ctx = UserContext(pregnancy_status="no")
    active_findings = compute_all_findings(normal_ctx, [], shadow_logs, include_shadow=False)
    active_codes = {f.rule_code for f in active_findings}
    shadow_findings = compute_shadow_findings(normal_ctx, shadow_logs)
    shadow_codes = {f.rule_code for f in shadow_findings}

    assert shadow_codes == {
        "HEAVY_SOAKING",
        "INTERMENSTRUAL_BLEEDING",
        "POST_COITAL_BLEEDING",
        "SEVERE_PAIN_FUNCTIONAL",
        "ANEMIA_PATTERN",
    }
    assert active_codes.isdisjoint(shadow_codes)


# ---------------------------------------------------------------------------
# 7. فحص السلامة اليومي الحتمي (Safety Check) + انعدام النص الحر في السجلات
# ---------------------------------------------------------------------------
def test_daily_safety_check_immediate_emergency_and_crisis_without_llm(client: TestClient) -> None:
    # 1) علامة خطر جسدية -> طوارئ فورية بلا موديل
    r_med = client.post(
        "/v1/safety-check?user_key=safety-user-1",
        json={
            "log_date": "2026-10-20",
            "flags": ["flag.heavy_soak"],
            "country_code": "EG",
        },
    )
    assert r_med.status_code == 200
    body_med = r_med.json()
    assert body_med["triggered"] is True
    assert body_med["emergency"] is True
    assert body_med["crisis"] is False
    assert "123" in body_med["answer"]

    # 2) علامة ضيق نفسي شديد -> مسار أزمة فوري بلا موديل
    r_crisis = client.post(
        "/v1/safety-check?user_key=safety-user-1",
        json={
            "log_date": "2026-10-20",
            "flags": ["flag.self_harm_thoughts"],
            "country_code": "EG",
        },
    )
    assert r_crisis.status_code == 200
    body_crisis = r_crisis.json()
    assert body_crisis["triggered"] is True
    assert body_crisis["emergency"] is True
    assert body_crisis["crisis"] is True

    # 3) التحقق من حفظ رموز القواعد فقط في safety_events في التصدير الكامل
    exp = client.get("/v1/export/full.json?user_key=safety-user-1").json()
    events = exp["safety_events"]
    assert len(events) == 2
    assert {e["triggered_by"] for e in events} == {"flag.heavy_soak", "flag.self_harm_thoughts"}


# ---------------------------------------------------------------------------
# 8. العمل الكامل بلا إنترنت وبلا مفتاح LLM (Offline Operation)
# ---------------------------------------------------------------------------
def test_offline_logging_derivation_and_insights_without_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_llm", None)
    ukey = "offline-user"

    for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"):
        resp = client.put(
            f"/v1/daily-logs?user_key={ukey}",
            json={
                "log_date": day,
                "bleeding_level": "heavy" if day == "2026-10-01" else "medium",
                "soak_hourly": day == "2026-10-01",
                "pain_score": 4,
                "symptoms": ["cramps"],
                "today_local": "2026-10-20",
            },
        )
        assert resp.status_code == 200

    cycles = client.get(f"/v1/cycles?user_key={ukey}").json()
    assert len(cycles) == 1
    assert cycles[0]["start_date"] == "2026-10-01"
    assert cycles[0]["bleeding_days"] == 4

    ins = client.get(f"/v1/insights?user_key={ukey}").json()
    assert ins["cycles_recorded"] == 1
    # HEAVY_SOAKING يُحسب في shadow_findings ولا يظهر في findings المفعّلة
    assert any(f["rule_code"] == "HEAVY_SOAKING" for f in ins["shadow_findings"])
    assert all(f["rule_code"] != "HEAVY_SOAKING" for f in ins["findings"])


# ---------------------------------------------------------------------------
# 9. التصدير الكامل (JSON + CSV مع UTF-8 BOM + PDF)
# ---------------------------------------------------------------------------
def test_full_export_json_csv_bom_and_pdf(client: TestClient) -> None:
    ukey = "export-user"
    client.put(
        f"/v1/daily-logs?user_key={ukey}",
        json={
            "log_date": "2026-10-15",
            "bleeding_level": "medium",
            "pain_score": 4,
            "mood": "calm",
            "energy": 3,
            "symptoms": ["cramps", "bloating"],
            "answered": ["d.bleeding", "d.pain", "d.symptoms"],
            "skipped": ["d.energy"],
            "today_local": "2026-10-20",
        },
    )

    # 1) JSON
    full_json = client.get(f"/v1/export/full.json?user_key={ukey}").json()
    assert full_json["schema_version"] == "1.1"
    assert len(full_json["daily_logs"]) == 1
    assert full_json["daily_logs"][0]["bleeding_level"] == "medium"
    assert "log_edits" in full_json
    assert "safety_events" in full_json

    # 2) CSV مع UTF-8 BOM
    csv_resp = client.get(f"/v1/export/daily-logs.csv?user_key={ukey}")
    assert csv_resp.status_code == 200
    assert csv_resp.content.startswith(b"\xef\xbb\xbf")
    csv_text = csv_resp.content.decode("utf-8-sig")
    assert "bleeding_level" in csv_text
    assert "cramps;bloating" in csv_text

    # 3) PDF شامل لتقرير الطبيبة مع ملخص السجل اليومي المهيكل
    pdf_resp = client.get(f"/v1/export/report.pdf?user_key={ukey}")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF")

    # 4) ملفات PWA (manifest.json + sw.js)
    assert client.get("/manifest.json").status_code == 200
    assert client.get("/sw.js").status_code == 200


# ---------------------------------------------------------------------------
# 10. الحذف المؤقّت (Soft Delete) + التراجع خلال 10 ثوانٍ (Undo)
# ---------------------------------------------------------------------------
def test_soft_delete_and_undo_restores_log_and_derived_cycle(client: TestClient) -> None:
    ukey = "undo-user"
    client.put(
        f"/v1/daily-logs?user_key={ukey}",
        json={
            "log_date": "2026-10-10",
            "bleeding_level": "heavy",
            "pain_score": 4,
            "symptoms": ["cramps"],
            "today_local": "2026-10-20",
        },
    )
    assert len(client.get(f"/v1/daily-logs?user_key={ukey}").json()) == 1
    assert len(client.get(f"/v1/cycles?user_key={ukey}").json()) == 1

    # حذف مؤقّت
    del_resp = client.delete(f"/v1/daily-logs/2026-10-10?user_key={ukey}")
    assert del_resp.status_code == 200
    assert del_resp.json()["undo_window_seconds"] == 10
    assert len(client.get(f"/v1/daily-logs?user_key={ukey}").json()) == 0
    assert len(client.get(f"/v1/cycles?user_key={ukey}").json()) == 0

    # تراجع (Undo)
    undo_resp = client.post(f"/v1/daily-logs/2026-10-10/undo?user_key={ukey}")
    assert undo_resp.status_code == 200
    assert undo_resp.json()["bleeding_level"] == "heavy"
    assert [s["symptom_id"] for s in undo_resp.json()["symptoms"]] == ["cramps"]
    assert len(client.get(f"/v1/daily-logs?user_key={ukey}").json()) == 1
    assert len(client.get(f"/v1/cycles?user_key={ukey}").json()) == 1
