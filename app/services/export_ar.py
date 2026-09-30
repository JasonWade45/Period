"""تصدير عربي: CSV بترميز UTF-8 مع BOM، وPDF عربي (RTL + تشكيل حروف + bidi).

المشكلة التي يحلّها الملف:
- Excel على ويندوز يقرأ CSV بترميز النظام، فيظهر العربي «????» بلا BOM.
- PDF لا يشكّل الحروف العربية تلقائيًا من نص منطقي: التقرير يحتاج إعادة تشكيل
  (arabic_reshaper) + ترتيب ثنائي الاتجاه (python-bidi) + محاذاة يمين + خطًا
  يحتوي أشكال العرض العربية. بدون الثلاثة يخرج النص مقلوبًا أو منفصل الحروف.

⚠️ حدّ مكتبي معروف: reportlab لا يدعم تشكيلًا عربيًا أصليًا (لا HarfBuzz)،
والحل هنا إعادة تشكيل قبل الرسم. أثره: لا يُنتج «كشيدة» ولا يراعي كل حالات
الاتجاه المختلط المعقّدة (أرقام لاتينية داخل جملة عربية). البديل عند الحاجة:
WeasyPrint أو محرّك يعتمد HarfBuzz (انظر README، قسم حدود المكتبات).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..config import settings
from ..i18n import get_translator
from ..i18n.format import (format_date, format_duration_days, format_number,
                           to_digits)

# الخطوط المرشّحة، بالترتيب: إعداد صريح ← IBM Plex Sans Arabic ← Noto Sans Arabic
# ← خط نظام عربي. أي ملف خط عربي يُقبل؛ لا نُثبّت خطًا في المستودع.
FONT_CANDIDATES: tuple[str, ...] = (
    "assets/fonts/IBMPlexSansArabic-Regular.ttf",
    "assets/fonts/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

FONT_NAME = "CycleCareArabic"
LINE_HEIGHT_FACTOR = 1.7          # البريف يطلب ≥ 1.6 للنص العربي


class FontNotFound(RuntimeError):
    """لا خط عربي متاح: نُفشل التصدير صراحةً بدل إخراج مربعات فارغة."""


def resolve_arabic_font(explicit: str | None = None) -> Path:
    """أول خط عربي متاح. لا نخمّن: إن لم يوجد نُخطئ برسالة واضحة."""
    candidates = [explicit] if explicit else []
    candidates += [settings.pdf_arabic_font_path] if settings.pdf_arabic_font_path else []
    candidates += list(FONT_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and path.suffix.lower() in (".ttf", ".otf"):
            return path
    raise FontNotFound(
        "لا يوجد خط عربي للتصدير. أضيفي IBM Plex Sans Arabic أو Noto Sans Arabic "
        "إلى assets/fonts/ أو اضبطي PDF_ARABIC_FONT_PATH."
    )


def register_arabic_font(path: Path | str | None = None) -> str:
    """يسجّل الخط في reportlab ويُرجع اسمه (يُسجّل مرة واحدة لكل مسار)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = Path(path) if path else resolve_arabic_font()
    name = f"{FONT_NAME}-{font_path.stem}"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path)))
    return name


def shape_arabic(text: str) -> str:
    """تشكيل + ترتيب ثنائي الاتجاه للنص العربي.

    النص اللاتيني الخالص يُترك كما هو (إعادة التشكيل لا تفيده وقد تُفسد الرموز).
    الأرقام العربية-الهندية تبقى كما هي؛ التحويل يتم في `to_digits` لا هنا.
    """
    if not text or not any("\u0600" <= ch <= "\u06FF" for ch in text):
        return text
    import arabic_reshaper
    from bidi.algorithm import get_display

    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped, base_dir="R")


@dataclass
class ExportContext:
    """إعدادات العرض للتصدير — تُمرَّر صراحةً فلا يتسرّب إعداد مستخدمة لأخرى."""

    locale: str
    digits_style: str = ""
    timezone: str = ""

    @classmethod
    def from_request(cls, locale: str, digits_style: str | None = None,
                     timezone: str | None = None) -> "ExportContext":
        return cls(locale=locale,
                   digits_style=digits_style or settings.digits_style,
                   timezone=timezone or settings.default_timezone)


# ------------------------------------------------------------------------ CSV

def cycles_to_csv(rows: Iterable[dict[str, Any]], ctx: ExportContext) -> bytes:
    """CSV بترميز UTF-8 مع BOM حتى يفتحه Excel بالعربي صحيحًا.

    الأعمدة تأتي من ملف الموارد (لا نص مكتوب هنا)، والقيم الرقمية تُنسَّق حسب
    إعداد الأرقام، والتواريخ بالتقويم الغريغوري.
    """
    t = get_translator()
    loc = ctx.locale
    headers = [t.t("csv.cycle_start", loc), t.t("csv.bleeding_days", loc),
               t.t("csv.cycle_length", loc), t.t("csv.note", loc)]

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([
            format_date(row["start_date"], loc, ctx.digits_style,
                        timezone_name=ctx.timezone),
            _maybe_number(row.get("length_days"), ctx),
            _maybe_number(row.get("cycle_length"), ctx),
            str(row.get("note") or ""),
        ])

    # \ufeff = BOM: بدونه يقرأ Excel العربي كترميز محلي فيظهر مشوّهًا.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def symptoms_to_csv(rows: Iterable[dict[str, Any]], ctx: ExportContext) -> bytes:
    t = get_translator()
    loc = ctx.locale
    headers = [t.t("csv.log_date", loc), t.t("csv.symptom", loc),
               t.t("csv.severity", loc), t.t("csv.note", loc)]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([
            format_date(row["log_date"], loc, ctx.digits_style, timezone_name=ctx.timezone),
            str(row.get("symptom") or ""),
            _maybe_number(row.get("severity"), ctx),
            str(row.get("note") or ""),
        ])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _maybe_number(value: Any, ctx: ExportContext) -> str:
    if value in (None, ""):
        return ""
    try:
        return format_number(int(value), ctx.digits_style, group=False)
    except (TypeError, ValueError):
        return str(value)


# ------------------------------------------------------------------------ PDF

@dataclass
class PdfSection:
    heading: str
    body: str = ""
    bullets: Sequence[str] = ()


def build_report_pdf(sections: Sequence[PdfSection], ctx: ExportContext, *,
                     title: str = "", footer: str = "",
                     font_path: str | None = None) -> bytes:
    """تقرير PDF عربي RTL: محاذاة يمين، سطور متباعدة، بلا حروف مفردة.

    يُرجع البايتات مباشرة (لا ملف على القرص) حتى يكون الاستدعاء من مسار HTTP
    بسيطًا وبلا آثار جانبية.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = register_arabic_font(font_path)
    t = get_translator()
    loc = ctx.locale

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=title or t.t("app.name", loc),
    )

    # RTL: المحاذاة يمين والاتجاه من اليمين. لا تباعد أحرف (letter-spacing) ولا
    # ميل (italic) — كلاهما يُفسد العربية بصريًا.
    body_style = ParagraphStyle(
        "body-ar", fontName=font, fontSize=11.5, alignment=2,
        leading=11.5 * LINE_HEIGHT_FACTOR, wordWrap="RTL", spaceAfter=4,
    )
    heading_style = ParagraphStyle(
        "h-ar", parent=body_style, fontSize=14.5, leading=14.5 * LINE_HEIGHT_FACTOR,
        spaceBefore=8, spaceAfter=6,
    )
    title_style = ParagraphStyle(
        "title-ar", parent=body_style, fontSize=19, leading=19 * LINE_HEIGHT_FACTOR,
        spaceAfter=14,
    )

    story = []
    if title:
        story.append(Paragraph(_esc(shape_arabic(title)), title_style))
        story.append(Spacer(1, 4))

    for section in sections:
        if section.heading:
            story.append(Paragraph(_esc(shape_arabic(section.heading)), heading_style))
        if section.body:
            story.append(Paragraph(_esc(shape_arabic(section.body)), body_style))
        for bullet in section.bullets:
            story.append(Paragraph(_esc(shape_arabic(f"• {bullet}")), body_style))

    if footer:
        story.append(Spacer(1, 10))
        story.append(Paragraph(_esc(shape_arabic(footer)), body_style))

    doc.build(story)
    return buffer.getvalue()


def report_sections_from_cycles(cycles: Sequence[dict[str, Any]],
                                ctx: ExportContext) -> list[PdfSection]:
    """أقسام التقرير الأساسية من بيانات الدورات المسجّلة."""
    t = get_translator()
    loc = ctx.locale
    rows = []
    for cycle in cycles:
        date_text = format_date(cycle["start_date"], loc, ctx.digits_style,
                                timezone_name=ctx.timezone)
        days = cycle.get("length_days")
        days_text = (format_duration_days(int(days), loc, ctx.digits_style)
                     if days else "")
        rows.append(f"{date_text} — {days_text}".strip(" —"))
    summary = (t.tn("units.cycles", len(cycles), loc) if cycles
               else t.t("units.cycles.zero", loc))
    return [
        PdfSection(heading=t.t("csv.cycle_start", loc),
                   body=f"{t.t('pdf.intro', loc)} ({summary})",
                   bullets=rows[:24]),
        PdfSection(heading=t.t("pdf.disclaimer_heading", loc),
                   body=t.t("app.not_a_diagnosis", loc)),
    ]


def _esc(text: str) -> str:
    """reportlab يقرأ النص كوسوم XML: نهرّب الرموز الخاصة."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


__all__ = [
    "ExportContext", "PdfSection", "FontNotFound", "resolve_arabic_font",
    "register_arabic_font", "shape_arabic", "cycles_to_csv", "symptoms_to_csv",
    "build_report_pdf", "report_sections_from_cycles",
]
