"""Shared domain vocabulary (enums & code lists) used by DB, API and engines.

Keep these as plain string enums so they serialize cleanly and can be used in
CHECK constraints.
"""

from enum import Enum


class StrEnum(str, Enum):  # noqa: UP042 - explicit for clarity/portability
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class Severity(StrEnum):
    """Triage categories — NOT diagnoses."""

    NORMAL = "NORMAL"
    MONITOR = "MONITOR"
    MEDICAL_REVIEW = "MEDICAL_REVIEW"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def __ge__(self, other):  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other):  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other):  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other):  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank < other.rank
        return NotImplemented


_SEVERITY_RANK = {
    Severity.NORMAL: 0,
    Severity.MONITOR: 1,
    Severity.MEDICAL_REVIEW: 2,
    Severity.URGENT: 3,
    Severity.EMERGENCY: 4,
}


class FlowLevel(StrEnum):
    NONE = "none"
    SPOTTING = "spotting"
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    VERY_HEAVY = "very_heavy"

    @property
    def score(self) -> int:
        return _FLOW_SCORE[self]


_FLOW_SCORE = {
    FlowLevel.NONE: 0,
    FlowLevel.SPOTTING: 1,
    FlowLevel.LIGHT: 2,
    FlowLevel.MEDIUM: 3,
    FlowLevel.HEAVY: 4,
    FlowLevel.VERY_HEAVY: 5,
}


class ProductType(StrEnum):
    PAD = "pad"
    TAMPON = "tampon"
    MENSTRUAL_CUP = "menstrual_cup"
    PERIOD_UNDERWEAR = "period_underwear"
    OTHER = "other"


class ClotSize(StrEnum):
    NONE = "none"
    SMALL = "small"
    LARGE = "large"  # larger than ~2.5 cm (NHS heavy-period indicator)


class Contraception(StrEnum):
    NONE = "none"
    COMBINED_PILL = "combined_pill"
    PROGESTOGEN_ONLY_PILL = "progestogen_only_pill"
    IUD = "iud"  # copper, non-hormonal
    HORMONAL_IUS = "hormonal_ius"
    IMPLANT = "implant"
    INJECTION = "injection"
    PATCH = "patch"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


# NOTE: code sets hold plain string *values* (Enum hashing uses member names,
# so mixing enum members and raw strings in sets is error-prone).
HORMONAL_CONTRACEPTION = {
    c.value
    for c in (
        Contraception.COMBINED_PILL,
        Contraception.PROGESTOGEN_ONLY_PILL,
        Contraception.HORMONAL_IUS,
        Contraception.IMPLANT,
        Contraception.INJECTION,
        Contraception.PATCH,
    )
}

# Methods where irregular/absent bleeding is commonly expected, so calendar
# predictions are much less meaningful.
UNPREDICTABLE_BLEEDING_CONTRACEPTION = {
    c.value
    for c in (
        Contraception.PROGESTOGEN_ONLY_PILL,
        Contraception.HORMONAL_IUS,
        Contraception.IMPLANT,
        Contraception.INJECTION,
    )
}


class PregnancyPossibility(StrEnum):
    NO = "no"
    YES = "yes"
    UNSURE = "unsure"
    CONFIRMED = "confirmed"  # user reports a pregnancy confirmed by a professional/test
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class PregnancyTestResult(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class PainLocation(StrEnum):
    LOWER_ABDOMEN = "lower_abdomen"
    LOWER_BACK = "lower_back"
    PELVIS = "pelvis"
    ONE_SIDED = "one_sided"
    HEAD = "head"
    BREAST = "breast"
    SHOULDER = "shoulder"
    OTHER = "other"


class Symptom(StrEnum):
    # General
    BLOATING = "bloating"
    HEADACHE = "headache"
    MIGRAINE = "migraine"
    FATIGUE = "fatigue"
    NAUSEA = "nausea"
    DIARRHEA = "diarrhea"
    CONSTIPATION = "constipation"
    BREAST_TENDERNESS = "breast_tenderness"
    ACNE = "acne"
    MOOD_CHANGES = "mood_changes"
    IRRITABILITY = "irritability"
    ANXIETY = "anxiety"
    SLEEP_PROBLEMS = "sleep_problems"
    SHORTNESS_OF_BREATH = "shortness_of_breath"
    # Gynecological
    SPOTTING = "spotting"
    BLEEDING_BETWEEN_PERIODS = "bleeding_between_periods"
    BLEEDING_AFTER_SEX = "bleeding_after_sex"
    UNUSUAL_DISCHARGE = "unusual_discharge"
    PAIN_DURING_SEX = "pain_during_sex"
    PAIN_URINATING = "pain_urinating"
    PAIN_BOWEL_MOVEMENTS = "pain_bowel_movements"
    # Red-flag / safety symptoms
    PAINKILLERS_NOT_HELPING = "painkillers_not_helping"
    SUDDEN_SEVERE_PAIN = "sudden_severe_pain"
    FEVER = "fever"
    DIZZINESS = "dizziness"
    FAINTING = "fainting"
    LOSS_OF_CONSCIOUSNESS = "loss_of_consciousness"
    SHOULDER_TIP_PAIN = "shoulder_tip_pain"


BLEEDING_SYMPTOMS = {s.value for s in (Symptom.SPOTTING, Symptom.BLEEDING_BETWEEN_PERIODS, Symptom.BLEEDING_AFTER_SEX)}
ASSOCIATED_PAIN_SYMPTOMS = {s.value for s in (Symptom.PAIN_DURING_SEX, Symptom.PAIN_URINATING, Symptom.PAIN_BOWEL_MOVEMENTS)}


def value_of(x) -> str | None:
    """Normalize an enum member or raw string to its string value."""
    if x is None:
        return None
    return x.value if isinstance(x, Enum) else str(x)


class Language(StrEnum):
    AR = "ar"
    EN = "en"


class FindingStatus(StrEnum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


def pain_category(level: int | None) -> str:
    """Map 0–10 pain scale to the PRD's None/Mild/Moderate/Severe buckets."""
    if level is None:
        return "unknown"
    if level <= 0:
        return "none"
    if level <= 3:
        return "mild"
    if level <= 6:
        return "moderate"
    return "severe"
