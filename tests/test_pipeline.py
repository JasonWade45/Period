"""سلوك خط الأنابيب عند فشل الموديل/التدقيق — بلا HTTP وبلا شبكة."""
from __future__ import annotations

import pytest

from app.i18n import get_translator
from app.schemas import AiChatRequest, UserContext
from tests.pipeline_helpers import build_pipeline


class _BoomLLM:
    def complete(self, *args, **kwargs):
        raise RuntimeError("simulated LLM outage")


def _req(message: str) -> AiChatRequest:
    return AiChatRequest(
        message=message,
        user_context=UserContext(cycles_recorded=5, avg_cycle_days=41),
    )


def test_fallback_when_llm_fails():
    pipe = build_pipeline(llm=_BoomLLM())
    resp = pipe.chat(_req("إيه أعراض ما قبل الدورة؟"))
    assert "تعذّر" in resp.answer
    assert resp.emergency is False
    assert resp.prompt_version


def test_summary_fallback_when_llm_fails():
    pipe = build_pipeline(llm=_BoomLLM())
    resp = pipe.summary(_req("ملخص لدوراتي"))
    assert resp.overview
    assert resp.what_this_does_not_mean


def test_emergency_never_calls_llm():
    pipe = build_pipeline(llm=_BoomLLM())
    resp = pipe.chat(_req("بنزف كل ساعة وبرمي جلطات"))
    assert resp.emergency is True
    assert "رعاية طبية عاجلة" in resp.answer
    assert "123" in resp.answer


def test_crisis_never_calls_llm():
    pipe = build_pipeline(llm=_BoomLLM())
    resp = pipe.chat(_req("مش عايزة أعيش"))
    assert resp.crisis is True
    assert resp.emergency is True


def test_missing_model_key_degrades_instead_of_erroring():
    """لا مفتاح → إجابة احتياطية بحالة 200، وليس 503."""
    pipe = build_pipeline(llm=None)
    resp = pipe.chat(_req("إيه أعراض ما قبل الدورة؟"))
    assert resp.answer == get_translator().t("answer.not_configured", "ar")
    assert resp.prompt_version
    assert resp.rule_codes


def test_audit_failure_does_not_break_the_response():
    """قرص ممتلئ أو نظام للقراءة فقط لا يجوز أن يمنع ردًّا آمنًا."""
    def _boom(entry, extra=None):
        raise OSError("read-only file system")

    pipe = build_pipeline(llm=None, audit_writer=_boom)
    resp = pipe.chat(_req("إيه أعراض ما قبل الدورة؟"))
    assert resp.answer == get_translator().t("answer.not_configured", "ar")


def test_audit_records_why_it_fell_back():
    """سبب الرد الاحتياطي يجب أن يظهر في سجل التدقيق للتشغيل والمراجعة."""
    captured: list = []
    pipe = build_pipeline(llm=_BoomLLM(), audit_writer=lambda e, x=None: captured.append(e))

    pipe.chat(_req("إيه أعراض ما قبل الدورة؟"))

    assert len(captured) == 1
    entry = captured[0]
    assert entry.fallback is True
    assert entry.used_model is False
    assert entry.llm_error.startswith("RuntimeError")


def test_emergency_audit_entry_has_no_model_error():
    captured: list = []
    pipe = build_pipeline(llm=_BoomLLM(), audit_writer=lambda e, x=None: captured.append(e))

    pipe.chat(_req("بنزف كل ساعة وبرمي جلطات كبيرة"))

    assert captured[0].emergency is True
    assert captured[0].llm_error == ""
