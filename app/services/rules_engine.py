from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from ..schemas import Finding, Severity, UserContext, max_severity_of

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "medical_rules.json"

_SEVERITY_VALUES: frozenset[str] = frozenset(s.value for s in Severity)


class RulesRegistryError(RuntimeError):
    """سجل القواعد ناقص أو فاسد — يُصرَح به فورًا بدل تشغيل عتبات صامتة."""


def load_rules(path: Path | str | None = None) -> dict:
    """قراءة وتصفية app/data/medical_rules.json (MR-001..MR-010).

    كل فشل ي raising بالعربية مع الملف والسبب؛ لا افتراضات صامتة على صحة العتب.
    """
    p = Path(path) if path is not None else RULES_PATH
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesRegistryError(f"تعذّرت قراءة سجل القواعد {p}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RulesRegistryError(f"سجل القواعد ليس JSON صالحًا ({p}): {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RulesRegistryError(f"سجل القواعد {p}: غير مدعوم أو version != 1")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RulesRegistryError(f"سجل القواعد {p}: rules مفقودة أو فارغة")

    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RulesRegistryError(f"سجل القواعد {p}: عنصر ليس كائنًا")
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid.startswith("MR-"):
            raise RulesRegistryError(f"سجل القواعد {p}: id غير صالح: {rid!r}")
        if rid in seen:
            raise RulesRegistryError(f"سجل القواعد {p}: id مكرر {rid}")
        seen.add(rid)

        review = rule.get("clinical_review")
        if not isinstance(review, dict) or not isinstance(review.get("required"), bool):
            raise RulesRegistryError(f"{rid}: clinical_review مفقود أو بلا required")

        code = rule.get("finding_code")
        threshold = rule.get("threshold")
        severity = rule.get("severity")
        if code == "":
            # قاعدة خاملة (MR-009/MR-010): لا عتب ولا خطورة حتى تأتي المواصفات
            if threshold is not None or severity is not None:
                raise RulesRegistryError(f"{rid}: خاملة (finding_code فارغ) لذا threshold/severity يجب أن يكونا null")
            continue
        if not isinstance(code, str):
            raise RulesRegistryError(f"{rid}: finding_code غير صالح: {code!r}")
        if threshold is None and code != "NO_ALERT_PATTERN":
            raise RulesRegistryError(f"{rid}: قاعدة فعّالة ({code}) بلا threshold — لا تُشغَّل عتبات صامتة")
        if not isinstance(severity, dict) or severity.get("base") not in _SEVERITY_VALUES:
            raise RulesRegistryError(f"{rid}: severity.base مفقود أو خارج مستويات النظام")
        escalate = severity.get("escalate_to")
        if escalate is not None and escalate not in _SEVERITY_VALUES:
            raise RulesRegistryError(f"{rid}: severity.escalate_to خارج مستويات النظام")
        if threshold is not None and not isinstance(threshold, dict):
            raise RulesRegistryError(f"{rid}: threshold يجب أن يكون كائنًا أو null")
    return data


_REGISTRY: dict = load_rules()
_BY_ID: dict[str, dict] = {r["id"]: r for r in _REGISTRY["rules"]}


def _threshold(rule_id: str, key: str) -> int:
    """عتبة رقمية من السجل — غيابها فشل صريح لا قيمة افتراضية مخترعة."""
    rule = _BY_ID.get(rule_id)
    if rule is None:
        raise RulesRegistryError(f"قاعدة {rule_id} غائبة من السجل")
    th = rule.get("threshold")
    if not isinstance(th, dict) or key not in th:
        raise RulesRegistryError(f"{rule_id}: عتبة {key!r} مفقودة أو null")
    return int(th[key])


# العتبات كلها مصدرها app/data/medical_rules.json (values معتمدة من المستخدم،
# مأخوذة حرفيًا من المحرك السابق — لا رقم مخترع). القاعدة بعتبة null خاملة.
MIN_CYCLES_FOR_PATTERN = _threshold("MR-007", "min_cycles")
SYMPTOM_WINDOW_DAYS = _threshold("MR-006", "window_days")     # نافذة سجل الأعراض
REPEATED_SEVERE_THRESHOLD = _threshold("MR-006", "severe_logs")
SEVERE_SYMPTOM_LEVEL = _threshold("MR-006", "min_severity")    # الشدة المحتسبة (4-5)

MAX_BLEEDING_DAYS = _threshold("MR-001", "days")
LONG_BLEEDING_DAYS = _threshold("MR-001", "escalate_at")
AMENORRHEA_DAYS = _threshold("MR-002", "days")
LONG_CYCLE_DAYS = _threshold("MR-003", "days")
LONG_CYCLE_ESCALATE_DAYS = _threshold("MR-003", "escalate_at")
SHORT_CYCLE_DAYS = _threshold("MR-004", "days")
IRREGULAR_SPREAD_DAYS = _threshold("MR-005", "monitor_at")
IRREGULAR_SPREAD_HIGH = _threshold("MR-005", "medical_at")

# finding_code → معرّف السجل (ADDITIVE على Finding.rule_id)
RULE_IDS: dict[str, str] = {
    r["finding_code"]: r["id"]
    for r in _REGISTRY["rules"]
    if r.get("finding_code")
}


def _with_rule_ids(findings: list[Finding]) -> list[Finding]:
    for f in findings:
        if f.rule_id is None:
            f.rule_id = RULE_IDS.get(f.rule_code)
    return findings


def _bleeding_lengths(ctx: UserContext) -> list[int]:
    """أطوال النزيف المسجّلة — وليست أطوال الدورات."""
    return [c.bleeding_days for c in ctx.last_cycles if c.bleeding_days]


def cycle_gaps(ctx: UserContext) -> list[int]:
    """أطوال الدورات الفعلية: فروق تواريخ البداية المتتالية.

    تُستخدم للتفاوت (IRREGULAR_CYCLE). لا تُستخدم أطوال النزيف هنا إطلاقًا:
    خلط الاثنين يجعل «تفاوت النزيف» يظهر كأنه «تفاوت دورات» والعكس.
    """
    if ctx.cycle_gaps:
        return [g for g in ctx.cycle_gaps if 1 <= g <= 400]
    dates = sorted({c.start_date for c in ctx.last_cycles if c.start_date})
    gaps: list[int] = []
    for earlier, later in zip(dates, dates[1:]):
        try:
            delta = (date.fromisoformat(later) - date.fromisoformat(earlier)).days
        except ValueError:
            continue
        if 1 <= delta <= 400:
            gaps.append(delta)
    return gaps


def compute_findings(ctx: UserContext, today: date | None = None) -> list[Finding]:
    """محرك القواعد — توليد FINDINGS من البيانات المسجّلة فقط.

    كل finding يحمل evidence من البيانات فعليًا؛ لا استنتاجات بلا أرقام.
    """
    ref = today or date.today()
    findings: list[Finding] = []
    findings.extend(_record_level_findings(ctx, ref))

    if ctx.cycles_recorded < MIN_CYCLES_FOR_PATTERN:
        findings.insert(0, Finding(
            rule_code="INSUFFICIENT_DATA",
            severity=Severity.MONITOR,
            title="بيانات دورات غير كافية لاستنتاج نمط",
            evidence=[f"cycles_recorded={ctx.cycles_recorded}",
                      f"required>={MIN_CYCLES_FOR_PATTERN}"],
        ))
        return _with_rule_ids(findings)

    if ctx.avg_cycle_days:
        if ctx.avg_cycle_days > LONG_CYCLE_DAYS:
            sev = Severity.MEDICAL_REVIEW if ctx.avg_cycle_days >= LONG_CYCLE_ESCALATE_DAYS else Severity.MONITOR
            findings.append(Finding(
                rule_code="LONG_CYCLE",
                severity=sev,
                title="متوسط طول الدورة أطول من المدى الشائع (21–35 يومًا)",
                evidence=[f"avg_cycle_days={ctx.avg_cycle_days}",
                          f"threshold={LONG_CYCLE_DAYS}"],
            ))
        elif ctx.avg_cycle_days < SHORT_CYCLE_DAYS:
            findings.append(Finding(
                rule_code="SHORT_CYCLE",
                severity=Severity.MONITOR,
                title="متوسط طول الدورة أقصر من المدى الشائع (21–35 يومًا)",
                evidence=[f"avg_cycle_days={ctx.avg_cycle_days}",
                          f"threshold={SHORT_CYCLE_DAYS}"],
            ))

    gaps = cycle_gaps(ctx)
    if len(gaps) >= 2:
        spread = max(gaps) - min(gaps)
        if spread >= IRREGULAR_SPREAD_HIGH:
            findings.append(Finding(
                rule_code="IRREGULAR_CYCLE",
                severity=Severity.MEDICAL_REVIEW,
                title="تفاوت كبير بين أطوال الدورات المسجّلة",
                evidence=[f"spread_days={spread}",
                          f"min={min(gaps)}", f"max={max(gaps)}"],
            ))
        elif spread >= IRREGULAR_SPREAD_DAYS:
            findings.append(Finding(
                rule_code="IRREGULAR_CYCLE",
                severity=Severity.MONITOR,
                title="تفاوت بين أطوال الدورات المسجّلة",
                evidence=[f"spread_days={spread}",
                          f"min={min(gaps)}", f"max={max(gaps)}"],
            ))

    if not findings:
        findings.append(Finding(
            rule_code="NO_ALERT_PATTERN",
            severity=Severity.NORMAL,
            title="لم يُرصد نمط يستدعي تنبيهًا في البيانات المسجّلة",
            evidence=[f"cycles_recorded={ctx.cycles_recorded}"],
        ))

    return _with_rule_ids(findings)


def _record_level_findings(ctx: UserContext, ref: date) -> list[Finding]:
    """قواعد تعتمد على سجل واحد أو على آخر سجل — لا تحتاج 3 دورات.

    السبب: نزيف 9 أيام ليس «نمطًا» لكنه معلومة واحدة واضحة تستحق التنبيه،
    وانتظار 3 دورات لتذكيرها بها خطأ. لا تُفعّل هذه القواعد رد الطوارئ
    (MONITOR)، الفلتر يعمل على URGENT/EMERGENCY فقط.
    """
    findings: list[Finding] = []

    long_bleeds = [c for c in ctx.last_cycles
                   if c.bleeding_days and c.bleeding_days > MAX_BLEEDING_DAYS]
    if long_bleeds:
        worst = max(c.bleeding_days for c in long_bleeds)
        findings.append(Finding(
            rule_code="PROLONGED_BLEEDING",
            severity=Severity.MEDICAL_REVIEW if worst >= LONG_BLEEDING_DAYS else Severity.MONITOR,
            title="طول النزيف أطول من المدى الشائع (حتى 7 أيام)",
            evidence=[f"longest_bleeding_days={worst}",
                      f"threshold={MAX_BLEEDING_DAYS}",
                      f"episodes_over_threshold={len(long_bleeds)}"],
        ))

    dated = [c.start_date for c in ctx.last_cycles if c.start_date]
    if dated:
        try:
            last = date.fromisoformat(max(dated))
        except ValueError:
            return findings
        days_since = (ref - last).days
        if days_since >= AMENORRHEA_DAYS:
            findings.append(Finding(
                rule_code="MISSED_PERIOD",
                severity=Severity.MONITOR,
                title="آخر نزيف مسجّل كان قبل 90 يومًا أو أكثر",
                evidence=[f"days_since_last_logged_bleeding={days_since}",
                          f"threshold={AMENORRHEA_DAYS}",
                          f"last_logged={last.isoformat()}",
                          "تفسيران محتملان: دورة متوقفة فعلًا، أو سجل لم يُحدَّث"],
            ))

    return findings


def compute_symptom_findings(symptoms: list[dict], today: date | None = None) -> list[Finding]:
    """نتيجة من سجل الأعراض: تكرار أعراض شديدة (4–5) خلال آخر 90 يومًا.

    القاعدة محافظة ومبنية على البيانات المسجّلة فقط: لا تفسّر سبب الأعراض ولا
    تشخّص، بل ترصد تكرارًا يستحق النقاش مع طبيبة. مستوى MONITOR دائمًا لأن
    الشدة هنا **تقدير المستخدمة نفسها** لا قياس سريري.
    """
    ref = today or date.today()
    cutoff = ref - timedelta(days=SYMPTOM_WINDOW_DAYS)
    severe: list[dict] = []
    for log in symptoms or []:
        if not log.get("severity") or int(log["severity"]) < SEVERE_SYMPTOM_LEVEL:
            continue
        try:
            day = date.fromisoformat(str(log.get("log_date", "")))
        except ValueError:
            continue
        if day >= cutoff:
            severe.append(log)

    if len(severe) < REPEATED_SEVERE_THRESHOLD:
        return []

    names = sorted({str(s["symptom"]) for s in severe})
    dates = sorted(str(s["log_date"]) for s in severe)
    return _with_rule_ids([Finding(
        rule_code="REPEATED_SEVERE_SYMPTOMS",
        severity=Severity.MONITOR,
        title="أعراض شديدة متكررة في السجل خلال آخر 90 يومًا",
        evidence=[f"severe_logs={len(severe)}",
                  f"threshold={REPEATED_SEVERE_THRESHOLD}",
                  f"from={dates[0]}", f"to={dates[-1]}",
                  "symptoms=" + "، ".join(names[:5])],
    )])


def compute_all_findings(ctx: UserContext, symptoms: list[dict] | None = None) -> list[Finding]:
    """دمج نتائج الدورات ونتائج الأعراض (سجل فارغ = لا نتيجة منه)."""
    return compute_findings(ctx) + compute_symptom_findings(symptoms or [])


def max_severity(findings: list[Finding]) -> Severity:
    """أعلى مستوى خطورة بين النتائج — الترتيب من Severity.rank وحده."""
    return max_severity_of([f.severity for f in findings])
