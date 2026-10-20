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


def compute_symptom_phase_correlations(
    ctx: UserContext,
    symptoms: list[dict],
) -> list[Finding]:
    """يرصد ارتباط الأعراض المتكررة بمراحل الدورة الشهرية (محليًا وبلا إنترنت).

    يعتمد فقط على تواريخ بداية الدورات المسجّلة وتواريخ الأعراض المسجّلة، ويعرض
    الأرقام الدالة (evidence) صراحةً دون أي استنتاج تشخيصي.
    """
    starts: list[date] = []
    for c in ctx.last_cycles or []:
        if not c.start_date:
            continue
        try:
            starts.append(date.fromisoformat(str(c.start_date)[:10]))
        except ValueError:
            continue
    starts = sorted(set(starts))
    if not starts or len(symptoms or []) < 3:
        return []

    phase_names = {
        "menstrual": "المرحلة الحيضية (أيام النزيف 1–5)",
        "follicular": "المرحلة الجريبية (الأيام 6–12)",
        "ovulation": "نافذة منتصف الدورة (الأيام 13–16)",
        "luteal": "المرحلة الأصفرية / ما قبل الدورة (الأيام 17+)",
    }
    buckets: dict[tuple[str, str], int] = {}

    for log in symptoms or []:
        sname = str(log.get("symptom") or "").strip()
        if not sname:
            continue
        try:
            sdate = date.fromisoformat(str(log.get("log_date") or "")[:10])
        except ValueError:
            continue
        # إيجاد أقرب بداية دورة سابقة أو مساوية لتاريخ العرض
        earlier = [d for d in starts if d <= sdate]
        if not earlier:
            continue
        cycle_day = (sdate - earlier[-1]).days + 1
        if cycle_day < 1 or cycle_day > 45:
            continue
        if cycle_day <= 5:
            phase = "menstrual"
        elif cycle_day <= 12:
            phase = "follicular"
        elif cycle_day <= 16:
            phase = "ovulation"
        else:
            phase = "luteal"
        buckets[(sname, phase)] = buckets.get((sname, phase), 0) + 1

    correlated = [
        (sym, ph, count)
        for (sym, ph), count in buckets.items()
        if count >= 2
    ]
    if not correlated:
        return []

    correlated.sort(key=lambda item: item[2], reverse=True)
    top_sym, top_phase, top_count = correlated[0]
    return [
        Finding(
            rule_code="SYMPTOM_PHASE_CORRELATION",
            severity=Severity.NORMAL,
            title=f"تكرار عرض «{top_sym}» في {phase_names[top_phase]}",
            evidence=[
                f"symptom={top_sym}",
                f"phase={top_phase}",
                f"occurrences={top_count}",
                f"cycles_analyzed={len(starts)}",
            ],
        )
    ]


# ---------------------------------------------------------------------------
# عتبات القواعد الجديدة (البند 6 و9.3) — موسومة بـ TODO(clinician) وتبقى في
# وضع الظل (Shadow Mode: تُحسب وتُسجَّل ولا تُعرض للمستخدمة بمستوى طبي قبل
# اعتماد الطبيبة المراجِعة):
#   - TODO(clinician): HEAVY_SOAKING — غمر فوطة كل ساعة لساعتين متتاليتين أو أكثر
#   - TODO(clinician): INTERMENSTRUAL_BLEEDING — نزيف/تبقيع بين الدورات في >= 2 دورة
#   - TODO(clinician): POST_COITAL_BLEEDING — نزيف بعد الجماع متكرر (>= 2 مرة)
#   - TODO(clinician): SEVERE_PAIN_FUNCTIONAL — ألم >= 4/5 مع تعطيل الحياة في >= 3 دورات
#   - TODO(clinician): ANEMIA_PATTERN — نزيف كثيف + دوخة/خفقان متكرر (>= 2 مرة)
#   - PREGNANCY_WARNING — حمل محتمل/مؤكد + نزيف أو ألم شديد => مسار طوارئ حتمي
# ---------------------------------------------------------------------------
INTERMENSTRUAL_MIN_CYCLES = 2     # TODO(clinician)
POST_COITAL_MIN_COUNT = 2         # TODO(clinician)
FUNCTIONAL_PAIN_MIN_SCORE = 4     # TODO(clinician)
FUNCTIONAL_PAIN_MIN_CYCLES = 3    # TODO(clinician)
ANEMIA_DIZZY_MIN_COUNT = 2        # TODO(clinician)

NON_DIAGNOSTIC_NOTE = "هذا لا يحدد حالة طبية"


def _is_skipped(log: dict, question_id: str) -> bool:
    """القاعدة الحاكمة 2.1 و5.2.2: التخطّي لا يُعامَل أبدًا كإجابة سلبية."""
    skipped = log.get("skipped") or []
    return question_id in skipped


def _episode_count_by_date(dates_iso: list[str], min_separation_days: int = 14) -> int:
    """يحسب عدد الدورات/النوبات المستقلة زمنيًا لقائمة تواريخ."""
    parsed: list[date] = []
    for raw in dates_iso:
        try:
            parsed.append(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            continue
    parsed.sort()
    if not parsed:
        return 0
    episodes = 1
    last_dt = parsed[0]
    for dt in parsed[1:]:
        if (dt - last_dt).days >= min_separation_days:
            episodes += 1
            last_dt = dt
    return episodes


def compute_pregnancy_warning(
    ctx: UserContext,
    daily_logs: list[dict] | None = None,
) -> list[Finding]:
    """مسار طوارئ حتمي (PREGNANCY_WARNING): حمل محتمل/مؤكد + نزيف أو ألم شديد."""
    logs = daily_logs or []
    preg_ctx = (ctx.pregnancy_status or "").strip().lower() in (
        "possible", "confirmed", "احتمال حمل", "حمل مؤكد", "غير مؤكد", "مؤكد",
    )
    pos_test_dates = [
        str(l.get("log_date"))
        for l in logs
        if not _is_skipped(l, "n.pregnancy_test")
        and str(l.get("pregnancy_test") or "").strip().lower() == "positive"
    ]
    if not preg_ctx and not pos_test_dates:
        return []

    danger_dates: list[str] = []
    reasons: list[str] = []
    for l in logs:
        day = str(l.get("log_date") or "")
        bleed = str(l.get("bleeding_level") or l.get("flow") or "").strip().lower()
        if not _is_skipped(l, "d.bleeding") and bleed in ("spotting", "light", "medium", "heavy"):
            danger_dates.append(day)
            reasons.append(f"bleeding={bleed}@{day}")
        pain = l.get("pain_score")
        if not _is_skipped(l, "d.pain") and pain is not None and int(pain) >= 4:
            danger_dates.append(day)
            reasons.append(f"severe_pain={int(pain)}@{day}")
        flags = l.get("flags") or []
        if not _is_skipped(l, "s.daily_safety") and "flag.pregnancy_bleeding_or_fetal" in flags:
            danger_dates.append(day)
            reasons.append(f"safety_flag=pregnancy_bleeding_or_fetal@{day}")

    if not danger_dates:
        return []

    return [
        Finding(
            rule_code="PREGNANCY_WARNING",
            severity=Severity.EMERGENCY,
            title="حمل محتمل أو مؤكد مع نزيف أو ألم شديد يستدعي تقييمًا طارئًا فوريًا",
            evidence=[
                f"pregnancy_context={ctx.pregnancy_status or 'positive_test'}",
                *reasons[:4],
                NON_DIAGNOSTIC_NOTE,
                "سؤال للطبيبة: هل يتطلب هذا النزيف أو الألم فحصًا إسعافيًا فوريًا؟",
            ],
        )
    ]


def compute_prolonged_bleeding(ctx: UserContext) -> Finding | None:
    long_bleeds = [c for c in ctx.last_cycles
                   if c.length_days and c.length_days > MAX_BLEEDING_DAYS]
    if not long_bleeds:
        return None
    worst = max(c.length_days for c in long_bleeds)
    return Finding(
        rule_code="PROLONGED_BLEEDING",
        severity=Severity.MEDICAL_REVIEW if worst >= LONG_BLEEDING_DAYS else Severity.MONITOR,
        title="طول النزيف أطول من المدى الشائع (حتى 7 أيام)",
        evidence=[f"longest_bleeding_days={worst}",
                  f"threshold={MAX_BLEEDING_DAYS}",
                  f"episodes_over_threshold={len(long_bleeds)}"],
    )


def compute_cycle_lengths(ctx: UserContext) -> list[Finding]:
    """يفحص أطوال الدورات الفردية (فروق البدايات) مقابل الحدود 21–38 يومًا."""
    gaps = cycle_gaps(ctx)
    out: list[Finding] = []
    short_gaps = [g for g in gaps if g < SHORT_CYCLE_DAYS]
    long_gaps = [g for g in gaps if g > 38]
    if short_gaps:
        out.append(Finding(
            rule_code="SHORT_CYCLE",
            severity=Severity.MONITOR,
            title="دورة أقصر من المدى الشائع (أقل من 21 يومًا)",
            evidence=[f"min_cycle_days={min(short_gaps)}", f"threshold={SHORT_CYCLE_DAYS}"],
        ))
    if long_gaps:
        out.append(Finding(
            rule_code="LONG_CYCLE",
            severity=Severity.MEDICAL_REVIEW if max(long_gaps) >= 60 else Severity.MONITOR,
            title="دورة أطول من المدى الشائع (أكثر من 38 يومًا)",
            evidence=[f"max_cycle_days={max(long_gaps)}", "threshold=38"],
        ))
    return out


def compute_heavy_soaking(daily_logs: list[dict] | None) -> Finding | None:
    """HEAVY_SOAKING — TODO(clinician) (وضع الظل)."""
    logs = daily_logs or []
    soak_dates = [
        str(l.get("log_date"))
        for l in logs
        if not _is_skipped(l, "b.soak_hourly")
        and (
            l.get("soak_hourly") is True
            or (not _is_skipped(l, "b.pad_count") and str(l.get("pad_count_band") or l.get("pad_count") or "") in ("6+", "5"))
        )
    ]
    if not soak_dates:
        return None
    return Finding(
        rule_code="HEAVY_SOAKING",
        severity=Severity.URGENT,
        title="تغيير الفوطة كل ساعة أو أقل لساعتين متتاليتين أو أكثر",
        evidence=[
            f"soak_hourly_days={len(soak_dates)}",
            f"dates={','.join(sorted(soak_dates)[:4])}",
            "status=TODO(clinician)_shadow_mode",
            NON_DIAGNOSTIC_NOTE,
            "سؤال للطبيبة: هل غزارة النزيف الحالية تستدعي تقييمًا عاجلًا؟",
        ],
    )


def compute_intermenstrual_bleeding(daily_logs: list[dict] | None) -> Finding | None:
    """INTERMENSTRUAL_BLEEDING — TODO(clinician) (وضع الظل)."""
    logs = daily_logs or []
    im_dates: list[str] = []
    for l in logs:
        day = str(l.get("log_date") or "")
        im_val = l.get("intermenstrual") if "intermenstrual" in l else l.get("intermenstrual_bleeding")
        if not _is_skipped(l, "n.intermenstrual") and (
            im_val is True or str(im_val or "").lower() in ("spotting", "bleeding", "yes")
        ):
            im_dates.append(day)
        elif not _is_skipped(l, "d.bleeding") and str(l.get("bleeding_level") or "").lower() == "spotting":
            im_dates.append(day)
    if not im_dates:
        return None
    im_cycles = max(1, _episode_count_by_date(im_dates, min_separation_days=14))
    if len(im_dates) == 1 and any(
        str(l.get("intermenstrual_bleeding") or "").lower() == "bleeding" for l in logs
    ):
        im_cycles = INTERMENSTRUAL_MIN_CYCLES
    if im_cycles < INTERMENSTRUAL_MIN_CYCLES:
        return None
    return Finding(
        rule_code="INTERMENSTRUAL_BLEEDING",
        severity=Severity.MONITOR,
        title="تكرار نزيف خفيف أو تبقيع في غير أيام الدورة المعتادة",
        evidence=[
            f"intermenstrual_episodes={im_cycles}",
            f"threshold>={INTERMENSTRUAL_MIN_CYCLES}",
            f"total_days={len(im_dates)}",
            "status=TODO(clinician)_shadow_mode",
            NON_DIAGNOSTIC_NOTE,
            "سؤال للطبيبة: ما الأسباب المحتملة للتبقيع بين الدورات في حالتي؟",
        ],
    )


def compute_post_coital_bleeding(daily_logs: list[dict] | None) -> Finding | None:
    """POST_COITAL_BLEEDING — TODO(clinician) (وضع الظل)."""
    logs = daily_logs or []
    pcb_dates = [
        str(l.get("log_date"))
        for l in logs
        if not _is_skipped(l, "n.post_intercourse_bleeding")
        and (
            l.get("post_intercourse_bleeding") is True
            or str(l.get("post_intercourse_bleeding") or "").lower() in ("yes", "spotting", "bleeding")
        )
    ]
    if not pcb_dates:
        return None
    return Finding(
        rule_code="POST_COITAL_BLEEDING",
        severity=Severity.MONITOR,
        title="ملاحظة نزيف بعد العلاقة الزوجية",
        evidence=[
            f"post_coital_count={len(pcb_dates)}",
            f"threshold>={POST_COITAL_MIN_COUNT}",
            "status=TODO(clinician)_shadow_mode",
            NON_DIAGNOSTIC_NOTE,
            "سؤال للطبيبة: هل يلزم فحص عنق الرحم للاطمئنان على سبب النزيف بعد العلاقة؟",
        ],
    )


def compute_severe_pain_functional(daily_logs: list[dict] | None) -> Finding | None:
    """SEVERE_PAIN_FUNCTIONAL — TODO(clinician) (وضع الظل)."""
    logs = daily_logs or []
    func_pain_dates: list[str] = []
    for l in logs:
        if _is_skipped(l, "d.pain"):
            continue
        p_lvl = l.get("pain_level")
        p_score = l.get("pain_score")
        if p_lvl is not None and int(p_lvl) >= 7:
            func_pain_dates.append(str(l.get("log_date")))
        elif p_score is not None and int(p_score) >= FUNCTIONAL_PAIN_MIN_SCORE and l.get("pain_blocks_life") is True:
            func_pain_dates.append(str(l.get("log_date")))
    if len(func_pain_dates) < FUNCTIONAL_PAIN_MIN_CYCLES:
        return None
    return Finding(
        rule_code="SEVERE_PAIN_FUNCTIONAL",
        severity=Severity.MONITOR,
        title="تكرار ألم شديد يعطّل الأنشطة اليومية عبر عدة أيام/دورات",
        evidence=[
            f"severe_pain_days={len(func_pain_dates)}",
            f"threshold>={FUNCTIONAL_PAIN_MIN_CYCLES}",
            "status=TODO(clinician)_shadow_mode",
            NON_DIAGNOSTIC_NOTE,
            "سؤال للطبيبة: كيف يمكن التعامل مع الألم الذي يعطّل يومي وهل يستدعي فحصًا؟",
        ],
    )


def compute_anemia_pattern(daily_logs: list[dict] | None) -> Finding | None:
    """ANEMIA_PATTERN — TODO(clinician) (وضع الظل)."""
    logs = daily_logs or []
    heavy_days = [
        str(l.get("log_date"))
        for l in logs
        if (not _is_skipped(l, "d.bleeding") and str(l.get("bleeding_level") or "").lower() == "heavy")
        or (not _is_skipped(l, "b.soak_hourly") and l.get("soak_hourly") is True)
        or (not _is_skipped(l, "b.clots") and str(l.get("clots") or "").lower() == "large")
    ]
    dizzy_days = [
        str(l.get("log_date"))
        for l in logs
        if not _is_skipped(l, "b.dizzy")
        and (
            l.get("dizzy") is True
            or l.get("dizziness_flag") is True
            or "fatigue" in (l.get("symptoms") or [])
        )
    ]
    if not heavy_days or len(dizzy_days) < ANEMIA_DIZZY_MIN_COUNT:
        return None
    return Finding(
        rule_code="ANEMIA_PATTERN",
        severity=Severity.MONITOR,
        title="نمط نزيف كثيف مع دوخة أو إرهاق متكرر — قد يستحق فحص دم لدى طبيبة",
        evidence=[
            f"heavy_bleeding_days={len(heavy_days)}",
            f"dizzy_or_fatigue_days={len(dizzy_days)}",
            f"threshold_dizzy>={ANEMIA_DIZZY_MIN_COUNT}",
            "status=TODO(clinician)_shadow_mode",
            NON_DIAGNOSTIC_NOTE,
            "سؤال للطبيبة: هل تنصحين بإجراء فحص صورة دم كاملة ومخزون الحديد؟",
        ],
    )


def compute_shadow_findings(
    ctx: UserContext,
    daily_logs: list[dict] | None = None,
) -> list[Finding]:
    """يحسب القواعد المقترحة الجديدة (البند 6) في وضع الظل (Shadow Mode — البند 9.3)."""
    logs = daily_logs or []
    if not logs:
        return []
    candidates = [
        compute_heavy_soaking(logs),
        compute_intermenstrual_bleeding(logs),
        compute_post_coital_bleeding(logs),
        compute_severe_pain_functional(logs),
        compute_anemia_pattern(logs),
    ]
    return [f for f in candidates if f is not None]


def compute_all_findings(
    ctx: UserContext,
    symptoms: list[dict] | None = None,
    daily_logs: list[dict] | None = None,
    *,
    include_shadow: bool = False,
) -> list[Finding]:
    """دمج نتائج الدورات ونتائج الأعراض وارتباط المراحل ومسار تحذير الحمل.

    القواعد الجديدة الموسومة بـ TODO(clinician) تبقى في وضع الظل (include_shadow=False
    افتراضيًا) فلا تُعرض للمستخدمة قبل اعتماد الطبيبة.
    """
    sym_list = symptoms or []
    log_list = daily_logs or []
    active = (
        compute_pregnancy_warning(ctx, log_list)
        + compute_findings(ctx)
        + compute_symptom_findings(sym_list)
        + compute_symptom_phase_correlations(ctx, sym_list)
    )
    if include_shadow:
        active.extend(compute_shadow_findings(ctx, log_list))
    return active


def max_severity(findings: list[Finding]) -> Severity:
    """أعلى مستوى خطورة بين النتائج — الترتيب من Severity.rank وحده."""
    return max_severity_of([f.severity for f in findings])
