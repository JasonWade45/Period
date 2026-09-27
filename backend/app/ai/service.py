"""AI orchestration: Rules Engine → minimal context → Grok → validation → fallback.

The app must keep working when AI is unavailable (PRD §56), so every path
here has a deterministic fallback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.grok_client import AIUnavailable, ChatClient
from app.ai.safety import SELF_HARM_MESSAGE, Violation, validate_ai_response
from app.services.health_data import UserHealthData
from app.services.medical_rules import Evaluation, load_content

log = logging.getLogger("cyclecare.ai")

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8")
SYSTEM_PROMPT_VERSION = "1.0"
MAX_HISTORY_MESSAGES = 10

FALLBACK = {
    "unavailable": {
        "en": "I can't generate an AI response right now.\n\nYour recorded medical findings are still available under Insights → Medical Alerts.",
        "ar": "لا يمكنني إنشاء رد بالذكاء الاصطناعي الآن.\n\nملاحظاتك الطبية المسجلة ما زالت متاحة في: الرؤى ← التنبيهات الطبية.",
    },
    "validation": {
        "en": "I couldn't produce a response that meets CycleCare's safety standards for this question.\n\nYour recorded findings are available under Insights → Medical Alerts. For questions about a possible condition, a healthcare professional is the right person to ask.",
        "ar": "لم أتمكن من تقديم رد يلتزم بمعايير السلامة في CycleCare لهذا السؤال.\n\nملاحظاتك المسجلة متاحة في: الرؤى ← التنبيهات الطبية. وللأسئلة المتعلقة بحالة صحية محتملة، مقدّم الرعاية الصحية هو الشخص المناسب.",
    },
}

LANGUAGE_INSTRUCTION = {
    "ar": "Respond in Arabic (clear Modern Standard Arabic; simple wording). Keep safety instructions unambiguous.",
    "en": "Respond in English.",
}

SUMMARY_INSTRUCTION = {
    "en": (
        "Write a summary of the user's recorded cycle data using exactly these sections as short headings: "
        "Overview; What changed; Patterns noticed; Medical alerts; What this does NOT mean; What to discuss with a doctor. "
        "Use only the supplied context. Keep it under 250 words."
    ),
    "ar": (
        "اكتب ملخصًا لبيانات الدورة المسجلة للمستخدمة باستخدام هذه الأقسام كعناوين قصيرة بالترتيب: "
        "نظرة عامة؛ ما الذي تغيّر؛ الأنماط الملحوظة؛ التنبيهات الطبية؛ ما الذي لا يعنيه ذلك؛ ما يمكن مناقشته مع الطبيب. "
        "استخدم فقط البيانات المرفقة. لا تتجاوز 250 كلمة."
    ),
}


@dataclass
class AIResult:
    text: str
    model: str | None
    ai_generated: bool
    fallback_reason: str | None = None  # unavailable | validation | emergency | self_harm
    violations: list[str] = field(default_factory=list)


def _context_message(context: dict, language: str) -> dict[str, str]:
    return {
        "role": "system",
        "content": "CycleCare server-supplied user context (JSON). This is the only user data you may use:\n"
        + json.dumps(context, ensure_ascii=False, default=str)
        + "\n\n"
        + LANGUAGE_INSTRUCTION[language],
    }


def _run(client: ChatClient, messages: list[dict[str, str]], language: str, pregnancy_confirmed: bool) -> AIResult:
    try:
        text = client.complete(messages)
    except AIUnavailable as exc:
        log.info("AI unavailable: %s", exc)
        return AIResult(FALLBACK["unavailable"][language], None, False, "unavailable")

    violations = validate_ai_response(text, pregnancy_confirmed=pregnancy_confirmed)
    if not violations:
        return AIResult(text, client.model, True)

    # One regeneration attempt with explicit corrective instruction.
    log.warning("AI response failed validation: %s", [v.kind for v in violations])
    retry = [
        *messages,
        {"role": "assistant", "content": text},
        {
            "role": "system",
            "content": "Your previous reply violated CycleCare safety policy ("
            + ", ".join(sorted({v.kind for v in violations}))
            + "). Rewrite it: do not diagnose, do not state or deny pregnancy/fertility, do not reassure the user that no care is needed, "
            "do not recommend prescription medication, and never dismiss a Rules Engine alert. Reply only with the corrected answer.",
        },
    ]
    try:
        text2 = client.complete(retry)
    except AIUnavailable:
        return AIResult(FALLBACK["validation"][language], None, False, "validation", [v.kind for v in violations])
    violations2 = validate_ai_response(text2, pregnancy_confirmed=pregnancy_confirmed)
    if not violations2:
        return AIResult(text2, client.model, True, violations=[v.kind for v in violations])
    return AIResult(FALLBACK["validation"][language], None, False, "validation", [v.kind for v in violations + violations2])


def chat_reply(
    client: ChatClient,
    *,
    context: dict,
    history: list[dict[str, str]],
    user_message: str,
    language: str,
    pregnancy_confirmed: bool,
) -> AIResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        _context_message(context, language),
        *history[-MAX_HISTORY_MESSAGES:],
        {"role": "user", "content": user_message},
    ]
    return _run(client, messages, language, pregnancy_confirmed)


def summary_reply(client: ChatClient, *, context: dict, language: str, pregnancy_confirmed: bool) -> AIResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        _context_message(context, language),
        {"role": "user", "content": SUMMARY_INSTRUCTION[language]},
    ]
    return _run(client, messages, language, pregnancy_confirmed)


def emergency_message(language: str, evaluation: Evaluation | None = None, category: str = "physical") -> str:
    if category == "self_harm":
        return SELF_HARM_MESSAGE[language]
    if evaluation is not None and evaluation.alert is not None and evaluation.alert.severity.rank >= 3:
        a = evaluation.alert
        return f"{a.title}\n\n{a.summary}\n\n{a.recommended_action}"
    e = load_content()["emergency"]["generic"][language]
    return f"{e['title']}\n\n{e['body']}"


# ---------------------------------------------------------------------------
# Deterministic (non-AI) outputs
# ---------------------------------------------------------------------------


def questions_for_doctor(evaluation: Evaluation, data: UserHealthData, language: str) -> list[str]:
    q = load_content()["questions_for_doctor"]
    out = [q[f.rule_code][language] for f in evaluation.findings if f.rule_code in q and f.severity.rank >= 1]
    contraception = data.profile.contraception_type if data.profile else None
    if contraception and contraception not in ("none", "prefer_not_to_say") and evaluation.findings:
        out.append(q["contraception"][language])
    if not out:
        out.append(q["general"][language])
    return list(dict.fromkeys(out))


def deterministic_summary(data: UserHealthData, evaluation: Evaluation, cycles: int, language: str) -> str:
    """Plain summary built without AI — used when Grok is unavailable."""
    lengths = data.lengths[-cycles:]
    ar = language == "ar"
    sep = "، " if ar else ", "
    lines: list[str] = []
    if lengths:
        if ar:
            lines.append(
                f"نظرة عامة: آخر {len(lengths)} دورات تراوحت بين {min(lengths)} و{max(lengths)} يومًا ({sep.join(map(str, lengths))})."
            )
        else:
            lines.append(
                f"Overview: your last {len(lengths)} cycles ranged from {min(lengths)} to {max(lengths)} days ({sep.join(map(str, lengths))})."
            )
    else:
        lines.append("نظرة عامة: لا توجد دورات مكتملة مسجلة بعد." if ar else "Overview: no completed cycles recorded yet.")
    if evaluation.findings:
        head = "التنبيهات الطبية:" if ar else "Medical alerts:"
        lines.append(head + " " + sep.join(f.title for f in evaluation.findings))
        lines.append(load_content()["disclaimer"][language])
    else:
        lines.append("التنبيهات الطبية: لا توجد." if ar else "Medical alerts: none.")
    return "\n".join(lines)


__all__ = [
    "AIResult",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_VERSION",
    "Violation",
    "chat_reply",
    "deterministic_summary",
    "emergency_message",
    "questions_for_doctor",
    "summary_reply",
]
