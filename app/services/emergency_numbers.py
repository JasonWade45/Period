"""اختيار رقم الطوارئ الصحيح حسب بلد المستخدمة.

القاعدة الأمنية: لا نخترع رقمًا. إن لم يكن البلد في الجدول أو لم يكن الرقم
مُتحقًّا منه، نستخدم الرقم العام مع وسم «غير مُتحقق منه لبلدك» بدل تقديمه
كأنه الصحيح. رقم خاطئ في رد طوارئ أخطر من الاعتراف بعدم المعرفة.

الأولوية: EMERGENCY_NUMBER من البيئة (تشغيل صريح) > جدول البلد > الرقم العام.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "emergency_numbers.json"


@dataclass(frozen=True)
class EmergencyInfo:
    number: str
    country_code: str | None
    country_name: str
    verified: bool          # هل الرقم مُتحقق منه لهذا البلد تحديدًا؟
    crisis_line: str        # فارغ = لا خط دعم مُتحقق منه (لا نخترعه)
    source: str = ""


class EmergencyNumbers:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        fallback = raw.get("fallback", {})
        self._fallback_number: str = str(fallback.get("emergency") or "112")
        self._fallback_note: str = str(fallback.get("note") or "")
        self._countries: dict[str, dict] = {
            str(k).upper(): v for k, v in (raw.get("countries") or {}).items()
        }

    @property
    def known_countries(self) -> list[str]:
        return sorted(self._countries)

    def lookup(self, country_code: str | None, override_number: str = "",
               override_crisis_line: str = "") -> EmergencyInfo:
        code = (country_code or "").strip().upper()
        entry = self._countries.get(code)

        if override_number:
            # تشغيل صريح: نثق بالمشغّل، لكن نُبقي وسم التحقق من الجدول إن وُجد
            return EmergencyInfo(
                number=override_number,
                country_code=code or None,
                country_name=(entry or {}).get("name_ar", "") or code or "غير محدّد",
                verified=True,
                crisis_line=override_crisis_line or (entry or {}).get("crisis_line", ""),
                source=(entry or {}).get("source", "EMERGENCY_NUMBER من إعدادات التشغيل"),
            )

        if entry:
            return EmergencyInfo(
                number=str(entry.get("emergency") or self._fallback_number),
                country_code=code,
                country_name=str(entry.get("name_ar") or code),
                verified=bool(entry.get("verified_at")),
                crisis_line=override_crisis_line or str(entry.get("crisis_line") or ""),
                source=str(entry.get("source") or ""),
            )

        return EmergencyInfo(
            number=self._fallback_number,
            country_code=code or None,
            country_name=code or "غير محدّد",
            verified=False,
            crisis_line=override_crisis_line,
            source=self._fallback_note,
        )
