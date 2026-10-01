"""سجل القواعد الطبية MR-001..MR-010.

يقيس أربعة أشياء: البنية المعتمدة، خمول MR-009/MR-010 بلا عتبات مخترعة،
أن ثوابت المحرك كلها مصدرها الملف لا الكود، وأن أي ملف ناقص أو فاسد يفشل
صريحًا (RulesRegistryError) بدل تشغيل قيم صامتة.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.schemas import CycleStat, Finding, Severity, UserContext
from app.services import rules_engine
from app.services.rules_engine import (
    RulesRegistryError,
    compute_findings,
    compute_symptom_findings,
    load_rules,
)

REPO = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO / "app" / "data" / "medical_rules.json"
SOURCES_PATH = REPO / "app" / "data" / "sources.json"

TODAY = date(2026, 9, 30)

EXPECTED_IDS = [f"MR-{i:03d}" for i in range(1, 11)]


def _by_id() -> dict[str, dict]:
    return {r["id"]: r for r in load_rules()["rules"]}


def _ctx(cycles: int, bleeding: int = 5, avg: int = 28, gaps: list[int] | None = None) -> UserContext:
    return UserContext(
        cycles_recorded=cycles,
        avg_cycle_days=avg,
        cycle_gaps=gaps if gaps is not None else [],
        last_cycles=[CycleStat(start_date=(TODAY - timedelta(days=28)).isoformat(),
                               bleeding_days=bleeding)],
    )


def _write(tmp_path: Path, payload: object) -> Path:
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------


def test_registry_ships_ten_rules_with_expected_ids():
    assert REGISTRY_PATH.is_file()
    data = load_rules()
    assert data["version"] == 1
    ids = [r["id"] for r in data["rules"]]
    assert ids == EXPECTED_IDS
    assert len(set(ids)) == 10


def test_every_rule_carries_clinical_review_gate():
    for rule in load_rules()["rules"]:
        review = rule["clinical_review"]
        assert review["required"] is True, rule["id"]
        assert review["reviewer"] is None, rule["id"]
        assert review["reviewed_at"] is None, rule["id"]


def test_mr009_and_mr010_are_dormant_placeholders():
    """لا عتبة ولا شرط ولا خطورة حتى تأتي مواصفات المستخدم — لا اختراع طبي."""
    by_id = _by_id()
    for rid in ("MR-009", "MR-010"):
        rule = by_id[rid]
        assert rule["threshold"] is None
        assert rule["severity"] is None
        assert rule["finding_code"] == ""
        assert rule["condition"] == ""
        assert rule["source_id"] is None
        assert rule["clinical_review"]["required"] is True


def test_active_rules_have_thresholds_and_valid_severities():
    severities = {s.value for s in Severity}
    for rule in load_rules()["rules"]:
        if rule["finding_code"] == "":
            continue
        if rule["finding_code"] == "NO_ALERT_PATTERN":
            assert rule["threshold"] is None  # قاعدة بنيوية لا عتب لها
        else:
            assert isinstance(rule["threshold"], dict) and rule["threshold"], rule["id"]
        assert rule["severity"]["base"] in severities
        escalate = rule["severity"].get("escalate_to")
        if escalate is not None:
            assert escalate in severities


def test_engine_constants_are_sourced_from_the_registry():
    """القيم المعتمدة: 7/10 · 90 · 35/60 · 21 · 7/14 · 3×شدة4×90 · 3 دورات."""
    by_id = _by_id()
    assert rules_engine.MAX_BLEEDING_DAYS == by_id["MR-001"]["threshold"]["days"] == 7
    assert rules_engine.LONG_BLEEDING_DAYS == by_id["MR-001"]["threshold"]["escalate_at"] == 10
    assert rules_engine.AMENORRHEA_DAYS == by_id["MR-002"]["threshold"]["days"] == 90
    assert rules_engine.LONG_CYCLE_DAYS == by_id["MR-003"]["threshold"]["days"] == 35
    assert rules_engine.LONG_CYCLE_ESCALATE_DAYS == by_id["MR-003"]["threshold"]["escalate_at"] == 60
    assert rules_engine.SHORT_CYCLE_DAYS == by_id["MR-004"]["threshold"]["days"] == 21
    assert rules_engine.IRREGULAR_SPREAD_DAYS == by_id["MR-005"]["threshold"]["monitor_at"] == 7
    assert rules_engine.IRREGULAR_SPREAD_HIGH == by_id["MR-005"]["threshold"]["medical_at"] == 14
    assert rules_engine.REPEATED_SEVERE_THRESHOLD == by_id["MR-006"]["threshold"]["severe_logs"] == 3
    assert rules_engine.SEVERE_SYMPTOM_LEVEL == by_id["MR-006"]["threshold"]["min_severity"] == 4
    assert rules_engine.SYMPTOM_WINDOW_DAYS == by_id["MR-006"]["threshold"]["window_days"] == 90
    assert rules_engine.MIN_CYCLES_FOR_PATTERN == by_id["MR-007"]["threshold"]["min_cycles"] == 3


def test_source_ids_resolve_in_sources_json():
    source_ids = {s["id"] for s in json.loads(SOURCES_PATH.read_text(encoding="utf-8"))}
    for rule in load_rules()["rules"]:
        if rule["source_id"] is not None:
            assert rule["source_id"] in source_ids, rule["id"]


def test_rule_ids_map_to_every_engine_code():
    assert rules_engine.RULE_IDS == {
        "PROLONGED_BLEEDING": "MR-001",
        "MISSED_PERIOD": "MR-002",
        "LONG_CYCLE": "MR-003",
        "SHORT_CYCLE": "MR-004",
        "IRREGULAR_CYCLE": "MR-005",
        "REPEATED_SEVERE_SYMPTOMS": "MR-006",
        "INSUFFICIENT_DATA": "MR-007",
        "NO_ALERT_PATTERN": "MR-008",
    }


# ---------------------------------------------------------------------------


def test_findings_carry_their_rule_id():
    long_bleed = compute_findings(_ctx(6, bleeding=9), today=TODAY)
    prolonged = next(f for f in long_bleed if f.rule_code == "PROLONGED_BLEEDING")
    assert prolonged.rule_id == "MR-001"

    no_alert = compute_findings(_ctx(6, bleeding=5, gaps=[28, 28]), today=TODAY)
    assert [f.rule_id for f in no_alert] == ["MR-008"]

    insufficient = compute_findings(_ctx(1), today=TODAY)
    assert insufficient[0].rule_id == "MR-007"


def test_symptom_findings_carry_rule_id():
    logs = [
        {"symptom": "ألم شديد", "severity": 5,
         "log_date": (TODAY - timedelta(days=d)).isoformat()}
        for d in (5, 15, 25)
    ]
    fs = compute_symptom_findings(logs, today=TODAY)
    assert [f.rule_id for f in fs] == ["MR-006"]


def test_rule_id_is_additive_not_required():
    """مَن لا يعرف rule_id يبني Finding قديمًا كما كان — لا كسر للواجهات."""
    legacy = Finding(rule_code="X", severity=Severity.MONITOR, title="t")
    assert legacy.rule_id is None


# ---------------------------------------------------------------------------


def _minimal_active(**overrides: object) -> dict:
    rule = {
        "id": "MR-001",
        "title_ar": "t",
        "title_en": "t",
        "condition": "c",
        "threshold": {"days": 7},
        "severity": {"base": "MONITOR"},
        "finding_code": "PROLONGED_BLEEDING",
        "source_id": None,
        "clinical_review": {"required": True, "reviewer": None, "reviewed_at": None},
    }
    rule.update(overrides)
    return rule


def _minimal_dormant(**overrides: object) -> dict:
    rule = {
        "id": "MR-009",
        "title_ar": "",
        "title_en": "",
        "condition": "",
        "threshold": None,
        "severity": None,
        "finding_code": "",
        "source_id": None,
        "clinical_review": {"required": True, "reviewer": None, "reviewed_at": None},
    }
    rule.update(overrides)
    return rule


def test_missing_registry_file_fails_loud(tmp_path: Path):
    with pytest.raises(RulesRegistryError):
        load_rules(tmp_path / "nope.json")


def test_invalid_json_fails_loud(tmp_path: Path):
    p = tmp_path / "rules.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RulesRegistryError):
        load_rules(p)


def test_unsupported_version_fails_loud(tmp_path: Path):
    with pytest.raises(RulesRegistryError):
        load_rules(_write(tmp_path, {"version": 2, "rules": [_minimal_active()]}))


def test_duplicate_ids_fail_loud(tmp_path: Path):
    payload = {"version": 1, "rules": [_minimal_active(), _minimal_active()]}
    with pytest.raises(RulesRegistryError, match="MR-001"):
        load_rules(_write(tmp_path, payload))


def test_active_rule_without_threshold_fails_loud(tmp_path: Path):
    payload = {"version": 1, "rules": [_minimal_active(threshold=None)]}
    with pytest.raises(RulesRegistryError):
        load_rules(_write(tmp_path, payload))


def test_dormant_rule_with_threshold_fails_loud(tmp_path: Path):
    payload = {"version": 1, "rules": [_minimal_dormant(threshold={"days": 1})]}
    with pytest.raises(RulesRegistryError):
        load_rules(_write(tmp_path, payload))


def test_severity_outside_system_levels_fails_loud(tmp_path: Path):
    payload = {"version": 1, "rules": [_minimal_active(severity={"base": "HIGH"})]}
    with pytest.raises(RulesRegistryError):
        load_rules(_write(tmp_path, payload))


def test_missing_clinical_review_fails_loud(tmp_path: Path):
    rule = _minimal_active()
    rule.pop("clinical_review")
    with pytest.raises(RulesRegistryError):
        load_rules(_write(tmp_path, {"version": 1, "rules": [rule]}))


def test_valid_shaped_registry_passes(tmp_path: Path):
    payload = {"version": 1, "rules": [_minimal_active(), _minimal_dormant()]}
    data = load_rules(_write(tmp_path, payload))
    assert len(data["rules"]) == 2
