"""Build the minimal, controlled context object sent to Grok (PRD §30, §60).

Never included: email, password hash, tokens, display name, exact date of birth
(only age), free-text notes, IP/device data, medication details.
Only the topics relevant to the question are included ("minimum necessary data").
"""

from __future__ import annotations

import re
from typing import Any

from app.services.health_data import UserHealthData
from app.services.medical_rules import Evaluation
from app.services.prediction_engine import Prediction

TOPIC_KEYWORDS: dict[str, str] = {
    "cycle": r"cycle|period|late|early|length|irregular|regular|predict|next|missed|دور[ةه]|الدور[ةه]|اتأخر|تأخر|متأخر|منتظم|طول|ميعاد|موعد|الجاية|القادمة|غابت|انقطع",
    "bleeding": r"bleed|flow|heavy|clot|pad|tampon|spotting|blood|نزيف|نزف|دم|غزير|فوط|جلط|تبقيع|تنقيط",
    "symptoms": r"pain|cramp|symptom|headache|migraine|mood|bloat|tired|fatigue|acne|ألم|وجع|مغص|أعراض|اعراض|صداع|مزاج|انتفاخ|تعب|إرهاق|حبوب",
    "all": r"doctor|gynae|gyneco|summar|overview|report|question|دكتور|دكتورة|طبيب|طبيبة|لخص|ملخص|تقرير|أسأل|اسأل|سؤال",
}

RECENT_CYCLES = 6


def detect_topics(message: str | None) -> set[str]:
    if not message:
        return {"cycle", "bleeding", "symptoms"}
    text = message.lower()
    topics = {t for t, rx in TOPIC_KEYWORDS.items() if re.search(rx, text)}
    if "all" in topics or not topics:
        # Unknown / general question → cycle overview + findings only (not every symptom).
        return {"cycle", "bleeding", "symptoms"} if "all" in topics else {"cycle"}
    return topics


def build_ai_context(
    data: UserHealthData,
    evaluation: Evaluation,
    prediction: Prediction | None = None,
    *,
    message: str | None = None,
    cycles: int = RECENT_CYCLES,
    topics: set[str] | None = None,
) -> dict[str, Any]:
    topics = topics or detect_topics(message)
    profile = data.profile
    finding_codes = {f.rule_code for f in evaluation.findings}

    user_context: dict[str, Any] = {"age": data.age}
    if profile:
        user_context["contraception"] = profile.contraception_type or "not_provided"
        if profile.contraception_started_on and "cycle" in topics:
            user_context["contraception_started_on"] = profile.contraception_started_on.isoformat()
        if "cycle" in topics or finding_codes & {"MR-007", "MR-008", "MR-006"}:
            user_context["breastfeeding"] = profile.breastfeeding
            user_context["pregnancy_possibility"] = profile.pregnancy_possibility or "not_provided"
        known = {
            "pcos_or_pmos": profile.known_pcos,
            "endometriosis": profile.known_endometriosis,
            "thyroid_condition": profile.known_thyroid_condition,
            "bleeding_disorder": profile.known_bleeding_disorder,
        }
        reported = [k for k, v in known.items() if v]
        if reported:
            user_context["user_reported_diagnoses_from_professionals"] = reported
    else:
        user_context["health_profile"] = "not_provided"

    stats = data.cycle_stats()
    history = data.history()[-(cycles + 1) :]
    recent: list[dict[str, Any]] = []
    for row in history:
        item: dict[str, Any] = {
            "start": row["start"],
            "status": "current_cycle" if row["is_current"] else "completed",
            "cycle_length_days": row["cycle_length"],
            "period_days": row["period_days"],
        }
        if "bleeding" in topics:
            item["peak_flow"] = row["peak_flow"]
        if "symptoms" in topics:
            item["max_pain"] = row["max_pain"]
        recent.append(item)

    ctx: dict[str, Any] = {
        "today": data.today.isoformat(),
        "data_labels": {
            "recent_cycles": "confirmed user-recorded history",
            "prediction": "estimate only, not guaranteed",
            "medical_findings": "deterministic CycleCare rules engine output (authoritative)",
        },
        "user_context": user_context,
        "cycle_summary": {
            "cycles_analyzed": stats.cycle_count,
            "average_length": stats.mean,
            "median_length": stats.median,
            "min_length": stats.min,
            "max_length": stats.max,
            "std_dev": stats.std_dev,
            "pattern": stats.pattern,
            "variability": stats.variability,
            "current_cycle_day": (data.today - data.views[-1].start).days + 1 if data.views else None,
        },
        "recent_cycles": recent,
        "medical_findings": [
            {
                "rule_code": f.rule_code,
                "severity": f.severity.value,
                "title": f.title,
                "description": f.summary,
                "recommended_action": f.recommended_action,
                "evidence": f.evidence,
            }
            for f in evaluation.findings
        ],
        "overall_severity": evaluation.overall_severity.value,
        "missing_data": evaluation.data_gaps,
        "included_topics": sorted(topics),
    }
    if "bleeding" in topics:
        ctx["period_summary"] = data.period_stats()
    if "symptoms" in topics:
        freq = data.symptom_frequency()
        ctx["symptom_summary"] = {
            "cycles_counted": freq["cycles_counted"],
            "top_symptoms": [f"{s['symptom']}: {s['label']} cycles" for s in freq["symptoms"][:8]],
            "severe_pain_cycles": freq["severe_pain_cycles"],
        }
    if "cycle" in topics and prediction is not None:
        pd = prediction.to_dict("en")
        ctx["prediction"] = {
            k: pd[k] for k in ("available", "estimated_start", "window_start", "window_end", "confidence", "status", "days_late", "reason")
        }
    return ctx
