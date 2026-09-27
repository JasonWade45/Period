"""Application settings, loaded from environment variables / backend/.env.

Secrets (JWT_SECRET, GROK_API_KEY, DATABASE_URL) live ONLY on the server.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://cyclecare:cyclecare@localhost:5432/cyclecare"

    jwt_secret: str = "dev-insecure-jwt-secret-change-me-please"  # noqa: S105 - dev only; blocked in production
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    audit_ip_salt: str = "dev-insecure-audit-salt-change-me-please"

    grok_api_key: str | None = None
    grok_model: str = "grok-4.7"
    grok_base_url: str = "https://api.x.ai/v1"
    grok_timeout_seconds: float = 30.0

    cors_origins: str = "*"

    # Rate limits (requests per window)
    auth_rate_limit: int = 10
    auth_rate_window_seconds: int = 60
    ai_rate_limit: int = 20
    ai_rate_window_seconds: int = 3600

    @field_validator("database_url")
    @classmethod
    def _force_psycopg_driver(cls, v: str) -> str:
        # Accept plain postgres:// URLs (Heroku/Render style) and use psycopg v3.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def assert_safe_for_production(self) -> None:
        if not self.is_production:
            return
        insecure = [
            name
            for name, value in (("JWT_SECRET", self.jwt_secret), ("AUDIT_IP_SALT", self.audit_ip_salt))
            if not value or "change-me" in value or len(value) < 32
        ]
        if insecure:
            raise RuntimeError(f"Refusing to start in production with insecure secrets: {', '.join(insecure)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
