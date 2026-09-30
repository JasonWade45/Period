"""التعريب في الباك-إند: نصوص ثابتة من ملفات موارد + أكواد أخطاء مستقرة.

المبدأ: أي نص يراه المستخدم نهائيًا (ردود الطوارئ، «لا أملك مصدرًا موثوقًا»،
رسائل الأخطاء، ترويسات CSV…) يأتي من `locales/<lang>.json`، لا من نص مكتوب
داخل دالة. السبب ليس الترجمة فقط: النص الطبي/الأمني يجب أن يمرّ بمراجعة بشرية،
ونصٌّ مدفون في الكود لا يخضع لمثل هذه المراجعة.

المفاتيح ثابتة، والترجمة تُختار بـAccept-Language من قائمة `SUPPORTED_LOCALES`
مع خلفية `DEFAULT_LOCALE`.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import settings
from .plural import category

log = logging.getLogger("cyclecare.i18n")

LOCALES_DIR = Path(settings.locales_path)

DEFAULT_LOCALE = settings.default_locale

# أكواد الأخطاء المستقرة: الواجهة تُترجم الكود، ولا تعتمد على نص الرسالة.
ERROR_CODES: dict[str, str] = {
    "auth_missing_key": "error.auth_missing_key",
    "auth_invalid_key": "error.auth_invalid_key",
    "rate_limited": "error.rate_limited",
    "empty_message": "error.empty_message",
    "invalid_request": "error.invalid_request",
    "internal_error": "error.internal_error",
    "no_reliable_source": "error.no_reliable_source",
    "llm_unavailable": "error.llm_unavailable",
}


class MissingTranslation(KeyError):
    """مفتاح غير موجود — خطأ برمجي، لا يُبتلع صامتًا في الإنتاج."""


class Translator:
    """قارئ ملفات الموارد مع دعم الجمع والانحدار للّغة الافتراضية."""

    def __init__(self, locales_dir: Path | str | None = None,
                 default_locale: str | None = None):
        self.dir = Path(locales_dir or LOCALES_DIR)
        self.default_locale = (default_locale or DEFAULT_LOCALE).lower()
        self._cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ تحميل
    def _load(self, locale: str) -> dict[str, Any]:
        if locale not in self._cache:
            path = self.dir / f"{locale}.json"
            if not path.exists():
                log.warning("ملف ترجمة مفقود: %s — سيُرجع المفتاح نفسه", path)
                self._cache[locale] = {}
            else:
                self._cache[locale] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[locale]

    def available_locales(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))

    # --------------------------------------------------------------- استعلامات
    def has(self, key: str, locale: str | None = None) -> bool:
        return self._lookup(self._load((locale or self.default_locale).lower()), key) is not None

    def t(self, key: str, locale: str | None = None, **params: Any) -> str:
        """نص جاهز للعرض. الاستخدام: t("emergency.medical", locale="ar-EG")"""
        loc = (locale or self.default_locale).lower()
        text = self._resolve(key, loc)
        if text is None:
            raise MissingTranslation(f"مفتاح ترجمة مفقود: {key} ({loc})")
        return _interpolate(text, params)

    def tn(self, key: str, count: float | int, locale: str | None = None, **params: Any) -> str:
        """نص جمعي: يقرأ الصيغة المناسبة من `<key>.<category>`.

        ملف المورد يحتوي:  "cycle.days": {"zero": "...", "one": "...", ...}
        ويُختار القسم حسب قواعد CLDR للّغة (لا حسب لغة الواجهة وحدها).
        """
        loc = _normalize_locale(locale or self.default_locale)
        node = self._resolve_node(key, loc)
        if node is None:
            raise MissingTranslation(f"مفتاح ترجمة مفقود: {key} ({loc})")
        if isinstance(node, str):
            return _interpolate(node, {**params, "count": count})

        chosen = category(loc, count)
        text = node.get(chosen) or node.get("other")
        if text is None:
            raise MissingTranslation(f"لا صيغة جمع صالحة للمفتاح {key} ({loc}/{chosen})")
        return _interpolate(text, {**params, "count": count})

    def node(self, key: str, locale: str | None = None) -> Any:
        """عقدة مورد غير نصية (قائمة أسماء شهور مثلًا) — نفس قواعد الانحدار."""
        loc = _normalize_locale(locale or self.default_locale)
        result = self._resolve_node(key, loc)
        if result is None:
            raise MissingTranslation(f"مفتاح ترجمة مفقود: {key} ({loc})")
        return result

    def error(self, code: str, locale: str | None = None, **params: Any) -> str:
        """رسالة خطأ من كود مستقر — الواجهة تتلقّى الكود والنص معًا."""
        key = ERROR_CODES.get(code)
        if key is None:
            key = ERROR_CODES["internal_error"]
        return self.t(key, locale, **params)

    # -------------------------------------------------------------------- داخلي
    def _resolve(self, key: str, locale: str) -> str | None:
        node = self._resolve_node(key, locale)
        return node if isinstance(node, str) else None

    def _resolve_node(self, key: str, locale: str) -> Any:
        candidates = [locale]
        if locale != self.default_locale:
            candidates.append(self.default_locale)
        if "en" not in candidates:
            candidates.append("en")
        for loc in candidates:
            node = self._lookup(self._load(loc), key)
            if node is not None:
                return node
        return None

    @staticmethod
    def _lookup(data: dict[str, Any], key: str) -> Any:
        node: Any = data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


@lru_cache(maxsize=1)
def get_translator() -> Translator:
    return Translator()


def _normalize_locale(locale: str) -> str:
    """`en-US` → `en` إن لم يوجد ملف إقليمي، و`ar-EG` → `ar` كخلفية."""
    loc = (locale or "").strip().lower().replace("_", "-")
    if not loc:
        return DEFAULT_LOCALE
    if loc in settings.supported_locale_list:
        return loc
    return loc.split("-")[0]


def resolve_locale(accept_language: str | None, fallback: str | None = None) -> str:
    """اختيار اللغة من ترويسة Accept-Language.

    تُحترم q-values، وتُطابق البادئة الإقليمية (ar-EG → ar)، وأي لغة غير مدعومة
    تُرجع اللغة الافتراضية — لا نُفاجئ المستخدمة بلغة لم تطلبها.
    """
    supported = settings.supported_locale_list
    if not accept_language:
        return (fallback or DEFAULT_LOCALE).lower() if fallback else DEFAULT_LOCALE

    for part in _parse_accept_language(accept_language):
        if part in supported:
            return part
        base = part.split("-")[0]
        if base in supported:
            return base
    if fallback:
        return fallback.lower()
    return DEFAULT_LOCALE


def _parse_accept_language(header: str) -> list[str]:
    """يرتّب اللغات حسب الجودة: `ar-EG;q=0.9, en;q=0.8` → ["ar-eg", "en"]."""
    entries: list[tuple[float, str]] = []
    for index, raw in enumerate(header.split(",")):
        token = raw.strip()
        if not token:
            continue
        parts = token.split(";")
        tag = parts[0].strip().lower().replace("_", "-")
        quality = 1.0
        for param in parts[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        if tag and quality > 0:
            entries.append((quality, tag, index))          # type: ignore[arg-type]
    entries.sort(key=lambda item: (-item[0], item[2]))
    return [tag for _q, tag, _i in entries]


def _interpolate(text: str, params: dict[str, Any]) -> str:
    """استبدال `{name}` — آمن: المتغيرات غير المعروفة تبقى كما هي."""
    def repl(match: re.Match) -> str:
        name = match.group(1)
        return str(params.get(name, match.group(0)))

    return re.sub(r"\{(\w+)\}", repl, text)


t = get_translator().t
tn = get_translator().tn

__all__ = [
    "Translator", "MissingTranslation", "ERROR_CODES",
    "get_translator", "resolve_locale", "t", "tn",
]
