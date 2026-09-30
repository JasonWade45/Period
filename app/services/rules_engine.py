from __future__ import annotations

from ..schemas import Finding, Severity, UserContext

MIN_CYCLES_FOR_PATTERN = 3
SHORT_CYCLE_DAYS = 21
LONG_CYCLE_DAYS = 35
IRREGULAR_SPREAD_DAYS = 7
IRREGULAR_SPREAD_HIGH = 14


def _lengths(ctx: UserContext) -> list[int]:
    return [c.length_days for c in ctx.last_cycles if c.length_days]


def compute_findings(ctx: UserContext) -> list[Finding]:
    """محرك القواعد — توليد FINDINGS من البيانات المسجّلة فقط.

    كل finding يحمل evidence من البيانات فعليًا؛ لا استنتاجات بلا أرقام.
    """
    findings: list[Finding] = []

    if ctx.cycles_recorded < MIN_CYCLES_FOR_PATTERN:
        findings.append(Finding(
            rule_code="INSUFFICIENT_DATA",
            severity=Severity.MONITOR,
            title="بيانات دورات غير كافية لاستنتاج نمط",
            evidence=[f"cycles_recorded={ctx.cycles_recorded}",
                      f"required>={MIN_CYCLES_FOR_PATTERN}"],
        ))
        return findings

    lengths = _lengths(ctx)

    if ctx.avg_cycle_days:
        if ctx.avg_cycle_days > LONG_CYCLE_DAYS:
            sev = Severity.MEDICAL_REVIEW if ctx.avg_cycle_days >= 60 else Severity.MONITOR
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

    if len(lengths) >= 2:
        spread = max(lengths) - min(lengths)
        if spread >= IRREGULAR_SPREAD_HIGH:
            findings.append(Finding(
                rule_code="IRREGULAR_CYCLE",
                severity=Severity.MEDICAL_REVIEW,
                title="تفاوت كبير بين أطوال الدورات المسجّلة",
                evidence=[f"spread_days={spread}",
                          f"min={min(lengths)}", f"max={max(lengths)}"],
            ))
        elif spread >= IRREGULAR_SPREAD_DAYS:
            findings.append(Finding(
                rule_code="IRREGULAR_CYCLE",
                severity=Severity.MONITOR,
                title="تفاوت بين أطوال الدورات المسجّلة",
                evidence=[f"spread_days={spread}",
                          f"min={min(lengths)}", f"max={max(lengths)}"],
            ))

    if not findings:
        findings.append(Finding(
            rule_code="NO_ALERT_PATTERN",
            severity=Severity.NORMAL,
            title="لم يُرصد نمط يستدعي تنبيهًا في البيانات المسجّلة",
            evidence=[f"cycles_recorded={ctx.cycles_recorded}"],
        ))

    return findings


def max_severity(findings: list[Finding]) -> Severity:
    order = [Severity.NORMAL, Severity.MONITOR, Severity.MEDICAL_REVIEW,
             Severity.URGENT, Severity.EMERGENCY]
    if not findings:
        return Severity.NORMAL
    return max((f.severity for f in findings), key=lambda s: order.index(s))
