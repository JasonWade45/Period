"""Test configuration.

Integration tests need PostgreSQL. Provide TEST_DATABASE_URL, or install the
dev requirements (`pgserver`) and an embedded PostgreSQL is started
automatically. Unit tests need no database.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("AUDIT_IP_SALT", "test-salt-" + "y" * 40)
os.environ["GROK_API_KEY"] = ""  # never call the real API from tests


def _database_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    try:
        import pgserver  # type: ignore
    except ImportError:
        return None
    datadir = os.environ.get("PGSERVER_DATA") or os.path.join(tempfile.gettempdir(), "cyclecare-test-pg")
    srv = pgserver.get_server(datadir, cleanup_mode=None)
    srv.psql("DROP DATABASE IF EXISTS cyclecare_test WITH (FORCE);")
    srv.psql("CREATE DATABASE cyclecare_test;")
    return f"postgresql+psycopg://postgres@/cyclecare_test?host={datadir}"


@pytest.fixture(scope="session")
def database_url():
    url = _database_url()
    if not url:
        pytest.skip("No PostgreSQL available (set TEST_DATABASE_URL or pip install pgserver)")
    os.environ["DATABASE_URL"] = url
    from app.core.config import get_settings
    from app.core.database import reset_engine

    get_settings.cache_clear()
    reset_engine()

    from alembic.config import Config

    from alembic import command

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    cfg.attributes["database_url"] = get_settings().database_url
    command.upgrade(cfg, "head")  # tests the real migrations
    return url


class FakeAI:
    """Stand-in for GrokClient. Queue responses; records every call."""

    model = "fake-grok"

    def __init__(self):
        self.responses: list[str | Exception] = []
        self.calls: list[list[dict]] = []

    def complete(self, messages, *, temperature=0.3, max_tokens=900):
        self.calls.append(messages)
        if not self.responses:
            return "This is an educational explanation of your recorded data. This does not establish a diagnosis."
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def fake_ai():
    return FakeAI()


@pytest.fixture
def client(database_url, fake_ai):
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.ai.grok_client import get_ai_client
    from app.core.database import get_engine
    from app.core.rate_limit import limiter
    from app.main import app

    with get_engine().begin() as conn:
        tables = (
            conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))
            .scalars()
            .all()
        )
        conn.execute(text("TRUNCATE " + ", ".join(tables) + " CASCADE"))
    limiter.reset()
    app.dependency_overrides[get_ai_client] = lambda: fake_ai
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db_session(client):
    """A short-lived session for assertions; always closed so it never blocks TRUNCATE."""
    from app.core.database import get_db

    gen = get_db()
    db = next(gen)
    try:
        yield db
    finally:
        db.rollback()
        gen.close()


def today():
    return datetime.now(UTC).date()


@pytest.fixture
def auth(client):
    """Register a user and return (headers, tokens)."""

    def _make(email="user@example.com", password="correct-horse-battery", language="en", **extra):
        r = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "language": language, "timezone": "UTC", **extra},
        )
        assert r.status_code == 201, r.text
        tokens = r.json()
        return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens

    return _make
