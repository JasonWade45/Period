from __future__ import annotations

import json
import re
from pathlib import Path

from ..schemas import Finding, SourceChunk, UserContext

_VAR_PATTERN = re.compile(r"\{\{\s*([A-Z_]+)\s*\}\}")

ALLOWED_MODES = {"chat", "summary"}


class PromptBuilder:
    def __init__(self, prompt_path: Path, prompt_version: str):
        self.version = prompt_version
        self.template = Path(prompt_path).read_text(encoding="utf-8")

    def _render(self, variables: dict[str, str]) -> str:
        def repl(match: re.Match) -> str:
            key = match.group(1)
            if key not in variables:
                raise KeyError(f"prompt variable not filled: {key}")
            return variables[key]

        rendered = _VAR_PATTERN.sub(repl, self.template)
        leftover = _VAR_PATTERN.findall(rendered)
        if leftover:
            raise ValueError(f"unfilled variables remain: {leftover}")
        return rendered

    def build(
        self,
        *,
        mode: str,
        user_context: UserContext,
        findings: list[Finding],
        sources: list[SourceChunk],
        rules_glossary: dict[str, str],
        current_date: str,
        emergency_number: str,
        crisis_line: str,
    ) -> str:
        if mode not in ALLOWED_MODES:
            mode = "chat"

        # keywords مخصصة للاسترجاع فقط — لا تُرسل للموديل لتوفير التوكنات
        sources_payload = json.dumps(
            [json.loads(c.model_dump_json(exclude={"keywords"})) for c in sources],
            ensure_ascii=False,
        )
        findings_payload = json.dumps(
            [json.loads(f.model_dump_json()) for f in findings],
            ensure_ascii=False,
        )
        glossary_used = {
            f.rule_code: rules_glossary[f.rule_code]
            for f in findings
            if f.rule_code in rules_glossary
        }

        return self._render({
            "MODE": mode,
            "USER_CONTEXT": user_context.compact_json() or "{}",
            "FINDINGS": findings_payload,
            "SOURCES": sources_payload,
            "RULE_GLOSSARY": json.dumps(glossary_used, ensure_ascii=False),
            "CURRENT_DATE": current_date,
            "EMERGENCY_NUMBER": emergency_number,
            "CRISIS_LINE": crisis_line or "غير متوفر حاليًا في بلدكِ",
        })
