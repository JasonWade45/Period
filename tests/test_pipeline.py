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


def test_fallback_when_llm_fails(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_llm", _BoomLLM())
    resp = main._run_pipeline(_req("إيه أعراض ما قبل الدورة؟"))
    assert "تعذّر" in resp.answer
    assert resp.emergency is False
    assert resp.prompt_version


def test_summary_fallback_when_llm_fails(monkeypatch):
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
