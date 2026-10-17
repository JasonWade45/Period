from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

import app.main as main_mod
from app.schemas import CycleStat, UserContext
from app.services.emergency_filter import run_filter
from app.services.export_ar import ExportContext, build_doctor_report_sections
from app.services.library import filter_approved_articles
from app.services.llm import LLMClient, LLMProvider
from app.services.prediction import predict_next_cycle_window
from app.services.prompt_builder import scrub_conversation_history, scrub_pii
from app.services.rules_engine import compute_all_findings, compute_symptom_phase_correlations
from app.services.store import Store


def _make_client(tmp_path) -> TestClient:
    main_mod._store = Store(tmp_path / "stages_2_6.db")
    return TestClient(main_mod.app)


def test_stage2_range_prediction_and_correction(tmp_path) -> None:
    client = _make_client(tmp_path)
    ukey = "stage2_pred_user_1234"

    # أقل من 3 دورات -> بيانات غير كافية
    client.post(f"/v1/cycles?user_key={ukey}", json={"start_date": "2026-06-01", "length_days": 5})
    client.post(f"/v1/cycles?user_key={ukey}", json={"start_date": "2026-06-29", "length_days": 5})
    r_insuff = client.get(f"/v1/predictions?user_key={ukey}")
    assert r_insuff.status_code == 200
    d_insuff = r_insuff.json()
    assert d_insuff["status"] == "insufficient_data"
    assert d_insuff["confidence"] == "none"
    assert d_insuff["window_start"] is None

    # إضافة الدورة الثالثة -> تنبؤ بنطاق (وليس تاريخًا منفردًا)
    client.post(f"/v1/cycles?user_key={ukey}", json={"start_date": "2026-07-27", "length_days": 5})
    r_pred = client.get(f"/v1/predictions?user_key={ukey}")
    assert r_pred.status_code == 200
    d_pred = r_pred.json()
    assert d_pred["status"] == "ok"
    assert d_pred["confidence"] in ("high", "medium", "low")
    assert d_pred["window_start"] < d_pred["window_end"]
    assert d_pred["fertile_window_start"] < d_pred["fertile_window_end"]
    assert d_pred["basis"]["cycles_used"] == 3

    # زر «ليست دقيقة؟» يصحح تاريخ البداية ويعيد الحساب فورًا
    r_corr = client.post(
        f"/v1/predictions/correct?user_key={ukey}",
        json={"actual_start_date": "2026-07-29", "length_days": 6},
    )
    assert r_corr.status_code == 200
    d_corr = r_corr.json()
    assert d_corr["cycle"]["start_date"] == "2026-07-29"
    assert d_corr["prediction"]["window_start"] != d_pred["window_start"]


def test_stage2_daily_logs_and_copy_yesterday(tmp_path) -> None:
    client = _make_client(tmp_path)
    ukey = "stage2_daily_user_1234"

    # تسجيل يوم أمس (سجل يومي + عرضين)
    r_put = client.put(
        f"/v1/daily-logs?user_key={ukey}",
        json={
            "log_date": "2026-10-15",
            "flow": "medium",
            "mood": "هادئة",
            "sleep_hours": 7.5,
            "water_cups": 8,
            "note": "نوم جيد",
        },
    )
    assert r_put.status_code == 200
    client.post(
        f"/v1/symptoms?user_key={ukey}",
        json={"log_date": "2026-10-15", "symptom": "تقلصات", "severity": 3},
    )

    # نسخ أمس إلى اليوم بضغطة واحدة («اليوم يشبه أمس»)
    r_copy = client.post(f"/v1/daily-logs/copy-yesterday?user_key={ukey}&target_date=2026-10-16")
    assert r_copy.status_code == 200
    d_copy = r_copy.json()
    assert d_copy["copied_daily_log"] is True
    assert d_copy["copied_symptoms"] == 1

    logs = client.get(f"/v1/daily-logs?user_key={ukey}").json()
    assert any(l["log_date"] == "2026-10-16" and l["flow"] == "medium" for l in logs)
    syms = client.get(f"/v1/symptoms?user_key={ukey}").json()
    assert any(s["log_date"] == "2026-10-16" and s["symptom"] == "تقلصات" for s in syms)


def test_stage2_symptom_phase_correlation() -> None:
    ctx = UserContext(
        cycles_recorded=3,
        avg_cycle_days=28,
        last_cycles=[
            CycleStat(start_date="2026-08-01", length_days=5),
            CycleStat(start_date="2026-08-29", length_days=5),
            CycleStat(start_date="2026-09-26", length_days=5),
        ],
    )
    symptoms = [
        {"log_date": "2026-08-02", "symptom": "تقلصات", "severity": 4},
        {"log_date": "2026-08-30", "symptom": "تقلصات", "severity": 3},
        {"log_date": "2026-09-27", "symptom": "تقلصات", "severity": 4},
    ]
    corr = compute_symptom_phase_correlations(ctx, symptoms)
    assert len(corr) == 1
    assert corr[0].rule_code == "SYMPTOM_PHASE_CORRELATION"
    assert any("تقلصات" in ev for ev in corr[0].evidence)

    all_findings = compute_all_findings(ctx, symptoms)
    assert any(f.rule_code == "SYMPTOM_PHASE_CORRELATION" for f in all_findings)


def test_stage3_llm_protocol_pii_scrubbing_and_doctor_pdf() -> None:
    assert issubclass(LLMClient, LLMProvider)

    dirty = "تواصلي معي على sara.test@example.com أو رقم 01012345678 بخصوص الألم"
    clean = scrub_pii(dirty)
    assert "sara.test@example.com" not in clean
    assert "01012345678" not in clean

    history = [{"role": "user", "content": f"رسالة {i} من sara@example.com"} for i in range(10)]
    scrubbed_hist = scrub_conversation_history(history, max_turns=6)
    assert len(scrubbed_hist) == 6
    assert all("sara@example.com" not in turn["content"] for turn in scrubbed_hist)

    # فحص أقسام تقرير الطبيبة
    ectx = ExportContext.from_request("ar")
    cycles = [
        {"start_date": "2026-07-01", "length_days": 5},
        {"start_date": "2026-07-29", "length_days": 5},
        {"start_date": "2026-08-26", "length_days": 5},
    ]
    symptoms = [{"log_date": "2026-08-27", "symptom": "صداع", "severity": 4}]
    sections = build_doctor_report_sections(cycles, symptoms, [], ectx)
    assert len(sections) >= 4


def test_stage4_library_filtering_and_pregnancy_red_flags(tmp_path) -> None:
    raw = [
        {
            "id": "art_ok",
            "title": "فهم مراحل الدورة الشهرية",
            "topic": "الدورة",
            "life_mode": "all",
            "body": "محتوى تثقيفي مراجَع.",
            "status": "approved",
            "reviewed_by": "د. سارة أحمد",
            "reviewed_at": "2026-09-01",
            "source_name": "ACOG",
        },
        {
            "id": "art_draft",
            "title": "مسودة غير مراجعة",
            "topic": "الدورة",
            "life_mode": "all",
            "body": "مسودة",
            "status": "draft_unreviewed",
            "reviewed_by": "",
            "reviewed_at": None,
            "source_name": "ACOG",
        },
        {
            "id": "art_no_reviewer",
            "title": "مقال بلا اسم مراجعة",
            "topic": "الدورة",
            "life_mode": "all",
            "body": "نص",
            "status": "approved",
            "reviewed_by": "",
            "reviewed_at": "2026-09-01",
            "source_name": "WHO",
        },
    ]
    approved = filter_approved_articles(raw, allow_draft=False)
    assert [a["id"] for a in approved] == ["art_ok"]

    # فحص علامات خطر الحمل وما بعد الولادة قبل الـ LLM
    res_ar = run_filter("أشعر أن حركة الجنين قلت منذ الصباح", [])
    assert res_ar.triggered is True and res_ar.kind == "medical"
    res_en = run_filter("I noticed reduced fetal movement today", [])
    assert res_en.triggered is True and res_en.kind == "medical"


def test_stage5_partner_sharing_and_full_export(tmp_path) -> None:
    client = _make_client(tmp_path)
    ukey = "stage5_partner_user_1234"

    for dt in ("2026-06-01", "2026-06-29", "2026-07-27"):
        client.post(f"/v1/cycles?user_key={ukey}", json={"start_date": dt, "length_days": 5})
    client.post(
        f"/v1/symptoms?user_key={ukey}",
        json={"log_date": "2026-07-28", "symptom": "تقلصات", "severity": 4, "note": "ملاحظة خاصة جدا"},
    )

    # إنشاء رابط مشاركة محدود للشريك
    r_share = client.post(
        f"/v1/partner-share?user_key={ukey}",
        json={"partner_label": "الزوج", "scope": "next_window_only"},
    )
    assert r_share.status_code == 200
    share_data = r_share.json()
    token = share_data["share_token"]
    share_id = share_data["id"]

    # عرض الشريك يكشف نافذة التوقع فقط ولا يكشف أي ملاحظات أو أعراض
    r_view = client.get(f"/v1/partner-share/view/{token}")
    assert r_view.status_code == 200
    view_json = r_view.json()
    assert view_json["window_start"] is not None
    assert "symptoms" not in view_json
    assert "ملاحظة خاصة جدا" not in str(view_json)

    # إلغاء المشاركة فوريًا
    r_rev = client.delete(f"/v1/partner-share/{share_id}?user_key={ukey}")
    assert r_rev.status_code == 200
    r_view_after = client.get(f"/v1/partner-share/view/{token}")
    assert r_view_after.status_code == 404

    # التصدير الكامل JSON بضغطة واحدة
    r_full = client.get(f"/v1/export/full.json?user_key={ukey}")
    assert r_full.status_code == 200
    full_data = r_full.json()
    assert len(full_data["cycles"]) == 3
    assert len(full_data["symptoms"]) == 1
    assert full_data["prediction"]["status"] == "ok"


def test_stage6_security_and_privacy_docs_exist() -> None:
    sec_doc = Path("docs/security_review.md")
    priv_doc = Path("store/privacy_policy_ar.md")
    assert sec_doc.is_file() and sec_doc.stat().st_size > 500
    assert priv_doc.is_file() and priv_doc.stat().st_size > 500
