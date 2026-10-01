"""اختبارات نقاط الـ API عبر HTTP — خاصة سلوك الرد الآمن بدون مفتاح موديل.

مسار المحادثة الوحيد هو `/api/v1/ai/*` (أنظر tests/conftest.py: العميل يأتي
بـpipeline بلا مفتاح موديل وبمُسترجع اختباري — بلا شبكة وبلا تدقيق حقيقي).
"""
from __future__ import annotations

from app.i18n import get_translator

CTX = {
    "age": 27,
    "cycles_recorded": 5,
    "avg_cycle_days": 41,
    "last_cycles": [
        {"start_date": "2026-06-05", "length_days": 5},
        {"start_date": "2026-07-13", "length_days": 4},
        {"start_date": "2026-08-23", "length_days": 6},
    ],
}


def test_health_reports_runtime_flags(client) -> None:
    import app.main as main
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["prompt_version"] == main.settings.prompt_version
    assert body["chunks_loaded"] > 0
    # أعلام فقط — لا مفاتيح ولا أرقام
    assert set(body) >= {
        "llm_configured", "emergency_number_is_default", "crisis_line_configured",
    }
    assert "groq_api_key" not in {k.lower() for k in body}


def test_chat_without_model_key_returns_safe_fallback(client) -> None:
    """غياب المفتاح ليس خطأ خادم: ترجع إجابة آمنة بحالة 200."""
    r = client.post("/api/v1/ai/chat",
                    json={"message": "إيه أعراض ما قبل الدورة؟", "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == get_translator().t("answer.not_configured", "ar")
    assert body["emergency"] is False
    assert body["prompt_version"]
    assert body["rule_codes"]


def test_summary_without_model_key_returns_safe_fallback(client) -> None:
    r = client.post("/api/v1/ai/summary",
                    json={"message": "ملخص لدوراتي", "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["overview"] == get_translator().t("answer.not_configured", "ar")
    assert body["what_this_does_not_mean"]


def test_emergency_path_works_without_model_key(client) -> None:
    """الأهم: طبقة الطوارئ لا تعتمد على الموديل ولا على مفتاحه."""
    r = client.post("/api/v1/ai/chat",
                    json={"message": "بنزف كل ساعة وبرمي جلطات كبيرة",
                          "user_context": CTX})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"] is True
    assert body["needs_doctor"] is True
    assert "رعاية طبية عاجلة" in body["answer"]
    from app.config import settings
    assert settings.emergency_number in body["answer"]


def test_crisis_path_reports_crisis(client) -> None:
    r = client.post("/api/v1/ai/chat",
                    json={"message": "مش عايزة أعيش", "user_context": CTX})
    body = r.json()
    assert body["crisis"] is True
    assert body["emergency"] is True


def test_empty_message_is_rejected(client) -> None:
    r = client.post("/api/v1/ai/chat", json={"message": "   ", "user_context": CTX})
    assert r.status_code == 422


def test_frontend_is_served(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fallback_does_not_claim_doctor_review_for_monitor_findings(client) -> None:
    """شارة «يستحق مراجعة طبية» لا تظهر لنتيجة MONITOR.

    هذه الحالة ظهرت في الواجهة فعليًا: المستخدمة بلا دورات مسجّلة تحصل على
    INSUFFICIENT_DATA (MONITOR)، وكانت المقارنة الأبجدية تجعل needs_doctor=True.
    """
    r = client.post("/api/v1/ai/chat", json={"message": "إيه أعراض ما قبل الدورة؟",
                                             "user_context": {"cycles_recorded": 0}})
    body = r.json()
    assert body["rule_codes"] == ["INSUFFICIENT_DATA"]
    assert body["needs_doctor"] is False
    assert body["emergency"] is False


def test_fallback_does_claim_doctor_review_for_medical_review_findings(client) -> None:
    """مقابل ذلك: نتيجة MEDICAL_REVIEW (متوسط دورة ≥ 60 يومًا) تستدعي التنبيه."""
    r = client.post("/api/v1/ai/chat", json={"message": "إيه أعراض ما قبل الدورة؟",
                                             "user_context": {"cycles_recorded": 5,
                                                              "avg_cycle_days": 70}})
    body = r.json()
    assert body["rule_codes"] == ["LONG_CYCLE"]
    assert body["needs_doctor"] is True


def test_fallback_for_short_cycle_monitor_does_not_claim_doctor_review(client) -> None:
    """SHORT_CYCLE = MONITOR أيضًا، فلا تنبيه مبالغًا فيه."""
    r = client.post("/api/v1/ai/chat", json={"message": "سؤال", "user_context": {
        "cycles_recorded": 4, "avg_cycle_days": 18,
        "cycle_gaps": [18, 18],
        "last_cycles": [{"start_date": "2026-07-01", "length_days": 4},
                        {"start_date": "2026-07-19", "length_days": 5}]}})
    body = r.json()
    assert body["rule_codes"] == ["SHORT_CYCLE"]
    assert body["needs_doctor"] is False
