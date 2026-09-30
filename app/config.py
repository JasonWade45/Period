from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:  # اختياري: لو لم تُثبَّت dotenv نكمل بمتغيرات البيئة الحقيقية فقط
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
DEFAULT_ENV_PATH = REPO_DIR / ".env"

# الرقم الافتراضي هو رقم الإسعاف في مصر. أي نشر خارج مصر يجب أن يضبط
# EMERGENCY_NUMBER حسب بلد المستخدمة (انظر .env.example).
DEFAULT_EMERGENCY_NUMBER = "123"

log = logging.getLogger("cyclecare.config")


def load_env_file(path: Path | str | None = None, *, override: bool = False) -> bool:
    """يحمّل ملف .env إن وُجد ويُرجع True عند التحميل.

    متغيرات البيئة الحقيقية تسبق الملف دائمًا (override=False)، حتى لا
    يستبدل ملفٌ محلي إعدادات النشر.
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not env_path.exists():
        return False
    if load_dotenv is None:
        log.warning("python-dotenv غير مثبّت؛ تم تجاهل %s", env_path)
        return False
    load_dotenv(env_path, override=override)
    return True


# يجب أن يسبق تحميل الملف إنشاء Settings، لأن قيم الحقول الافتراضية
# تُقرأ مرة واحدة عند تعريف الـ dataclass.
load_env_file(os.environ.get("ENV_FILE") or None)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # LLM (Groq)
    groq_api_key: str = _env("GROQ_API_KEY")
    groq_model: str = _env("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_base_url: str = _env("GROQ_BASE_URL", "https://api.groq.com")
    llm_temperature: float = float(_env("LLM_TEMPERATURE", "0.2"))
    llm_max_tokens: int = int(_env("LLM_MAX_TOKENS", "1200"))
    # إعدادات إعادة المحاولة عند 413/429 من المزوّد
    llm_max_attempts: int = int(_env("LLM_MAX_ATTEMPTS", "3"))
    # إعادات محاولة SDK نفسه (5xx/429) — الافتراضي في مكتبة groq هو 2
    llm_sdk_max_retries: int = int(_env("LLM_SDK_MAX_RETRIES", "2"))
    llm_retry_backoff_seconds: float = float(_env("LLM_RETRY_BACKOFF_SECONDS", "20"))

    # Safety numbers (يجب التحقق منها حسب بلد المستخدم قبل الإطلاق)
    emergency_number: str = _env("EMERGENCY_NUMBER", DEFAULT_EMERGENCY_NUMBER)
    crisis_line: str = _env("CRISIS_LINE", "")

    # Prompt
    prompt_version: str = _env("PROMPT_VERSION", "v1.2")
    prompt_path: Path = Path(_env("PROMPT_PATH", str(APP_DIR / "prompts" / "system_prompt_v1.2.md")))

    # Data files
    sources_path: Path = Path(_env("SOURCES_PATH", str(APP_DIR / "data" / "sources.json")))
    # مسودات المعرفة: تُحفظ للمراجعة ولا تُستشهد بها افتراضيًا
    draft_sources_path: Path = Path(_env("DRAFT_SOURCES_PATH",
                                         str(APP_DIR / "data" / "sources_draft.json")))
    knowledge_include_drafts: bool = _env("KNOWLEDGE_INCLUDE_DRAFTS", "0") in ("1", "true", "True")
    rules_glossary_path: Path = Path(_env("RULES_GLOSSARY_PATH", str(APP_DIR / "data" / "rules_glossary.json")))

    # Audit
    audit_log_path: Path = Path(_env("AUDIT_LOG_PATH", str(APP_DIR / ".." / "audit" / "responses.jsonl")))

    # RAG (النظام القديم — يُستبدل تدريجيًا بقاعدة المعرفة)
    rag_top_k: int = int(_env("RAG_TOP_K", "5"))

    # ------------------------------------------------------------------ اللغة
    default_locale: str = _env("DEFAULT_LOCALE", "ar")
    supported_locales: str = _env("SUPPORTED_LOCALES", "ar,en")
    default_timezone: str = _env("DEFAULT_TIMEZONE", "Africa/Cairo")
    # الأرقام: western (0-9) أو arabic_indic (٠-٩). الافتراضي غربي حسب البريف.
    digits_style: str = _env("DIGITS_STYLE", "western")
    week_start: str = _env("WEEK_START", "saturday")
    locales_path: Path = Path(_env("LOCALES_PATH", str(REPO_DIR / "locales")))

    # ------------------------------------------ أرقام الطوارئ (قابلة للتحقق)
    # إعداد مصر يجب أن يعيش في الإعدادات/البيئة لا في الكود، فلا تُثبَّت أرقام
    # بلد داخل دالة. القيم القادمة من البريف:
    #   123 إسعاف (متحقّق) | 122 شرطة | 112 موحّد | 105 صحة | 16000 أطفال
    # ⚠️ قيد التحقق قبل الإطلاق: UNIFIED_EMERGENCY=112 لم يُؤكَّد رسميًا،
    #    وخط الأزمة 08008880700 يجب التأكد أنه يعمل قبل عرضه على مستخدمة في خطر
    #    (لذلك DEFAULT لـCRISIS_LINE يبقى فارغًا: رقم ميت في لحظة أزمة أسوأ من
    #     توجيه عام للطوارئ). القيم موثّقة في .env.example مع وسم التحقق.
    emergency_country: str = _env("EMERGENCY_COUNTRY", "EG").upper()
    police_number: str = _env("POLICE_NUMBER", "122")
    unified_emergency: str = _env("UNIFIED_EMERGENCY", "112")
    health_hotline: str = _env("HEALTH_HOTLINE", "105")
    child_helpline: str = _env("CHILD_HELPLINE", "16000")
    unified_emergency_verified: bool = _env("UNIFIED_EMERGENCY_VERIFIED", "0") in ("1", "true", "True")

    # ------------------------------------------------------------ قاعدة المعرفة
    kb_allow_draft: bool = _env("KB_ALLOW_DRAFT", "0") in ("1", "true", "True")
    # sqlite للتطوير المحلي، postgres للنشر (يتطلب migrations/001_kb_pgvector.sql)
    kb_backend: str = _env("KB_BACKEND", "sqlite")
    kb_db_path: Path = Path(_env("KB_DB_PATH", str(REPO_DIR / "data" / "kb.db")))
    kb_source_registry_path: Path = Path(
        _env("KB_SOURCE_REGISTRY", str(REPO_DIR / "data" / "sources_registry.json")))
    kb_embedding_model: str = _env("KB_EMBEDDING_MODEL", "BAAI/bge-m3")
    # local = محوّل حتمي بلا شبكة (اختبار فقط) | sentence-transformers = الإنتاج
    kb_embedding_backend: str = _env("KB_EMBEDDING_BACKEND", "local")
    kb_top_k: int = int(_env("KB_TOP_K", "6"))            # البريف: 4–6
    kb_min_similarity: float = float(_env("KB_MIN_SIMILARITY", "0.30"))
    kb_min_keyword_score: float = float(_env("KB_MIN_KEYWORD_SCORE", "0.34"))
    kb_postgres_dsn: str = _env("DATABASE_URL", "")

    # التتبّع (البيانات المسجّلة)
    db_path: Path = Path(_env("DB_PATH", str(REPO_DIR / "data" / "cyclecare.db")))

    # البلد: يحدّد رقم الطوارئ من app/data/emergency_numbers.json
    country_code: str = _env("COUNTRY_CODE", "EG").upper()

    # المصادقة: إن كانت فارغة فالتطبيق مفتوح (للتطوير المحلي فقط)
    api_key: str = _env("API_KEY")

    # تحديد المعدّل: عدد الطلبات لكل نافذة زمنية لكل مستخدمة/جهاز
    rate_limit_requests: int = int(_env("RATE_LIMIT_REQUESTS", "30"))
    rate_limit_window_seconds: int = int(_env("RATE_LIMIT_WINDOW_SECONDS", "60"))

    # CORS — الواجهة تُخدم من نفس التطبيق، لذا الافتراضي هو «نفس الأصل فقط».
    # أضيفي أصولًا أخرى بفاصلة، أو "*" (غير آمن بدون مصادقة).
    cors_allow_origins: str = _env("CORS_ALLOW_ORIGINS", "")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def emergency_number_is_default(self) -> bool:
        return self.emergency_number == DEFAULT_EMERGENCY_NUMBER

    @property
    def supported_locale_list(self) -> list[str]:
        """اللغات المدعومة من `SUPPORTED_LOCALES` (مفصولة بفاصلة، بحروف صغيرة)."""
        return [loc.strip().lower() for loc in self.supported_locales.split(",") if loc.strip()]

    @property
    def week_start_index(self) -> int:
        """رقم أول أيام الأسبوع (0 = الاثنين … 6 = الأحد) — الافتراضي السبت = 5.

        البريف اختار السبت بداية الأسبوع (السائد في مصر/الخليج)، والإعداد قابل
        للتغيير لكل بلد عبر WEEK_START بدل تثبيته في كود الواجهة.
        """
        names = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                 "friday": 4, "saturday": 5, "sunday": 6}
        return names.get(self.week_start.strip().lower(), 5)


settings = Settings()
