import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.api.v1 import ai, auth, cycles, export, insights, logs, users
from app.core.config import get_settings
from app.core.database import get_engine
from app.services.medical_rules import load_content, load_rules_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()
settings.assert_safe_for_production()

app = FastAPI(
    title="CycleCare API",
    version="0.1.0",
    description=(
        "Menstrual cycle tracking, deterministic medical safety rules, and a Grok-powered "
        "explanation layer. Tracking, education and triage guidance — NOT diagnosis."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,  # bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")  # health data must not be cached
    return response


api = APIRouter(prefix="/api/v1")
for r in (
    auth.router,
    users.router,
    cycles.router,
    logs.bleeding_router,
    logs.symptoms_router,
    logs.medications_router,
    logs.pregnancy_router,
    insights.analytics_router,
    insights.predictions_router,
    insights.dashboard_router,
    insights.medical_router,
    ai.router,
    export.router,
):
    api.include_router(r)
app.include_router(api)


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def root():
    """Self-contained preview page (no external CDN). API docs remain at /docs."""
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/health", tags=["meta"])
def health():
    db_ok = True
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "ai_configured": bool(settings.grok_api_key),
        "ruleset_version": load_rules_config()["ruleset_version"],
        "content_version": load_content()["content_version"],
    }
