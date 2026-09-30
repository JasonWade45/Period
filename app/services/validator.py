from __future__ import annotations

import json
import re
import unicodedata
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
#
# تُطبَّق الأنماط على نص مُوحَّد (انظر _normalize_ar): بلا تشكيل، وبألف/ياء
# موحّدتين. لذلك تُكتب الأنماط هنا بالهمزات الموحّدة (ا/ي) لا (أ/إ/ى/ئ).
# ---------------------------------------------------------------------
BANNED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # (1) إسناد حالة أو مرض للمستخدمة — الصيغ المباشرة
        r"(?:انت|انتي|انتم)\s*(?:غالبا\s*)?(?:و)?(?:عندك|عندها|حامل|حامله|مريضه|مريض|"
        r"مصابه|مصاب|بتعاني|تعاني|جالك|معاك)",
        # (2) صيغ «يبدو/واضح/باين إنك …» — إسناد غير مباشر (كان يتجاوز الفلتر سابقًا)
        r"(?:يبدو|واضح|باين|شكلك|شكلها|مظهرك|غالبا)\s*(?:انك|انكي|انكو|انها|انه)\b",
        r"(?:انك|انكي|انكو)\s*(?:حامل|حامله|مريضه|مريض|مصابه|مصاب|عندك|بتعاني|تعاني)",
        # (3) «عندك» + حالة مسمّاة، بأي صياغة سابقة
        r"عندك\s*(?:تكيس|تكيسات|انيميا|بطانة|ورم|اليف|ليف|التهاب|عدوي|حمل|عقم|"
        r"متلازمه|خلل|اضطراب|مشكله|حاله)",
        r"تشخيصك\s*(?:هو|ان)",
        # (4) نفي الحمل أو تأكيده
        r"(?:مش|لست|لا\s*انت)\s*(?:حامل|حامله)",
        # (5) تطمين أو نفي قاطع للخطر
        r"(?:متقلقيش|تقلقيش|لا\s*تقلق|متخافيش|تخافيش|اطمني|اطمن|متخاف)",
        r"(?:مافيش|مفيش|ما\s*فيش)\s*(?:حاجه|مشكله|سبب|داعي|خطر|قلق)",
        r"مش\s*(?:مقلق|خطير|مشكله|حاجه\s*تقلق|محتاجه)",
        r"(?:ده|دا|هذا|دي|هي)\s*(?:طبيعي|عادي|مقلق|خطير)",
        r"(?:انت|انتي)\s*بخير",
        # (6) وصف دواء أو جرعة أو إيقافه
        r"(?:خذي|خدي|تاخدي|تاخذي|تناولي|تناول|لازم\s*(?:تاخدي|تاخذي|تاخد)|"
        r"ابديي|ابداي|استعملي|استخدمي|جربي)\s*"
        r"(?:حبوب|حبه|حباية|دوا|دواء|مسكن|مضاد|مكمل|فيتامين|هرمون|برشام|لبوس|"
        r"تحاميل|اقراص|قرص|جرعه|مليغرام|مليجرام|مجم|ملغ|مضاد\s*حيوي|"
        r"حقنه|امبول|ابره|كبسوله|قطره|مرهم|كريم|تحميله|شراب|لبوسه|"
        r"ايبوبروفين|باراسيتامول|ترانيكساميك|ميتفورمين|اسبرين|ibuprofen|"
        r"paracetamol|tranexamic|metformin|aspirin|"
        r"لولب|حبوب\s*منع\s*الحمل|ssri)",
        # «خذي/خدي» المجرّدة: تمنع أي توجيه لشيء، مع استثناء صريح للنصائح
        # الحميدة (وقت/نفس/راحة…) التي قد ترد في تعليم عام مشروع.
        r"(?:خذي|خدي)\b(?!\s*(?:وقت|وقتك|نفس|نفسا|راحه|راحتك|بالك|شويه|كفايتك|"
        r"وضعيه|دقيقه|لحظه|خطوه|صوره))",
        r"انسبي\b",
        r"جرعتك|جرعه\s*\d",
        r"\d+\s*(?:ملغ|مجم|ملجم|مليغرام|مليجرام|mg|مج)\b",
        r"(?:وقفي|ايقفي|اوقفي|توقفي|بلاش|ابطلي|اتركي)\s*(?:عن\s*)?"
        r"(?:الدواء|العلاج|الحبوب|الهرمون|المسكن)",
        r"(?:بلاش|مش)\s*(?:تاخدي|تاخذي|خدي|خذي|توقفي)",
        # نصيحة دوائية موجّهة بالإنجليزية (المصادر تُرسل بالإنجليزية أحيانًا)
        r"\b(?:you should|you could|i suggest|i recommend|make sure to)\s+"
        r"(?:take|use|try|start|stop)\b",
        r"\b(?:take|use|try|start|stop)\s+(?:the\s+)?"
        r"(?:ibuprofen|paracetamol|acetaminophen|tranexamic|metformin|aspirin|"
        r"ssri|antidepressant|hormonal\s+(?:pill|iud|contraceptive)|"
        r"ibuprofen\s+\d)",
        r"\byour\s+dose\b|\bdosage\b|\b\d+\s*(?:mg|mcg|g)\b",
    ]
]

# توحيد النص العربي قبل المطابقة: التشكيل وهمزات الألف والياء تختلف
# بين الفصحى والعامية المصرية، والتوحيد يمنع تجاوز الفلتر بفرق حرف واحد.
_AR_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_AR_ALEF = re.compile(r"[أإآٱ]")
_AR_YA = re.compile(r"[ىئ]")
_AR_TA_MARBUTA = re.compile(r"[\u0629]")

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


def _normalize_ar(text: str) -> str:
    """توحيد الهمزات والتشكيل — يجعل الأنماط محصّنة ضد فروق الإملاء والدلالة."""
    text = unicodedata.normalize("NFKC", text)
    text = _AR_DIACRITICS.sub("", text)
    text = _AR_ALEF.sub("ا", text)
    text = _AR_YA.sub("ي", text)
    text = _AR_TA_MARBUTA.sub("ه", text)
    return text.lower()


def check_banned_attribution(text: str) -> list[str]:
    normalized = _normalize_ar(text)
    errors = []
    for pat in BANNED_PATTERNS:
        if pat.search(normalized):
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
