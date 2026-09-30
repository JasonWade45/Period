from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

CHAT_FIELDS = {
    "answer": str,
    "sources_used": list,
    "needs_doctor": bool,
    "emergency": bool,
    "crisis": bool,
    "missing_info": list,
}
SUMMARY_FIELDS = {
    "overview": str,
    "what_changed": str,
    "patterns": str,
    "medical_alerts": str,
    "what_this_does_not_mean": str,
    "questions_for_doctor": list,
    "sources_used": list,
}

# ---------------------------------------------------------------------
# فلتر الإسناد الممنوع: يمنع الإسناد للمستخدم أو النصيحة الموجهة،
# وليس ذكر الأمراض كموضوع تثقيفي عام.
# ---------------------------------------------------------------------
BANNED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"أنتِ?\s*(?:و)?عندك",
        r"يبدو\s*إن(?:ك|كي)?\s*(?:و)?عندك",
        r"تشخيصك\s*(?:هو|أن)",
        r"أنتِ?\s*(?:غالبا\s*)?عندها",
        r"أنتِ?\s*حامل|أنتي\s*حامل",
        r"مش\s*حامل|لستِ?\s*حامل",
        r"أنتِ?\s*بخير|إنتي\s*بخير",
        r"مفيش\s*حاجة\s*تقلق|مفيش\s*سبب\s*للقلق",
        r"مش\s*محتاجة\s*(?:دكتور|طبيبة)",
        r"خذي\b",
        r"انسبي\b",
        r"تناول[ي]?\s",
        r"جرعتك",
        r"جرعة\s*\d",
        r"\d+\s*ملغ",
        r"وقّفي\s*(?:عن\s*)?الدواء|توقفي\s*(?:عن\s*)?الدواء",
        r"أنتِ?\s*عندها\s+",
    ]
]

_FENCE_PATTERNS = [
    re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE),
    re.compile(r"\s*```\s*$"),
]


@dataclass
class ValidationResult:
    ok: bool
    data: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


def _strip_fences(text: str) -> str:
    text = text.strip()
    for pat in _FENCE_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = _strip_fences(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        obj = json.loads(cleaned[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON root is not an object")
    return obj


def _check_fields(obj: dict[str, Any], spec: dict[str, type]) -> list[str]:
    errors = []
    for name, typ in spec.items():
        if name not in obj:
            errors.append(f"missing field: {name}")
            continue
        value = obj[name]
        if typ is bool and not isinstance(value, bool):
            errors.append(f"field {name} must be bool")
        elif typ is str and not isinstance(value, str):
            errors.append(f"field {name} must be str")
        elif typ is list and not isinstance(value, list):
            errors.append(f"field {name} must be list")
    return errors


def check_banned_attribution(text: str) -> list[str]:
    errors = []
    for pat in BANNED_PATTERNS:
        if pat.search(text):
            errors.append(f"banned attribution: {pat.pattern}")
    return errors


def validate(raw: str, mode: str, allowed_source_ids: set[str]) -> ValidationResult:
    errors: list[str] = []
    try:
        obj = extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return ValidationResult(ok=False, errors=[f"invalid JSON: {exc}"])

    spec = CHAT_FIELDS if mode != "summary" else SUMMARY_FIELDS
    errors.extend(_check_fields(obj, spec))

    if mode == "summary":
        if isinstance(obj.get("what_this_does_not_mean"), str) and not obj["what_this_does_not_mean"].strip():
            errors.append("what_this_does_not_mean is empty")
        if isinstance(obj.get("questions_for_doctor"), list) and \
                not (3 <= len(obj["questions_for_doctor"]) <= 5):
            errors.append("questions_for_doctor must have 3-5 items")

    used = obj.get("sources_used", [])
    if isinstance(used, list):
        for sid in used:
            if sid not in allowed_source_ids:
                errors.append(f"unknown source id: {sid}")

    text_to_scan = " ".join(
        str(v) for v in obj.values() if isinstance(v, (str, list))
    )
    errors.extend(check_banned_attribution(text_to_scan))

    if errors:
        return ValidationResult(ok=False, errors=errors)
    return ValidationResult(ok=True, data=obj)
