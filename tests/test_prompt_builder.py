from pathlib import Path

from app.config import settings
from app.schemas import CycleStat, Finding, SourceChunk, UserContext
from app.services.prompt_builder import PromptBuilder


def test_prompt_has_no_unfilled_variables():
    builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
    ctx = UserContext(cycles_recorded=4, avg_cycle_days=30,
                      last_cycles=[CycleStat(start_date="2026-08-01", length_days=30)])
    findings = [Finding(rule_code="NO_ALERT_PATTERN", severity="NORMAL",
                        title="t", evidence=["e"])]
    sources = [SourceChunk(id="a", source_name="NHS", section="s",
                           reviewed_at="2025-01-01", text="t")]
    out = builder.build(
        mode="chat", user_context=ctx, findings=findings, sources=sources,
        rules_glossary={"NO_ALERT_PATTERN": "شرح"},
        current_date="2026-09-30", emergency_number="123", crisis_line="",
    )
    from app.services.prompt_builder import _VAR_PATTERN
    assert _VAR_PATTERN.findall(out) == []
    assert "2026-09-30" in out
    assert "NO_ALERT_PATTERN" in out
    assert "v1.1" in builder.version


def test_unknown_mode_defaults_to_chat():
    builder = PromptBuilder(settings.prompt_path, settings.prompt_version)
    out = builder.build(
        mode="weird", user_context=UserContext(), findings=[],
        sources=[], rules_glossary={}, current_date="2026-09-30",
        emergency_number="123", crisis_line="",
    )
    assert "MODE chat" in out or "chat" in out
