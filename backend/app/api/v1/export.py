"""Data export (PRD §43, P1 'user owns the data') and doctor report (§65)."""

from __future__ import annotations

import csv
import io
import json
import statistics
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app.ai.service import questions_for_doctor
from app.api.deps import DB, CurrentUser, audit
from app.domain import pain_category
from app.models import (
    AIConversation,
    BleedingLog,
    Cycle,
    HealthProfile,
    MedicalFinding,
    Medication,
    PregnancyTest,
    SymptomLog,
    UserSettings,
)
from app.services.findings_service import evaluate_and_sync
from app.services.medical_rules import load_content

router = APIRouter(prefix="/export", tags=["export"])

_EXCLUDED = {"password_hash", "user_id"}


def _row(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if col.name in _EXCLUDED:
            continue
        v = getattr(obj, col.key)
        out[col.name] = v.isoformat() if hasattr(v, "isoformat") else (str(v) if col.name.endswith("id") and v is not None else v)
    return out


def _collect(db, user) -> dict[str, list[dict]]:
    uid = user.id

    def rows(model, order):
        return [_row(r) for r in db.scalars(select(model).where(model.user_id == uid).order_by(order)).all()]

    convs = db.scalars(select(AIConversation).where(AIConversation.user_id == uid).order_by(AIConversation.created_at)).all()
    return {
        "cycles": rows(Cycle, Cycle.start_date),
        "bleeding_logs": rows(BleedingLog, BleedingLog.log_date),
        "symptom_logs": rows(SymptomLog, SymptomLog.log_date),
        "medications": rows(Medication, Medication.created_at),
        "pregnancy_tests": rows(PregnancyTest, PregnancyTest.test_date),
        "medical_findings": rows(MedicalFinding, MedicalFinding.detected_at),
        "ai_conversations": [{**_row(c), "messages": [_row(m) for m in c.messages]} for c in convs],
    }


@router.get("/health-data")
def export_health_data(request: Request, user: CurrentUser, db: DB, format: Literal["json", "csv"] = "json"):
    """Export ALL of the user's data. JSON = one document; CSV = zip of one file per table."""
    data = _collect(db, user)
    audit(db, request, "export.health_data", user_id=user.id, metadata={"format": format})
    db.commit()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    if format == "json":
        hp = db.get(HealthProfile, user.id)
        st = db.get(UserSettings, user.id)
        doc = {
            "exported_at": datetime.now(UTC).isoformat(),
            "format_version": "1.0",
            "user": {k: v for k, v in _row(user).items() if k != "password_hash"},
            "settings": _row(st) if st else None,
            "health_profile": _row(hp) if hp else None,
            **data,
        }
        return Response(
            content=json.dumps(doc, ensure_ascii=False, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="cyclecare-export-{stamp}.json"'},
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, rows in data.items():
            if name == "ai_conversations":
                rows = [{**{k: v for k, v in c.items() if k != "messages"}, "message_count": len(c["messages"])} for c in rows]
            s = io.StringIO()
            if rows:
                w = csv.DictWriter(s, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for r in rows:
                    w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})
            # UTF-8 BOM so Excel opens Arabic text correctly.
            zf.writestr(f"{name}.csv", "\ufeff" + s.getvalue())
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="cyclecare-export-{stamp}.zip"'},
    )


REPORT_TEXT = {
    "en": {
        "title": "CycleCare Health Summary",
        "period": "Period: last {months} months ({start} – {end})",
        "cycles": "Cycle lengths (days)",
        "average": "Average cycle length",
        "duration": "Bleeding duration",
        "pain": "Pain",
        "findings": "Recorded findings",
        "questions": "Questions",
        "none": "none recorded",
        "days": "days",
        "pain_levels": {"none": "none", "mild": "mild", "moderate": "moderate", "severe": "severe"},
        "disclaimer": "This report summarizes user-entered information and does not constitute a medical diagnosis.",
    },
    "ar": {
        "title": "ملخص CycleCare الصحي",
        "period": "الفترة: آخر {months} أشهر ({start} – {end})",
        "cycles": "أطوال الدورات (بالأيام)",
        "average": "متوسط طول الدورة",
        "duration": "مدة النزيف",
        "pain": "الألم",
        "findings": "الملاحظات المسجلة",
        "questions": "أسئلة للطبيب",
        "none": "لا يوجد",
        "days": "يوم",
        "pain_levels": {"none": "لا يوجد", "mild": "خفيف", "moderate": "متوسط", "severe": "شديد"},
        "disclaimer": "هذا التقرير يلخّص معلومات أدخلتها المستخدمة ولا يُعد تشخيصًا طبيًا.",
    },
}


@router.get("/doctor-report")
def doctor_report(user: CurrentUser, db: DB, months: int = Query(6, ge=1, le=24), format: Literal["json", "text"] = "json"):
    data, evaluation = evaluate_and_sync(db, user)
    db.commit()
    lang = data.language if data.language in ("ar", "en") else "ar"
    t = REPORT_TEXT[lang]
    since = data.today - timedelta(days=round(months * 30.44))
    views = [v for v in data.views if v.start >= since]
    lengths = [v.cycle_length for v in views if v.cycle_length is not None]
    durations = [v.period_days for v in views if v.period_days]
    pains = [s.pain_level for s in data.symptoms if s.day >= since and s.pain_level is not None]
    report = {
        "title": t["title"],
        "language": lang,
        "period": {"months": months, "from": since.isoformat(), "to": data.today.isoformat()},
        "cycle_lengths": lengths,
        "average_cycle_length": round(statistics.fmean(lengths), 1) if lengths else None,
        "bleeding_duration_range": [min(durations), max(durations)] if durations else None,
        "pain_range": [pain_category(min(pains)), pain_category(max(pains))] if pains else None,
        "contraception": data.profile.contraception_type if data.profile else None,
        "findings": [
            {"rule_code": f.rule_code, "severity": f.severity.value, "title": f.title, "summary": f.summary} for f in evaluation.findings
        ],
        "questions": questions_for_doctor(evaluation, data, lang),
        "disclaimer": t["disclaimer"],
        "severity_labels": load_content()["severity_labels"][lang],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    if format == "json":
        return report

    lines = [t["title"], t["period"].format(months=months, start=since.isoformat(), end=data.today.isoformat()), ""]
    sep = "، " if lang == "ar" else ", "
    lines += [f"{t['cycles']}:", sep.join(map(str, lengths)) or t["none"], ""]
    if report["average_cycle_length"] is not None:
        lines += [f"{t['average']}:", f"{report['average_cycle_length']} {t['days']}", ""]
    if durations:
        lines += [f"{t['duration']}:", f"{min(durations)}–{max(durations)} {t['days']}", ""]
    if pains:
        lo, hi = (t["pain_levels"][p] for p in report["pain_range"])
        lines += [f"{t['pain']}:", lo if lo == hi else f"{lo} – {hi}", ""]
    lines += [f"{t['findings']}:"] + ([f"- {f['title']}" for f in report["findings"]] or [t["none"]]) + [""]
    lines += [f"{t['questions']}:"] + [f"{i}. {q}" for i, q in enumerate(report["questions"], 1)] + ["", t["disclaimer"]]
    return PlainTextResponse("\n".join(lines))
