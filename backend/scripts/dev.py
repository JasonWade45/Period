"""Zero-dependency local dev runner (no Docker needed).

Starts an embedded PostgreSQL (via `pgserver`, from requirements-dev.txt), applies
migrations, seeds the demo account, then runs the API on 0.0.0.0:8000.

    python -m scripts.dev            # from backend/

If DATABASE_URL is already set in the environment/.env, that database is used instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def ensure_database() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    import pgserver  # type: ignore

    datadir = BACKEND / ".pgdata"
    srv = pgserver.get_server(str(datadir), cleanup_mode=None)
    if "cyclecare" not in srv.psql("SELECT datname FROM pg_database;"):
        srv.psql("CREATE DATABASE cyclecare;")
    return f"postgresql+psycopg://postgres@/cyclecare?host={datadir}"


def main() -> None:
    os.chdir(BACKEND)
    env = {**os.environ, "DATABASE_URL": ensure_database()}
    run = lambda *cmd: subprocess.run(cmd, check=True, env=env)  # noqa: E731, S603
    run(sys.executable, "-m", "alembic", "upgrade", "head")
    run(sys.executable, "-m", "scripts.seed_demo")
    port = os.environ.get("PORT", "8000")
    os.execvpe(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port, "--proxy-headers"], env)  # noqa: S104,S606


if __name__ == "__main__":
    main()
