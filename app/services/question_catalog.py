"""كتالوج الأسئلة السياقية وقائمة الأعراض المهيكلة (البنود 2، 3، 4 من مواصفة التتبّع).

المبادئ:
- لا توجد أي نصوص للمستخدمة مكتوبة داخل الكود؛ كل النصوص تُجلب من `locales/ar.json`
  و`locales/en.json` عبر `i18n_key`.
- الأسئلة سياقية: تظهر أسئلة النزيف (`b.*`) فقط في أيام النزيف، وأسئلة غير النزيف
  (`n.*`) في أيام غير النزيف، والأسئلة الحساسة مخفية افتراضيًا ما لم تفعّلها المستخدمة.
- الأسئلة 1–3 من الاستبيان الأولي تظهر أولًا، والباقي يتوزع على أول أسبوعين («سؤال اليوم»).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..i18n import get_translator, resolve_locale

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "question_catalog.json"

# فهرس صريح لمفاتيح الخيارات القصيرة لضمان التحقق الساكن في tools/check_i18n.py
STATIC_OPTION_I18N_KEYS: tuple[str, ...] = (
    "questions.b.pad_count.options.1",
    "questions.b.pad_count.options.2",
    "questions.b.pad_count.options.3",
    "questions.b.pad_count.options.4",
    "questions.b.pad_count.options.5",
    "questions.d.energy.options.1",
    "questions.d.energy.options.2",
    "questions.d.energy.options.3",
    "questions.d.energy.options.4",
    "questions.d.energy.options.5",
    "questions.d.mood.options.low",
    "questions.d.pain.options.0",
    "questions.d.pain.options.1",
    "questions.d.pain.options.2",
    "questions.d.pain.options.3",
    "questions.d.pain.options.4",
    "questions.d.pain.options.5",
    "questions.l.sleep_hours.options.5",
    "questions.l.sleep_hours.options.6",
    "questions.l.sleep_hours.options.7",
    "questions.l.sleep_hours.options.8",
    "questions.l.sleep_hours.options.9",
    "questions.l.stress.options.1",
    "questions.l.stress.options.2",
    "questions.l.stress.options.3",
    "questions.l.stress.options.4",
    "questions.l.stress.options.5",
    "questions.l.water_ml.options.500",
    "questions.n.discharge.options.dry",
    "questions.ob.cycle_len_variation.options.0_2",
    "questions.ob.cycle_len_variation.options.3_7",
    "questions.ob.family_bleeding.options.no",
    "questions.ob.family_bleeding.options.yes",
    "questions.ob.goal.options.ttc",
    "questions.ob.pregnancy_status.options.no",
)


def load_raw_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


load_question_catalog = load_raw_catalog


def get_contextual_questions(
    *,
    locale: str | None = None,
    bleeding_level: str | None = None,
    pregnancy_status: str | None = None,
    show_sensitive: bool = False,
    enable_lifestyle: bool = True,
    answered_onboarding: list[str] | None = None,
    skipped_onboarding: list[str] | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    """يُرجع الكتالوج المترجم والمصفّى حسب سياق اليوم وإعدادات الخصوصية."""
    raw = load_raw_catalog()
    loc = resolve_locale(locale)
    t = get_translator()

    def _tr(key: str) -> str:
        return t.t(key, loc)

    def _localize_question(q: dict[str, Any]) -> dict[str, Any]:
        item = dict(q)
        base_prefix = f"questions.{q['id']}"
        item["label"] = _tr(q["i18n_key"])
        if "secondary_i18n_key" in q:
            item["secondary_label"] = _tr(q["secondary_i18n_key"])
        if "why_key" in q:
            item["why_label"] = _tr(q["why_key"])
            item["why_text"] = item["why_label"]
        if "options" in q:
            item["localized_options"] = [
                {"value": opt, "code": opt, "label": _tr(f"{base_prefix}.options.{opt}")}
                for opt in q["options"]
            ]
            item["options_localized"] = item["localized_options"]
        if "tags" in q:
            item["localized_tags"] = [
                {"value": tag, "code": tag, "label": _tr(f"{base_prefix}.tags.{tag}")}
                for tag in q["tags"]
            ]
        return item

    is_bleeding = (bleeding_level or "").strip().lower() in ("light", "medium", "heavy")
    is_pregnant_or_possible = (pregnancy_status or "").strip().lower() in (
        "possible", "confirmed", "احتمال حمل", "حمل مؤكد", "غير مؤكد", "مؤكد",
    )
    is_trying = (pregnancy_status or "").strip().lower() in ("trying", "محاولة حمل", "ttc")
    eff_sensitive = show_sensitive or is_trying

    onboarding_all = [_localize_question(q) for q in raw.get("onboarding", [])]
    onboarding_initial = [q for q in onboarding_all if q.get("initial_step")]
    done_set = set(answered_onboarding or []) | set(skipped_onboarding or [])
    remaining_qotd = [
        q for q in onboarding_all
        if not q.get("initial_step")
        and q["id"] not in done_set
        and (eff_sensitive or not q.get("sensitive"))
    ]
    # سؤال اليوم: أول سؤال غير مجاب/متخطّى من الأسئلة المتبقية (يتوزع على أسبوعين)
    question_of_the_day = remaining_qotd[0] if remaining_qotd else None

    daily_core = [_localize_question(q) for q in raw.get("daily_core", [])]

    daily_bleeding = (
        [_localize_question(q) for q in raw.get("daily_bleeding", [])]
        if is_bleeding
        else []
    )

    non_bleeding_pool = [
        _localize_question(q)
        for q in raw.get("daily_non_bleeding", [])
        if eff_sensitive or not q.get("sensitive")
    ]
    if not is_bleeding and non_bleeding_pool:
        if target_date:
            day_ord = date.fromisoformat(target_date[:10]).toordinal()
            rot_idx = day_ord % len(non_bleeding_pool)
            daily_non_bleeding = non_bleeding_pool[rot_idx:] + non_bleeding_pool[:rot_idx]
        else:
            daily_non_bleeding = list(non_bleeding_pool)
    else:
        daily_non_bleeding = []

    daily_lifestyle = (
        [_localize_question(q) for q in raw.get("daily_lifestyle", [])]
        if enable_lifestyle
        else []
    )

    raw_safety = raw.get("daily_safety", {})
    safety_items = []
    for s_item in raw_safety.get("items", []):
        if s_item.get("when") == "pregnant_or_possible" and not is_pregnant_or_possible:
            continue
        if s_item.get("sensitive") and not eff_sensitive:
            continue
        safety_items.append(
            {
                **s_item,
                "code": s_item["id"],
                "label": _tr(s_item["i18n_key"]),
            }
        )
    none_label = _tr(raw_safety.get("none_option_key", "questions.s.daily_safety.none"))
    daily_safety = {
        "id": raw_safety.get("id", "s.daily_safety"),
        "field": raw_safety.get("field", "flags"),
        "i18n_key": raw_safety.get("i18n_key", "questions.s.daily_safety.label"),
        "label": _tr(raw_safety.get("i18n_key", "questions.s.daily_safety.label")),
        "none_label": none_label,
        "skippable": True,
        "clinician_status": raw_safety.get("clinician_status", "approved"),
        "items": safety_items,
        "options_localized": [
            {"code": "flag.none", "label": none_label},
            *[{"code": s["id"], "label": s["label"]} for s in safety_items],
        ],
    }

    symptom_catalog = [
        {
            **sym,
            "label": _tr(sym["i18n_key"]),
        }
        for sym in raw.get("symptom_catalog", [])
        if eff_sensitive or not sym.get("sensitive")
    ]

    return {
        "version": raw.get("version", "1.0"),
        "locale": loc,
        "context": {
            "is_bleeding_day": is_bleeding,
            "is_pregnant_or_possible": is_pregnant_or_possible,
            "show_sensitive": eff_sensitive,
            "enable_lifestyle": enable_lifestyle,
        },
        "onboarding_initial": onboarding_initial,
        "question_of_the_day": question_of_the_day,
        "onboarding_all": onboarding_all,
        "daily_core": daily_core,
        "daily_bleeding": daily_bleeding,
        "bleeding_questions": daily_bleeding,
        "daily_non_bleeding": daily_non_bleeding,
        "non_bleeding_questions": daily_non_bleeding,
        "daily_lifestyle": daily_lifestyle,
        "lifestyle_questions": daily_lifestyle,
        "daily_safety": daily_safety,
        "safety_check": daily_safety,
        "symptom_catalog": symptom_catalog,
    }
