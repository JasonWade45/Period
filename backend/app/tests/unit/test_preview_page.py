"""The preview page must offer every symptom and pain location the API accepts."""

import re
from pathlib import Path

from app.domain import PainLocation, Symptom

HTML = (Path(__file__).resolve().parents[2] / "static" / "index.html").read_text(encoding="utf-8")


def _keys(const_name: str) -> set[str]:
    start = HTML.index(f"const {const_name} =")
    end = HTML.index("];" if const_name == "SYMPTOM_GROUPS" else "};", start)
    return set(re.findall(r"([a-z_]+):\"", HTML[start:end]))


def test_every_symptom_is_selectable():
    assert {s.value for s in Symptom} <= _keys("SYMPTOM_GROUPS")


def test_every_pain_location_except_shoulder_is_selectable():
    # shoulder pain is offered as the red-flag symptom "shoulder_tip_pain" instead of a location
    assert {p.value for p in PainLocation} - {"shoulder"} <= _keys("PAIN_LOCATIONS")
    assert "lower_back" in _keys("PAIN_LOCATIONS")
