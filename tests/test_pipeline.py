import pytest

from app.main import _run_pipeline
from app.schemas import ChatRequest, UserContext


@pytest.fixture(autouse=True)
def _disable_audit(monkeypatch):
    import app.services.audit as audit
    monkeypatch.setattr(audit, "write", lambda entry: None)


class _BoomLLM:
    def complete(self, *args, **kwargs):
        raise RuntimeError("simulated LLM outage")


def _req(message: str, mode: str = "chat") -> ChatRequest:
    return ChatRequest(
        message=message,
        mode=mode,
        user_context=UserContext(
            cycles_recorded=5,
            avg_cycle_days=41,
        ),
    )


def test_fallback_when_llm_fails(monkeypatch, test_rag):
    import app.main as main
    monkeypatch.setattr(main, "_llm", _BoomLLM())
    resp = main._run_pipeline(_req("إيه أعراض ما قبل الدورة؟"))
    assert "تعذّر" in resp.answer
    assert resp.emergency is False
    assert resp.prompt_version


def test_summary_fallback_when_llm_fails(monkeypatch, test_rag):
    import app.main as main
    monkeypatch.setattr(main, "_llm", _BoomLLM())
    resp = main._run_pipeline(_req("ملخص لدوراتي", mode="summary"))
    assert resp.overview
    assert resp.what_this_does_not_mean


def test_emergency_never_calls_llm(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_llm", _BoomLLM())
    resp = main._run_pipeline(_req("بنزف كل ساعة وبرمي جلطات"))
    assert resp.emergency is True
    assert "رعاية طبية عاجلة" in resp.answer
    assert "123" in resp.answer


def test_crisis_never_calls_llm(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_llm", _BoomLLM())
    resp = main._run_pipeline(_req("مش عايزة أعيش"))
    assert resp.crisis is True
    assert resp.emergency is True


def test_missing_model_key_degrades_instead_of_erroring(monkeypatch, test_rag):
    """لا مفتاح → إجابة احتياطية بحالة 200، وليس 503."""
    import app.main as main
    monkeypatch.setattr(main, "_llm", None)
    resp = main._run_pipeline(_req("إيه أعراض ما قبل الدورة؟"))
    assert resp.answer == main.NOT_CONFIGURED_ANSWER
    assert resp.prompt_version
    assert resp.rule_codes


def test_audit_failure_does_not_break_the_response(monkeypatch, test_rag):
    """قرص ممتلئ أو نظام للقراءة فقط لا يجوز أن يمنع ردًّا آمنًا."""
    import app.main as main
    import app.services.audit as audit

    def _boom(entry):
        raise OSError("read-only file system")

    monkeypatch.setattr(audit, "write", _boom)
    monkeypatch.setattr(main, "_llm", None)
    resp = main._run_pipeline(_req("إيه أعراض ما قبل الدورة؟"))
    assert resp.answer == main.NOT_CONFIGURED_ANSWER


def test_audit_records_why_it_fell_back(monkeypatch, test_rag):
    """سبب الرد الاحتياطي يجب أن يظهر في سجل التدقيق للتشغيل والمراجعة."""
    import app.main as main
    import app.services.audit as audit

    captured = []
    monkeypatch.setattr(audit, "write", captured.append)
    monkeypatch.setattr(main, "_llm", _BoomLLM())

    main._run_pipeline(_req("إيه أعراض ما قبل الدورة؟"))

    assert len(captured) == 1
    entry = captured[0]
    assert entry.fallback is True
    assert entry.used_model is False
    assert entry.llm_error.startswith("RuntimeError")


def test_emergency_audit_entry_has_no_model_error(monkeypatch):
    import app.main as main
    import app.services.audit as audit

    captured = []
    monkeypatch.setattr(audit, "write", captured.append)
    monkeypatch.setattr(main, "_llm", _BoomLLM())

    main._run_pipeline(_req("بنزف كل ساعة وبرمي جلطات كبيرة"))

    assert captured[0].emergency is True
    assert captured[0].llm_error == ""
