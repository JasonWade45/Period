"""تنسيق الأرقام والتواريخ — الغريغوري افتراضيًا، والأرقام حسب إعداد المستخدمة.

قرارات مقصودة:
- التقويم الغريغوري افتراضيًا (لا الهجري): البيانات الصحية تُقارَن بمواعيد
  طبية وتقارير معمل، وخلط التقويمين في نفس الشاشة يُنتج أخطاء تاريخ.
- الأرقام: الغربية (0-9) افتراضيًا حسب البريف، مع دعم العربية-الهندية لمن
  تختارها. التحويل للعرض فقط — التخزين والتحقق يبقيان على الأرقام الغربية.
- لا `strftime` بلا منطقة زمنية: التاريخ يُحسب في منطقة المستخدمة (Africa/Cairo
  افتراضيًا) لأن «اليوم» يختلف بين المناطق ويؤثر في قواعد الدورة.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..config import settings
from . import get_translator

WESTERN = "western"
ARABIC_INDIC = "arabic_indic"

_ARABIC_INDIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
# فاصلة الآلاف العربية هي U+066C في الأرقام العربية-الهندية، والفاصلة العادية للغربية.
_ARABIC_THOUSANDS = "\u066c"
WEEKDAY_ORDER: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday",
                                  "friday", "saturday", "sunday")


def to_digits(value: Any, digits_style: str | None = None) -> str:
    """يحوّل أرقام النص للصيغة المطلوبة (بلا فواصل آلاف)."""
    text = str(value)
    style = (digits_style or settings.digits_style or WESTERN).lower()
    if style in (ARABIC_INDIC, "ar", "arabic-indic", "eastern"):
        return text.translate(_ARABIC_INDIC_DIGITS)
    return text


def format_number(value: float | int, digits_style: str | None = None,
                  *, group: bool = True) -> str:
    """رقم منسّق: 12345 → «12٬345» (عربية-هندية) أو «12,345» (غربية)."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    raw = f"{value:,}" if group else f"{value}"
    style = (digits_style or settings.digits_style or WESTERN).lower()
    if style in (ARABIC_INDIC, "ar", "arabic-indic", "eastern"):
        return raw.translate(str.maketrans("0123456789,", "٠١٢٣٤٥٦٧٨٩" + _ARABIC_THOUSANDS))
    return raw


def format_duration_days(days: int, locale: str | None = None,
                         digits_style: str | None = None) -> str:
    """مدة بالأيام بصيغة الجمع الصحيحة: 1 → «يوم واحد»، 2 → «يومان»، 11 → «11 يومًا»."""
    t = get_translator()
    text = t.tn("units.days", days, locale)
    return to_digits(text, digits_style)


def format_date(value: date | datetime | str, locale: str | None = None,
                digits_style: str | None = None, *, with_weekday: bool = False,
                timezone_name: str | None = None) -> str:
    """تاريخ غريغوري بأسماء الشهور العربية/الإنجليزية من ملف الموارد."""
    loc = (locale or settings.default_locale).lower()
    day = _as_date(value, timezone_name)
    t = get_translator()
    months = t.node("dates.months", loc)
    weekdays = t.node("dates.weekdays", loc)
    if not isinstance(months, list) or not isinstance(weekdays, list):
        raise TypeError("أسماء الشهور/الأيام يجب أن تكون قوائم في ملف المورد")

    # ترتيب `date.weekday()` هو ISO (0 = الاثنين) وهو ترتيب ملف المورد؛
    # دوران القائمة لأجل `WEEK_START` خاص بشبكة التقويم في الواجهة لا بتاريخ واحد.
    payload = {
        "day": format_number(day.day, digits_style, group=False),
        "month": months[day.month - 1],
        "year": format_number(day.year, digits_style, group=False),
        "weekday": weekdays[day.weekday()],
    }
    key = "dates.long_with_weekday" if with_weekday else "dates.long"
    return t.t(key, loc, **payload)


def _weekday_names(weekdays: list[str], week_start: str | None = None) -> list[str]:
    """يرتّب أسماء الأيام كما تُعرض حسب `WEEK_START` (السبت افتراضيًا).

    ملف المورد يرتّبها من الاثنين (ترتيب ISO) لأنه ترتيب `date.weekday()`؛
    وترتيب *العرض* يبدأ من `WEEK_START`، وهي مسألة إعداد لا مسألة لغة.
    """
    start = _week_start_index(week_start)
    return [weekdays[(start + i) % 7] for i in range(7)]


def _week_start_index(week_start: str | None) -> int:
    if not week_start:
        return settings.week_start_index
    names = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
    return names.get(week_start.strip().lower(), settings.week_start_index)


def weekday_labels(locale: str | None = None, week_start: str | None = None) -> list[str]:
    """أسماء أيام الأسبوع بترتيب العرض (يبدأ من WEEK_START، السبت افتراضيًا)."""
    t = get_translator()
    weekdays = t.node("dates.weekdays", (locale or settings.default_locale).lower())
    if not isinstance(weekdays, list):
        raise TypeError("dates.weekdays يجب أن تكون قائمة")
    return _weekday_names(weekdays, week_start)


def format_time(value: datetime | str, locale: str | None = None,
                *, hour12: bool = True, digits_style: str | None = None,
                timezone_name: str | None = None) -> str:
    """ساعة 12 أو 24 حسب إعداد المستخدمة (افتراضي 12 في العربية)."""
    moment = _as_datetime(value, timezone_name)
    if hour12:
        hour = moment.hour % 12 or 12
        suffix = "ص" if moment.hour < 12 else "م"
        if (locale or settings.default_locale).lower().startswith("en"):
            suffix = "AM" if moment.hour < 12 else "PM"
        text = (f"{format_number(hour, digits_style, group=False)}:"
                f"{moment.minute:02d} {suffix}")
    else:
        text = (f"{format_number(moment.hour, digits_style, group=False)}:"
                f"{moment.minute:02d}")
    return to_digits(text, digits_style)


def format_relative_days(days_ago: int, locale: str | None = None,
                         digits_style: str | None = None) -> str:
    """«اليوم» / «أمس» / «منذ 5 أيام» بصيغة الجمع العربية الصحيحة.

    اليوم والبارحة لهما لفظ خاص في العربية لا صيغة جمع، واستخدام «منذ يوم»
    لِما حدث أمس يقرأه العربي غريبًا.
    """
    t = get_translator()
    loc = locale or settings.default_locale
    if days_ago == 0:
        return to_digits(t.t("dates.today", loc), digits_style)
    if days_ago == 1:
        return to_digits(t.t("dates.yesterday", loc), digits_style)
    return to_digits(t.tn("dates.ago", days_ago, loc), digits_style)


def days_between(start: date | str, end: date | str | None = None) -> int:
    """فرق الأيام بين تاريخين (يُستخدم في القواعد وفي العرض)."""
    first, second = _as_date(start), _as_date(end or date.today())
    return (second - first).days


def today_in_user_timezone(timezone_name: str | None = None) -> date:
    """«اليوم» في منطقة المستخدمة لا في UTC — يهم لقواعد تأخر الدورة."""
    name = timezone_name or settings.default_timezone
    try:
        zone = ZoneInfo(name)
    except Exception:  # noqa: BLE001 — اسم منطقة غير صالح لا يُسقط الطلب
        zone = timezone.utc
    return datetime.now(zone).date()


def _as_date(value: date | datetime | str, timezone_name: str | None = None) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(_zone(timezone_name)).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value: datetime | str, timezone_name: str | None = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _zone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or settings.default_timezone)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


__all__ = [
    "WESTERN", "ARABIC_INDIC", "to_digits", "format_number", "format_duration_days",
    "format_date", "format_time", "format_relative_days", "days_between",
    "today_in_user_timezone", "weekday_labels", "timedelta",
]
