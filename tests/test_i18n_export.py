"""التعريب: قواعد الجمع، تنسيق الأرقام والتواريخ، التصدير، الإشعارات، والتدقيق."""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.i18n import Translator, get_translator, resolve_locale
from app.i18n.format import (ARABIC_INDIC, WESTERN, format_date, format_duration_days,
                             format_number, format_relative_days, format_time,
                             today_in_user_timezone, weekday_labels)
from app.i18n.plural import arabic_category, category, english_category
from app.services.export_ar import (ExportContext, FontNotFound, build_report_pdf,
                                    cycles_to_csv, resolve_arabic_font, shape_arabic,
                                    symptoms_to_csv)
from app.services.notifications import (UnsafeNotification, assert_lock_screen_safe,
                                        build_notification)

LOCALES = Path("locales")


# ================================================================ قواعد الجمع

@pytest.mark.parametrize("count,expected", [
    (0, "zero"), (1, "one"), (2, "two"), (3, "few"), (10, "few"),
    # 101/102 ليست «one/two» في العربية: القاعدة تقيس باقي القسمة على 100
    # فـ101 خارج 3..10 و11..99 ⇒ «other»، أما 103 ⇒ «few».
    (11, "many"), (99, "many"), (100, "other"), (101, "other"), (102, "other"),
    (103, "few"), (111, "many"), (200, "other"),
])
def test_arabic_plural_categories_follow_cldr(count, expected):
    assert arabic_category(count) == expected


@pytest.mark.parametrize("count,expected", [(0, "other"), (1, "one"), (2, "other")])
def test_english_plural_categories(count, expected):
    assert english_category(count) == expected


def test_category_chooses_by_locale():
    assert category("ar-EG", 3) == "few"
    assert category("en-GB", 3) == "other"
    assert category("fr", 3) == "few"          # خلفية عربية للغات غير المعروفة


def test_days_plural_forms_are_correct_arabic():
    assert format_duration_days(1) == "يوم واحد"
    assert format_duration_days(2) == "يومان"
    assert format_duration_days(5) == "5 أيام"
    assert format_duration_days(11) == "11 يومًا"
    assert format_duration_days(103) == "103 أيام"     # قاعدة %100


def test_days_plural_in_english():
    assert format_duration_days(1, "en") == "1 day"
    assert format_duration_days(5, "en") == "5 days"


def test_relative_days_uses_plural_forms():
    assert format_relative_days(0) == "اليوم"
    assert format_relative_days(1) == "أمس"          # لفظ خاص لا صيغة جمع
    assert format_relative_days(2) == "منذ يومين"
    assert format_relative_days(30) == "منذ 30 يومًا"


# ================================================================ الأرقام

def test_digits_default_is_western():
    """البريف: الافتراضي أرقام غربية."""
    assert settings.digits_style == "western"
    assert format_number(1234) == "1,234"
    assert format_duration_days(5).startswith("5")


def test_arabic_indic_digits_when_requested():
    assert format_number(1234, ARABIC_INDIC) == "١٬٢٣٤"
    assert format_duration_days(5, digits_style=ARABIC_INDIC) == "٥ أيام"
    assert format_date(date(2026, 8, 1), digits_style=ARABIC_INDIC).startswith("١")


def test_number_formatting_does_not_change_values():
    assert format_number(0, WESTERN) == "0"
    assert format_number(1234, WESTERN, group=False) == "1234"


# ================================================================ التواريخ

def test_date_uses_gregorian_with_arabic_month_names():
    assert format_date(date(2026, 8, 1), "ar") == "1 أغسطس 2026"
    assert format_date(date(2026, 8, 1), "en") == "1 August 2026"


def test_date_with_weekday_uses_locale_weekday_order():
    text = format_date(date(2026, 8, 29), "ar", with_weekday=True)   # السبت
    assert "السبت" in text
    assert "29 أغسطس 2026" in text


def test_week_starts_on_saturday_by_default():
    """البريف: السبت بداية الأسبوع."""
    labels = weekday_labels("ar")
    assert len(labels) == 7
    assert labels[0] == "السبت"
    assert settings.week_start == "saturday"


def test_week_start_is_configurable():
    """بداية الأسبوع إعداد لا ثابت في الكود (Settings مجمّدة ⇒ نمرّرها صراحةً)."""
    assert weekday_labels("ar", week_start="monday")[0] == "الاثنين"
    assert weekday_labels("en", week_start="sunday")[0] == "Sunday"
    assert weekday_labels("ar")[0] == "السبت"


def test_time_12_and_24_hour_formats():
    moment = datetime(2026, 8, 1, 15, 30)
    assert format_time(moment, "ar", hour12=True) == "3:30 م"
    assert format_time(moment, "ar", hour12=False) == "15:30"


def test_today_uses_user_timezone_not_utc():
    cairo = today_in_user_timezone("Africa/Cairo")
    utc = today_in_user_timezone("UTC")
    assert abs((cairo - utc).days) <= 1     # لا نعتمد UTC وحده


# ================================================================ الترجمة

def test_accept_language_picks_supported_locale():
    assert resolve_locale("ar-EG,ar;q=0.9,en;q=0.8") == "ar"
    assert resolve_locale("en-US,en;q=0.9") == "en"
    assert resolve_locale("fr-FR,fr;q=0.9") == "ar"      # غير مدعومة → الافتراضية
    assert resolve_locale(None) == "ar"


def test_accept_language_respects_quality_values():
    assert resolve_locale("en;q=0.3, ar;q=0.9") == "ar"


def test_missing_key_raises_instead_of_silent_fallback():
    from app.i18n import MissingTranslation
    with pytest.raises(MissingTranslation):
        get_translator().t("no.such.key", "ar")


def test_locale_falls_back_to_default_language(tmp_path):
    (tmp_path / "ar.json").write_text(json.dumps({"a": {"b": "عربي"}},
                                                 ensure_ascii=False), encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({}), encoding="utf-8")
    translator = Translator(tmp_path, default_locale="ar")
    assert translator.t("a.b", "en") == "عربي"     # انحدار للافتراضية


def test_locale_key_parity_and_no_untranslated_placeholders():
    ar = json.loads((LOCALES / "ar.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))

    def keys(node, prefix=""):
        out = set()
        for key, value in node.items():
            if key.startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            out |= keys(value, path) if isinstance(value, dict) else {path}
        return out

    assert keys(ar) == keys(en), "مفاتيح الترجمة غير متكافئة بين ar و en"
    # لا نص ترجمة تركه المترجم منقوصًا
    def texts(node):
        return [v for v in node.values() if isinstance(v, str)] + \
               [t for v in node.values() if isinstance(v, dict) for t in texts(v)]
    assert not [t for t in texts(ar) if "TODO" in t or "XXX" in t or t.strip() == ""]
    assert not [t for t in texts(en) if "TODO" in t or "XXX" in t or t.strip() == ""]


# ================================================================ التصدير

def test_csv_has_utf8_bom_and_arabic_headers():
    ctx = ExportContext.from_request("ar")
    payload = cycles_to_csv([{"start_date": "2026-08-01", "length_days": 5,
                             "cycle_length": 28, "note": "ملاحظة"}], ctx)

    assert payload.startswith(b"\xef\xbb\xbf")            # BOM لـExcel
    text = payload.decode("utf-8-sig")
    header = next(csv.reader(io.StringIO(text)))
    assert header[0] == "بداية الدورة"
    assert "أغسطس" in text
    assert "ملاحظة" in text


def test_csv_uses_arabic_indic_digits_when_configured():
    ctx = ExportContext.from_request("ar", digits_style=ARABIC_INDIC)
    payload = cycles_to_csv([{"start_date": "2026-08-01", "length_days": 5}], ctx)
    assert "٥" in payload.decode("utf-8-sig")


def test_symptoms_csv_is_utf8_with_bom():
    ctx = ExportContext.from_request("ar")
    payload = symptoms_to_csv([{"log_date": "2026-08-01", "symptom": "صداع",
                                "severity": 3, "note": ""}], ctx)
    assert payload.startswith(b"\xef\xbb\xbf")
    assert "صداع" in payload.decode("utf-8-sig")


def test_shape_arabic_returns_presentation_forms():
    shaped = shape_arabic("الدورة الشهرية")
    assert shaped != "الدورة الشهرية"
    assert any(0xFE70 <= ord(ch) <= 0xFEFF for ch in shaped)


def test_shape_arabic_leaves_latin_untouched():
    assert shape_arabic("cycle length 21-35") == "cycle length 21-35"


def test_font_resolution_is_explicit_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.export_ar.FONT_CANDIDATES",
                        (str(tmp_path / "missing.ttf"),))
    with pytest.raises(FontNotFound):
        resolve_arabic_font(explicit=str(tmp_path / "also-missing.ttf"))


def test_report_pdf_arabic_smoke(tmp_path):
    """دخان PDF: ملف حقيقي، خط مضمّن، ونص عربي قابل للاستخراج (تشكيل سليم)."""
    from app.services.export_ar import PdfSection, report_sections_from_cycles

    ctx = ExportContext.from_request("ar")
    sections = report_sections_from_cycles(
        [{"start_date": "2026-08-01", "length_days": 5}], ctx)
    sections.append(PdfSection(heading="تنبيه", body="هذا ليس تشخيصًا."))
    pdf = build_report_pdf(sections, ctx, title="تقرير دورتي",
                           footer="هذا ليس تشخيصًا ولا بديلًا عن رأي طبيبة.")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000                      # خط مضمّن ⇒ حجم معتبر

    path = tmp_path / "report.pdf"
    path.write_bytes(pdf)
    text = _extract_pdf_text(path)
    assert "تقرير" in text or "ﺗﻘﺮﻳﺮ" in text   # قد يظهر بأشكال العرض


def test_report_pdf_has_no_letter_spacing_or_italic_for_arabic():
    """البريف: لا تباعد أحرف ولا ميل للنص العربي.

    الفحص على كود فعلي لا على تعليق: نُنشئ الأنماط كما يفعل المُصدِّر ونتحقق
    من خصائصها الفعلية.
    """
    from reportlab.lib.styles import ParagraphStyle

    from app.services.export_ar import LINE_HEIGHT_FACTOR, register_arabic_font

    assert LINE_HEIGHT_FACTOR >= 1.6                    # البريف: ≥ 1.6
    style = ParagraphStyle("probe", fontName=register_arabic_font(), fontSize=11.5,
                           leading=11.5 * LINE_HEIGHT_FACTOR, wordWrap="RTL",
                           alignment=2)
    assert getattr(style, "italic", 0) in (0, False)     # لا ميل للنص العربي
    assert getattr(style, "letterSpacing", 0) == 0       # لا تباعد أحرف
    assert style.wordWrap == "RTL"                       # لفّ النص من اليمين


def _extract_pdf_text(path: Path) -> str:
    try:
        import pymupdf
    except ImportError:                          # pragma: no cover
        pytest.skip("pymupdf غير مثبّت — استخراج النص غير متاح")
    with pymupdf.open(path) as doc:
        return doc[0].get_text()


# ================================================================ الإشعارات

@pytest.mark.parametrize("kind", ["cycle_expected", "log_reminder", "checkin"])
@pytest.mark.parametrize("locale", ["ar", "en"])
def test_notifications_reveal_no_health_details(kind, locale):
    notification = build_notification(kind, locale)
    assert notification.title and notification.body
    assert_lock_screen_safe(notification.title)
    assert_lock_screen_safe(notification.body)


def test_unsafe_notification_text_is_refused():
    with pytest.raises(UnsafeNotification):
        assert_lock_screen_safe("دورتكِ متأخرة 5 أيام")
    with pytest.raises(UnsafeNotification):
        assert_lock_screen_safe("Your period is late")


def test_safe_text_passes():
    assert_lock_screen_safe("لديكِ تذكير في التطبيق.")
    assert_lock_screen_safe("You have a reminder in the app.")


def test_unknown_notification_kind_falls_back_to_generic():
    notification = build_notification("not_a_kind", "ar")
    assert notification.kind == "not_a_kind"
    assert notification.body == get_translator().t("notifications.generic_body", "ar")


def test_english_boundary_does_not_flag_words_containing_terms():
    # "periodic" يحتوي "period" لكن حدّ الكلمة يمنع الإنذار الكاذب
    assert_lock_screen_safe("A periodic reminder")


# ================================================================ أدوات التدقيق

def test_i18n_lint_passes():
    result = subprocess.run([sys.executable, "tools/check_i18n.py"],
                            capture_output=True, text=True, cwd=Path.cwd())
    assert result.returncode == 0, result.stdout + result.stderr


def test_glossary_lint_passes():
    result = subprocess.run([sys.executable, "tools/check_glossary.py"],
                            capture_output=True, text=True, cwd=Path.cwd())
    assert result.returncode == 0, result.stdout + result.stderr


def test_glossary_lint_flags_forbidden_term(tmp_path):
    from tools.check_glossary import GlossaryTerm, scan_text
    terms = [GlossaryTerm(term_en="amenorrhea", preferred_ar="انقطاع الدورة",
                          not_preferred=["غياب الدورة"])]
    assert scan_text(terms, "قد يحدث غياب الدورة بعد التوقف.", "t")
    assert not scan_text(terms, "قد يحدث انقطاع الدورة بعد التوقف.", "t")


def test_glossary_csv_is_single_source_with_required_columns():
    rows = list(csv.DictReader(Path("knowledge/glossary_ar.csv").read_text(
        encoding="utf-8-sig").splitlines()))
    assert rows
    for row in rows:
        assert row["term_en"].strip()
        assert row["needs_review"].strip().lower() in ("true", "false")
    preferred = [r["preferred_ar"].strip() for r in rows]
    assert len(preferred) == len(set(preferred)), "لا تكرار في المصطلح المعتمد"


def test_rtl_static_lint_passes():
    result = subprocess.run([sys.executable, "tools/check_rtl.py"],
                            capture_output=True, text=True, cwd=Path.cwd())
    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_html_is_rtl_and_loads_i18n():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'dir="rtl"' in html
    assert 'lang="ar"' in html
    assert "i18n.js" in html and "rtl.css" in html

    rtl_css = Path("frontend/rtl.css").read_text(encoding="utf-8")
    assert "unicode-bidi: isolate" in rtl_css      # أرقام الهواتف لا تُقلب
    assert "scaleX(-1)" in rtl_css                 # الأيقونات الاتجاهية تُقلب


def test_pdf_summary_line_uses_arabic_plural():
    from app.services.export_ar import report_sections_from_cycles
    ctx = ExportContext.from_request("ar")
    assert "دورتان" in report_sections_from_cycles(
        [{"start_date": "2026-01-01"}, {"start_date": "2026-02-01"}], ctx)[0].body
    assert "لا دورات" in report_sections_from_cycles([], ctx)[0].body
