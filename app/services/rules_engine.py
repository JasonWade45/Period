from __future__ import annotations

from datetime import date, timedelta

from ..schemas import Finding, Severity, UserContext, max_severity_of

MIN_CYCLES_FOR_PATTERN = 3
SYMPTOM_WINDOW_DAYS = 90          # نافذة النظر في سجل الأعراض
REPEATED_SEVERE_THRESHOLD = 3     # عدد السجلات الشديدة خلال النافذة

# ---------------------------------------------------------------------------
# عتبات مستخلصة من المسودة التثقيفية (app/data/sources_draft.json) — بانتظار
# تثبيت طبي. مُعلَنة هنا صراحةً حتى لا تختلط بأرقام موثّقة:
#   - النزيف الأطول من 7 أيام من معايير النزيف الغزير التي تستحق تقييمًا.
#   - توقف الدورة 3 شهور (90 يومًا) يستحق تقييمًا بعد استبعاد الحمل.
# كلتاهما MONITOR لا MEDICAL_REVIEW، لأن مدخلات المستخدمة نفسها قد تكون ناقصة،
# ولا نملك تمييز «دورة متوقفة فعلًا» عن «سجل لم يُحدَّث منذ مدة».
# ---------------------------------------------------------------------------
MAX_BLEEDING_DAYS = 7
LONG_BLEEDING_DAYS = 10           # تصعيد عند طول نزيف بعيد عن المألوف
AMENORRHEA_DAYS = 90
SHORT_CYCLE_DAYS = 21
LONG_CYCLE_DAYS = 35
IRREGULAR_SPREAD_DAYS = 7
IRREGULAR_SPREAD_HIGH = 14


def _bleeding_lengths(ctx: UserContext) -> list[int]:
    """أطوال النزيف المسجّلة — وليست أطوال الدورات."""
    return [c.length_days for c in ctx.last_cycles if c.length_days]


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
        return findings

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

    return findings


def _record_level_findings(ctx: UserContext, ref: date) -> list[Finding]:
    """قواعد تعتمد على سجل واحد أو على آخر سجل — لا تحتاج 3 دورات.

    السبب: نزيف 9 أيام ليس «نمطًا» لكنه معلومة واحدة واضحة تستحق التنبيه،
    وانتظار 3 دورات لتذكيرها بها خطأ. لا تُفعّل هذه القواعد رد الطوارئ
    (MONITOR)، الفلتر يعمل على URGENT/EMERGENCY فقط.
    """
    findings: list[Finding] = []

    long_bleeds = [c for c in ctx.last_cycles
                   if c.length_days and c.length_days > MAX_BLEEDING_DAYS]
    if long_bleeds:
        worst = max(c.length_days for c in long_bleeds)
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
        if not log.get("severity") or int(log["severity"]) < 4:
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
    return [Finding(
        rule_code="REPEATED_SEVERE_SYMPTOMS",
        severity=Severity.MONITOR,
        title="أعراض شديدة متكررة في السجل خلال آخر 90 يومًا",
        evidence=[f"severe_logs={len(severe)}",
                  f"threshold={REPEATED_SEVERE_THRESHOLD}",
                  f"from={dates[0]}", f"to={dates[-1]}",
                  "symptoms=" + "، ".join(names[:5])],
    )]


def compute_all_findings(ctx: UserContext, symptoms: list[dict] | None = None) -> list[Finding]:
    """دمج نتائج الدورات ونتائج الأعراض (سجل فارغ = لا نتيجة منه)."""
    return compute_findings(ctx) + compute_symptom_findings(symptoms or [])


def max_severity(findings: list[Finding]) -> Severity:
    """أعلى مستوى خطورة بين النتائج — الترتيب من Severity.rank وحده."""
    return max_severity_of([f.severity for f in findings])
