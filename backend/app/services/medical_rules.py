"""Deterministic Medical Rules Engine (PRD §16–27, §66–67).

* Runs BEFORE any AI. Its output is authoritative for alert severity.
* Pure function of its input: same data + same config → same findings.
* Thresholds come from app/medical/rules.json, user-facing text from
  app/medical/content.json. Every finding records rule_version + content_version.
* Produces triage categories, never diagnoses.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain import (
    ASSOCIATED_PAIN_SYMPTOMS,
    BLEEDING_SYMPTOMS,
    HORMONAL_CONTRACEPTION,
    ClotSize,
    FlowLevel,
    PregnancyPossibility,
    PregnancyTestResult,
    Severity,
    Symptom,
    value_of,
)
from app.services.cycle_engine import (
    BleedingEntry,
    CycleView,
    PeriodRecord,
    SymptomEntry,
    build_cycle_views,
    period_windows,
    sort_periods,
)

MEDICAL_DIR = Path(__file__).resolve().parent.parent / "medical"


@lru_cache
def load_rules_config() -> dict:
    return json.loads((MEDICAL_DIR / "rules.json").read_text(encoding="utf-8"))


@lru_cache
def load_content() -> dict:
    return json.loads((MEDICAL_DIR / "content.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PregnancyTestEntry:
    day: date
    result: PregnancyTestResult


@dataclass
class RulesInput:
    today: date
    periods: list[PeriodRecord] = field(default_factory=list)
    bleeding: list[BleedingEntry] = field(default_factory=list)
    symptoms: list[SymptomEntry] = field(default_factory=list)
    pregnancy_tests: list[PregnancyTestEntry] = field(default_factory=list)
    age: int | None = None
    contraception: str | None = None
    breastfeeding: bool | None = None
    pregnancy_possibility: str | None = None
    known_bleeding_disorder: bool | None = None
    typical_cycle_length: int | None = None


@dataclass
class Finding:
    rule_code: str
    rule_version: str
    content_version: str
    severity: Severity
    variant: str
    title: str
    summary: str
    why: str
    recommended_action: str
    disclaimer: str
    evidence: dict[str, Any]
    context_notes: list[str]
    source: str | None
    source_url: str | None

    @property
    def is_emergency(self) -> bool:
        return self.severity == Severity.EMERGENCY

    def to_dict(self) -> dict:
        return {
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "content_version": self.content_version,
            "severity": self.severity.value,
            "variant": self.variant,
            "title": self.title,
            "summary": self.summary,
            "why": self.why,
            "recommended_action": self.recommended_action,
            "disclaimer": self.disclaimer,
            "evidence": self.evidence,
            "context_notes": self.context_notes,
            "is_emergency": self.is_emergency,
            "source": self.source,
            "source_url": self.source_url,
        }


@dataclass
class Evaluation:
    evaluated_for: date
    ruleset_version: str
    content_version: str
    overall_severity: Severity
    findings: list[Finding]
    alert: Finding | None
    data_gaps: list[str]
    pregnancy_confirmed: bool = False

    @property
    def requires_immediate_attention(self) -> bool:
        return self.overall_severity >= Severity.URGENT

    def to_dict(self) -> dict:
        return {
            "evaluated_for": self.evaluated_for.isoformat(),
            "ruleset_version": self.ruleset_version,
            "content_version": self.content_version,
            "overall_severity": self.overall_severity.value,
            "requires_immediate_attention": self.requires_immediate_attention,
            "findings": [f.to_dict() for f in self.findings],
            "alert": self.alert.to_dict() if self.alert else None,
            "data_gaps": self.data_gaps,
        }


@dataclass
class _Raw:
    code: str
    severity: Severity
    variant: str
    evidence: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MedicalRulesEngine:
    def __init__(self, config: dict | None = None, content: dict | None = None, language: str = "ar") -> None:
        self.config = config or load_rules_config()
        self.content = content or load_content()
        self.language = language if language in ("ar", "en") else "en"

    # -- config helpers -----------------------------------------------------
    def _rule(self, code: str) -> dict:
        return self.config["rules"][code]

    def _t(self, code: str) -> dict:
        return self._rule(code)["thresholds"]

    def _enabled(self, code: str) -> bool:
        return bool(self.config["rules"].get(code, {}).get("enabled", False))

    def _label(self, key: str) -> str:
        return self.content["labels"][self.language].get(key, key.replace("_", " "))

    def _join(self, items: list[Any]) -> str:
        sep = "، " if self.language == "ar" else ", "
        return sep.join(str(i) for i in items)

    # -- public -------------------------------------------------------------
    def evaluate(self, inp: RulesInput) -> Evaluation:
        ctx = _Context.build(inp)
        rules = [
            ("MR-001", self._mr001_cycle_interval),
            ("MR-002", self._mr002_period_duration),
            ("MR-003", self._mr003_heavy_bleeding),
            ("MR-004", self._mr004_intermenstrual),
            ("MR-005", self._mr005_postcoital),
            ("MR-006", self._mr006_severe_pain),
            ("MR-007", self._mr007_missed_periods),
            ("MR-008", self._mr008_pregnancy_bleeding),
            ("MR-009", self._mr009_baseline_change),
            ("MR-011", self._mr011_associated_pain),
        ]
        raws: list[_Raw] = []
        for code, fn in rules:
            if self._enabled(code):
                raw = fn(inp, ctx)
                if raw is not None:
                    raws.append(raw)

        findings = sorted((self._render(r) for r in raws), key=lambda f: (-f.severity.rank, f.rule_code))
        alert = self._consolidate(findings)
        overall = findings[0].severity if findings else Severity.NORMAL
        return Evaluation(
            evaluated_for=inp.today,
            ruleset_version=self.config["ruleset_version"],
            content_version=self.content["content_version"],
            overall_severity=overall,
            findings=findings,
            alert=alert,
            data_gaps=ctx.data_gaps(inp),
            pregnancy_confirmed=ctx.pregnancy_confirmed,
        )

    # -- rendering ----------------------------------------------------------
    def _render(self, raw: _Raw) -> Finding:
        rule = self._rule(raw.code)
        msg = self.content["messages"][f"{raw.code}.{raw.variant}"][self.language]
        params = {k: (self._join(v) if isinstance(v, list | tuple) else v) for k, v in raw.params.items()}
        fmt = lambda s: s.format(**params)  # noqa: E731
        notes = [self.content["notes"][n][self.language] for n in dict.fromkeys(raw.notes)]
        return Finding(
            rule_code=raw.code,
            rule_version=rule["version"],
            content_version=self.content["content_version"],
            severity=raw.severity,
            variant=raw.variant,
            title=fmt(msg["title"]),
            summary=fmt(msg["summary"]),
            why=fmt(msg["why"]),
            recommended_action=fmt(msg["action"]),
            disclaimer=self.content["disclaimer"][self.language],
            evidence=raw.evidence,
            context_notes=notes,
            source=rule.get("source"),
            source_url=rule.get("source_url"),
        )

    def _consolidate(self, findings: list[Finding]) -> Finding | None:
        """MR-010: one consolidated alert instead of several notifications."""
        if not findings:
            return None
        top = findings[0]
        if not self._enabled("MR-010"):
            return top
        t = self._t("MR-010")
        significant = [f for f in findings if f.severity >= Severity(t["min_severity"])]
        if len(significant) < t["min_findings"]:
            return top
        raw = _Raw(
            code="MR-010",
            severity=top.severity,
            variant="consolidated",
            evidence={
                "component_rule_codes": [f.rule_code for f in significant],
                "component_severities": {f.rule_code: f.severity.value for f in significant},
            },
            params={
                "count": len(significant),
                "titles_text": [f.title for f in significant],
                "top_action": top.recommended_action,
            },
        )
        return self._render(raw)

    # ------------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------------

    def _mr001_cycle_interval(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-001")
        lengths = ctx.lengths[-t["window_cycles"] :]
        if not lengths:
            return None
        notes: list[str] = []
        max_days = t["max_days"]
        if inp.age is not None and inp.age < t["adolescent_age_below"]:
            max_days = t["adolescent_max_days"]
            notes.append("adolescent")
        if inp.age is not None and inp.age >= 45:
            notes.append("perimenopause_age")
        if value_of(inp.contraception) in HORMONAL_CONTRACEPTION:
            notes.append("hormonal_contraception")
        out = [n for n in lengths if n < t["min_days"] or n > max_days]
        evidence = {
            "cycles_analyzed": len(lengths),
            "cycle_lengths": lengths,
            "out_of_range": out,
            "thresholds": {"min_days": t["min_days"], "max_days": max_days},
        }
        params = {"out_count": len(out), "analyzed": len(lengths), "min_days": t["min_days"], "max_days": max_days, "lengths_text": lengths}
        if len(out) >= t["min_out_of_range"]:
            return _Raw("MR-001", Severity.MEDICAL_REVIEW, "review", evidence, params, notes)
        latest = lengths[-1]
        if out and (latest < t["min_days"] or latest > max_days):
            # One isolated cycle: never a medical alert on its own.
            return _Raw("MR-001", Severity.MONITOR, "monitor", {**evidence, "repeated_pattern": False}, {**params, "latest": latest}, notes)
        return None

    def _mr002_period_duration(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-002")
        recent = [v for v in ctx.views if v.period_days][-t["lookback_periods"] :]
        prolonged = [v for v in recent if v.period_days > t["max_period_days"]]
        if not prolonged:
            return None
        notes = []
        if any(not v.end_is_explicit for v in prolonged):
            notes.append("derived_from_logs")
        if value_of(inp.contraception) in HORMONAL_CONTRACEPTION:
            notes.append("hormonal_contraception")
        if inp.known_bleeding_disorder:
            notes.append("bleeding_disorder")
        evidence = {
            "periods_analyzed": len(recent),
            "period_durations": [v.period_days for v in recent],
            "prolonged_periods": [
                {"start": v.start.isoformat(), "days": v.period_days, "end_recorded": v.end_is_explicit} for v in prolonged
            ],
            "threshold_days": t["max_period_days"],
        }
        params = {"prolonged_count": len(prolonged), "max_days": t["max_period_days"], "longest": max(v.period_days for v in prolonged)}
        return _Raw("MR-002", Severity.MEDICAL_REVIEW, "review", evidence, params, notes)

    def _mr003_heavy_bleeding(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-003")
        since = inp.today - timedelta(days=t["lookback_days"])
        logs = [b for b in inp.bleeding if since <= b.day <= inp.today and b.is_bleeding]
        indicator_days: dict[str, list[date]] = {}

        def add(key: str, d: date) -> None:
            indicator_days.setdefault(key, []).append(d)

        for b in logs:
            if b.frequent_changes:
                add("frequent_changes", b.day)
            if b.double_protection:
                add("double_protection", b.day)
            if b.leakage:
                add("leakage", b.day)
            if b.flooding:
                add("flooding", b.day)
            if b.clot_size == ClotSize.LARGE:
                add("large_clots", b.day)
            if b.affects_daily_life:
                add("affects_daily_life", b.day)
        very_heavy = [b.day for b in logs if b.flow == FlowLevel.VERY_HEAVY]
        if len(very_heavy) >= t["very_heavy_days_indicator"]:
            indicator_days["very_heavy_flow_multiple_days"] = very_heavy

        # Acute escalation: heavy bleeding in the last N days + systemic symptoms.
        acute_since = inp.today - timedelta(days=t["acute_window_days"])
        acute_heavy = any(
            b.day >= acute_since and (b.flow in (FlowLevel.HEAVY, FlowLevel.VERY_HEAVY) or b.frequent_changes or b.flooding or b.leakage)
            for b in logs
        )
        acute_symptoms = ctx.symptom_codes_between(acute_since, inp.today)
        emergency_flags = sorted(acute_symptoms & {Symptom.FAINTING.value, Symptom.LOSS_OF_CONSCIOUSNESS.value})
        urgent_flags = sorted(acute_symptoms & {Symptom.DIZZINESS.value, Symptom.SHORTNESS_OF_BREATH.value})

        if not indicator_days and not (acute_heavy and (emergency_flags or urgent_flags)):
            return None

        notes = ["bleeding_disorder"] if inp.known_bleeding_disorder else []
        supporting = sorted(ctx.symptom_codes_between(since, inp.today) & {Symptom.FATIGUE.value, Symptom.SHORTNESS_OF_BREATH.value})
        evidence = {
            "lookback_days": t["lookback_days"],
            "indicators": {k: sorted({d.isoformat() for d in v}) for k, v in indicator_days.items()},
            "supporting_symptoms": supporting,
        }
        indicators_text = [self._label(k) for k in indicator_days] or [self._label("heavy_flow")]
        params = {"indicators_text": indicators_text, "acute_days": t["acute_window_days"]}
        if acute_heavy and emergency_flags:
            evidence["acute_red_flags"] = emergency_flags + urgent_flags
            return _Raw(
                "MR-003",
                Severity.EMERGENCY,
                "emergency",
                evidence,
                {**params, "red_flags_text": [self._label(f) for f in emergency_flags + urgent_flags]},
                notes,
            )
        if acute_heavy and urgent_flags:
            evidence["acute_red_flags"] = urgent_flags
            return _Raw(
                "MR-003", Severity.URGENT, "urgent", evidence, {**params, "red_flags_text": [self._label(f) for f in urgent_flags]}, notes
            )
        return _Raw("MR-003", Severity.MEDICAL_REVIEW, "review", evidence, params, notes)

    def _mr004_intermenstrual(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        if ctx.pregnancy_confirmed:
            return None  # handled by MR-008
        t = self._t("MR-004")
        since = inp.today - timedelta(days=t["lookback_days"])
        explicit = sorted(
            {s.day for s in inp.symptoms if since <= s.day <= inp.today and Symptom.BLEEDING_BETWEEN_PERIODS.value in s.symptoms}
        )

        derived: set[date] = set()
        if ctx.views:
            first_start = ctx.views[0].start
            candidates = {b.day for b in inp.bleeding if b.is_bleeding} | {
                s.day for s in inp.symptoms if Symptom.SPOTTING.value in s.symptoms
            }
            grace = timedelta(days=t["pre_period_grace_days"])
            starts = [v.start for v in ctx.views]
            for d in candidates:
                if not (since <= d <= inp.today) or d < first_start:
                    continue
                if any(ws <= d <= we for ws, we in ctx.windows):
                    continue
                if any(timedelta(0) < s - d <= grace for s in starts):
                    continue  # pre-period spotting
                derived.add(d)
        all_days = sorted(set(explicit) | derived)
        if not all_days:
            return None
        notes = ["hormonal_contraception"] if value_of(inp.contraception) in HORMONAL_CONTRACEPTION else []
        evidence = {
            "lookback_days": t["lookback_days"],
            "explicitly_reported_days": [d.isoformat() for d in explicit],
            "bleeding_logged_outside_periods": sorted(d.isoformat() for d in derived),
        }
        params = {"days_count": len(all_days), "lookback": t["lookback_days"], "dates_text": [d.isoformat() for d in all_days[-5:]]}
        if explicit or len(derived) >= t["min_derived_days_for_review"]:
            return _Raw("MR-004", Severity.MEDICAL_REVIEW, "review", evidence, params, notes)
        return _Raw("MR-004", Severity.MONITOR, "monitor", evidence, params, notes)

    def _mr005_postcoital(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        if ctx.pregnancy_confirmed:
            return None  # handled by MR-008
        t = self._t("MR-005")
        since = inp.today - timedelta(days=t["lookback_days"])
        days = sorted({s.day for s in inp.symptoms if since <= s.day <= inp.today and Symptom.BLEEDING_AFTER_SEX.value in s.symptoms})
        if not days:
            return None
        evidence = {"lookback_days": t["lookback_days"], "reported_days": [d.isoformat() for d in days]}
        return _Raw("MR-005", Severity.MEDICAL_REVIEW, "review", evidence, {"days_count": len(days), "lookback": t["lookback_days"]})

    def _mr006_severe_pain(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-006")
        since = inp.today - timedelta(days=t["lookback_days"])
        acute_since = inp.today - timedelta(days=t["acute_window_days"])

        def is_severe(s: SymptomEntry) -> bool:
            return (s.pain_level is not None and s.pain_level >= t["severe_pain_min"]) or Symptom.SUDDEN_SEVERE_PAIN.value in s.symptoms

        severe = [s for s in inp.symptoms if since <= s.day <= inp.today and is_severe(s)]
        if not severe:
            return None
        acute = [s for s in severe if s.day >= acute_since]
        acute_codes = ctx.symptom_codes_between(acute_since, inp.today)
        if any("shoulder" in s.pain_location for s in inp.symptoms if acute_since <= s.day <= inp.today):
            acute_codes = acute_codes | {Symptom.SHOULDER_TIP_PAIN.value}
        pregnancy_possible = ctx.pregnancy_confirmed or value_of(inp.pregnancy_possibility) in (
            PregnancyPossibility.YES.value,
            PregnancyPossibility.UNSURE.value,
        )

        evidence: dict[str, Any] = {
            "lookback_days": t["lookback_days"],
            "severe_pain_days": sorted({s.day.isoformat() for s in severe}),
            "max_pain_level": max((s.pain_level or 0) for s in severe) or None,
        }
        params = {"acute_days": t["acute_window_days"], "severe_min": t["severe_pain_min"], "lookback": t["lookback_days"]}

        if acute:
            emergency = acute_codes & {Symptom.FAINTING.value, Symptom.LOSS_OF_CONSCIOUSNESS.value}
            if pregnancy_possible:
                emergency |= acute_codes & {Symptom.SUDDEN_SEVERE_PAIN.value, Symptom.SHOULDER_TIP_PAIN.value, Symptom.DIZZINESS.value}
            urgent = acute_codes & {Symptom.PAINKILLERS_NOT_HELPING.value, Symptom.FEVER.value, Symptom.SUDDEN_SEVERE_PAIN.value}
            flags = sorted(emergency) + sorted(urgent - emergency)
            if pregnancy_possible:
                flags.append("pregnancy_confirmed" if ctx.pregnancy_confirmed else "pregnancy_possible")
            if emergency:
                evidence["acute_red_flags"] = flags
                return _Raw(
                    "MR-006", Severity.EMERGENCY, "emergency", evidence, {**params, "red_flags_text": [self._label(f) for f in flags]}
                )
            if urgent or pregnancy_possible:
                evidence["acute_red_flags"] = flags
                return _Raw("MR-006", Severity.URGENT, "urgent", evidence, {**params, "red_flags_text": [self._label(f) for f in flags]})

        affecting = sorted({s.day for s in severe if s.affects_daily_activity})
        if affecting:
            evidence["days_affecting_daily_activity"] = [d.isoformat() for d in affecting]
            return _Raw("MR-006", Severity.MEDICAL_REVIEW, "review", evidence, {**params, "days_count": len(affecting)})
        return None

    def _mr007_missed_periods(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        if not ctx.views or ctx.pregnancy_confirmed:
            return None
        t = self._t("MR-007")
        last_start = ctx.views[-1].start
        days_since = (inp.today - last_start).days
        if len(ctx.lengths) >= t["min_cycles_for_baseline"]:
            reference, reference_basis = round(statistics.median(ctx.lengths[-6:])), "recorded_median"
        elif inp.typical_cycle_length:
            reference, reference_basis = inp.typical_cycle_length, "user_reported"
        else:
            reference, reference_basis = t["default_reference_days"], "default"
        reference = max(t["reference_min_days"], min(t["reference_max_days"], reference))
        if days_since - reference <= t["late_grace_days"]:
            return None
        missed = math.floor((days_since - t["late_grace_days"]) / reference)
        if missed < 1:
            return None

        notes: list[str] = ["log_missing_periods"]
        severity = Severity.MEDICAL_REVIEW if missed >= t["missed_for_review"] else Severity.MONITOR
        if inp.breastfeeding:
            notes.append("breastfeeding")
            severity = min(severity, Severity.MONITOR, key=lambda s: s.rank)
        if value_of(inp.contraception) in HORMONAL_CONTRACEPTION:
            notes.append("hormonal_contraception")
            severity = min(severity, Severity.MONITOR, key=lambda s: s.rank)
        if value_of(inp.pregnancy_possibility) in (PregnancyPossibility.YES.value, PregnancyPossibility.UNSURE.value):
            expected = last_start + timedelta(days=reference)
            has_negative_after = any(pt.result == PregnancyTestResult.NEGATIVE and pt.day >= expected for pt in inp.pregnancy_tests)
            if not has_negative_after:
                notes.append("pregnancy_test_suggested")

        evidence = {
            "last_period_start": last_start.isoformat(),
            "days_since_last_period": days_since,
            "reference_cycle_days": reference,
            "reference_basis": reference_basis,
            "estimated_missed_periods": missed,
        }
        params = {"days_since": days_since, "reference": reference, "missed": missed}
        variant = "review" if missed >= t["missed_for_review"] else "monitor"
        return _Raw("MR-007", severity, variant, evidence, params, notes)

    def _mr008_pregnancy_bleeding(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        if not ctx.pregnancy_confirmed:
            return None
        t = self._t("MR-008")
        since = ctx.pregnancy_since or (inp.today - timedelta(days=t["lookback_days_without_test_date"]))
        bleed_days = {b.day for b in inp.bleeding if b.is_bleeding and since <= b.day <= inp.today}
        bleed_days |= {s.day for s in inp.symptoms if since <= s.day <= inp.today and (set(s.symptoms) & BLEEDING_SYMPTOMS)}
        bleed_days |= {p.start for p in inp.periods if since <= p.start <= inp.today}
        if not bleed_days:
            return None
        last_bleed = max(bleed_days)
        urgent_since = inp.today - timedelta(days=t["urgent_window_days"])
        emerg_since = inp.today - timedelta(days=t["emergency_window_days"])

        flags: list[str] = []
        if any(
            b.day >= emerg_since and (b.flow in (FlowLevel.HEAVY, FlowLevel.VERY_HEAVY) or b.leakage or b.flooding or b.frequent_changes)
            for b in inp.bleeding
            if b.day <= inp.today
        ):
            flags.append("heavy_flow")
        recent_symptoms = [s for s in inp.symptoms if emerg_since <= s.day <= inp.today]
        if any((s.pain_level or 0) >= 7 or Symptom.SUDDEN_SEVERE_PAIN.value in s.symptoms for s in recent_symptoms):
            flags.append("severe_pain")
        codes = ctx.symptom_codes_between(emerg_since, inp.today)
        if any("shoulder" in s.pain_location for s in recent_symptoms):
            codes = codes | {Symptom.SHOULDER_TIP_PAIN.value}
        for code in (
            Symptom.SHOULDER_TIP_PAIN,
            Symptom.FAINTING,
            Symptom.DIZZINESS,
            Symptom.LOSS_OF_CONSCIOUSNESS,
            Symptom.NAUSEA,
            Symptom.VOMITING,
        ):
            if code.value in codes:
                flags.append(code.value)

        evidence = {
            "pregnancy_basis": ctx.pregnancy_basis,
            "pregnancy_since": ctx.pregnancy_since.isoformat() if ctx.pregnancy_since else None,
            "bleeding_days": sorted(d.isoformat() for d in bleed_days),
            "last_bleeding_date": last_bleed.isoformat(),
        }
        params = {"last_bleeding_date": last_bleed.isoformat(), "pregnancy_basis": self._label(ctx.pregnancy_basis or "user_reported")}
        if last_bleed >= emerg_since and flags:
            evidence["emergency_features"] = flags
            return _Raw("MR-008", Severity.EMERGENCY, "emergency", evidence, {**params, "red_flags_text": [self._label(f) for f in flags]})
        if last_bleed >= urgent_since:
            return _Raw("MR-008", Severity.URGENT, "urgent", evidence, params)
        return _Raw("MR-008", Severity.MEDICAL_REVIEW, "review", evidence, params)

    def _mr009_baseline_change(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-009")
        lengths = ctx.lengths
        n, dev = t["baseline_cycles"], t["deviation_days"]
        if len(lengths) < t["min_baseline_cycles"] + 1:
            return None
        latest = lengths[-1]
        # Persistent change: last 2 cycles both deviate the same way from the baseline before them.
        if len(lengths) >= t["min_baseline_cycles"] + 2:
            base2 = statistics.median(lengths[-(n + 2) : -2])
            d1, d2 = lengths[-2] - base2, latest - base2
            if abs(d1) >= dev and abs(d2) >= dev and (d1 > 0) == (d2 > 0):
                evidence = {
                    "baseline_median": base2,
                    "baseline_cycles": lengths[-(n + 2) : -2],
                    "recent_cycles": lengths[-2:],
                    "deviation_threshold_days": dev,
                    "repeated_pattern": True,
                }
                return _Raw(
                    "MR-009",
                    Severity.MEDICAL_REVIEW,
                    "review",
                    evidence,
                    {"persist": 2, "recent_text": lengths[-2:], "baseline": _fmt_num(base2)},
                )
        base1 = statistics.median(lengths[-(n + 1) : -1])
        if abs(latest - base1) >= dev:
            evidence = {
                "baseline_median": base1,
                "baseline_cycles": lengths[-(n + 1) : -1],
                "latest_cycle": latest,
                "deviation_threshold_days": dev,
                "repeated_pattern": False,
            }
            return _Raw("MR-009", Severity.MONITOR, "monitor", evidence, {"latest": latest, "baseline": _fmt_num(base1)})
        return None

    def _mr011_associated_pain(self, inp: RulesInput, ctx: _Context) -> _Raw | None:
        t = self._t("MR-011")
        since = inp.today - timedelta(days=t["lookback_days"])
        hits = [(s.day, set(s.symptoms) & ASSOCIATED_PAIN_SYMPTOMS) for s in inp.symptoms if since <= s.day <= inp.today]
        hits = [(d, c) for d, c in hits if c]
        if not hits:
            return None
        days = sorted({d for d, _ in hits})
        codes = sorted(set().union(*(c for _, c in hits)))
        evidence = {"lookback_days": t["lookback_days"], "days": [d.isoformat() for d in days], "symptoms": codes}
        params = {
            "symptoms_text": [self._label(c) for c in codes],
            "days_count": len(days),
            "lookback": t["lookback_days"],
            "dates_text": [d.isoformat() for d in days],
        }
        if len(days) >= t["min_days_for_review"]:
            return _Raw("MR-011", Severity.MEDICAL_REVIEW, "review", evidence, params)
        return _Raw("MR-011", Severity.MONITOR, "monitor", evidence, params)


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


# ---------------------------------------------------------------------------
# Derived context shared by rules
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    views: list[CycleView]
    lengths: list[int]
    windows: list[tuple[date, date]]
    symptoms: list[SymptomEntry]
    pregnancy_confirmed: bool
    pregnancy_since: date | None
    pregnancy_basis: str | None
    positive_test_superseded: bool

    @classmethod
    def build(cls, inp: RulesInput) -> _Context:
        periods = sort_periods([p for p in inp.periods if p.start <= inp.today])
        views = build_cycle_views(periods, [b for b in inp.bleeding if b.day <= inp.today])
        lengths = [v.cycle_length for v in views if v.cycle_length is not None]
        confirmed, since, basis, superseded = cls._pregnancy(inp, periods)
        return cls(
            views=views,
            lengths=lengths,
            windows=period_windows(views, inp.today),
            symptoms=inp.symptoms,
            pregnancy_confirmed=confirmed,
            pregnancy_since=since,
            pregnancy_basis=basis,
            positive_test_superseded=superseded,
        )

    @staticmethod
    def _pregnancy(inp: RulesInput, periods: list[PeriodRecord]) -> tuple[bool, date | None, str | None, bool]:
        """A pregnancy is 'confirmed' only from explicit user data — never inferred from a late period."""
        cfg = load_rules_config()["rules"]["MR-008"]["thresholds"]
        tests = sorted((pt for pt in inp.pregnancy_tests if pt.day <= inp.today), key=lambda pt: pt.day)
        latest_positive = next((pt for pt in reversed(tests) if pt.result == PregnancyTestResult.POSITIVE), None)
        superseded = False
        if latest_positive and (inp.today - latest_positive.day).days <= cfg["positive_test_valid_days"]:
            negative_after = any(pt.result == PregnancyTestResult.NEGATIVE and pt.day > latest_positive.day for pt in tests)
            periods_after = [p for p in periods if p.start > latest_positive.day]
            if negative_after:
                pass
            elif len(periods_after) >= cfg["periods_after_test_to_assume_not_current"]:
                superseded = True
            else:
                return True, latest_positive.day, "positive_test", False
        if value_of(inp.pregnancy_possibility) == PregnancyPossibility.CONFIRMED.value:
            return True, None, "user_reported", superseded
        return False, None, None, superseded

    def symptom_codes_between(self, start: date, end: date) -> set[str]:
        codes: set[str] = set()
        for s in self.symptoms:
            if start <= s.day <= end:
                codes |= set(s.symptoms)
        return codes

    def data_gaps(self, inp: RulesInput) -> list[str]:
        gaps = []
        if not self.views:
            gaps.append("no_periods_recorded")
        elif len(self.lengths) < 3:
            gaps.append("fewer_than_3_completed_cycles")
        missing_end = [v for v in self.views[:-1] if v.end is None]
        if missing_end:
            gaps.append("period_end_dates_missing")
        if self.positive_test_superseded:
            gaps.append("positive_pregnancy_test_superseded_by_logged_periods")
        if inp.pregnancy_possibility is None:
            gaps.append("pregnancy_context_not_provided")
        return gaps
