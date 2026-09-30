from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


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

    # Safety numbers (يجب التحقق منها حسب بلد المستخدم قبل الإطلاق)
    emergency_number: str = _env("EMERGENCY_NUMBER", "123")
    crisis_line: str = _env("CRISIS_LINE", "")

    # Prompt
    prompt_version: str = _env("PROMPT_VERSION", "v1.1")
    prompt_path: Path = Path(_env("PROMPT_PATH", str(APP_DIR / "prompts" / "system_prompt_v1.1.md")))

    # Data files
    sources_path: Path = Path(_env("SOURCES_PATH", str(APP_DIR / "data" / "sources.json")))
    rules_glossary_path: Path = Path(_env("RULES_GLOSSARY_PATH", str(APP_DIR / "data" / "rules_glossary.json")))

    # Audit
    audit_log_path: Path = Path(_env("AUDIT_LOG_PATH", str(APP_DIR / ".." / "audit" / "responses.jsonl")))

    # RAG
    rag_top_k: int = int(_env("RAG_TOP_K", "5"))


settings = Settings()
