from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import MaxBodySizeMiddleware, RequestIdMiddleware

configure_logging(settings.log_level, settings.log_json, echo_sql=settings.db_echo)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Storage directories must exist before the first upload.
    for directory in (settings.storage_local_root, settings.storage_tmp_dir, settings.duckdb_temp_dir):
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("storage_dir_unavailable", extra={"path": directory})

    # Background jobs run in-process, so a restart can strand a dataset in
    # `profiling`. An investigation runs inside one request, so a restart can
    # strand one of those mid-flight too. Reconcile both on the way up.
    try:
        from app.db.session import session_scope
        from app.services.investigation_service import reconcile_stale_investigations
        from app.services.profiling_service import reconcile_stale_jobs

        with session_scope() as db:
            recovered = reconcile_stale_jobs(db)
            if recovered:
                logger.warning("stale_profiling_jobs_reconciled", extra={"count": recovered})
            abandoned = reconcile_stale_investigations(db)
            if abandoned:
                logger.warning("stale_investigations_reconciled", extra={"count": abandoned})
    except Exception:
        # A database that is not ready must not stop the app from booting.
        logger.warning("startup_reconcile_skipped")

    if not settings.encryption_key and settings.is_development:
        logger.warning(
            "ENCRYPTION_KEY is not set; using the development key. "
            "Set a real key before running outside development."
        )

    yield


app = FastAPI(
    title="AI Root Cause Analysis Platform API",
    version="1.0.0",
    description=(
        "Phase 1 - Data Foundation: uploads, dataset metadata, data profiling, "
        "schema validation, KPI selection, and a SQL Server editor.\n\n"
        "**This deployment has no authentication.** Do not expose it publicly."
    ),
    lifespan=lifespan,
)

# Order matters: the body cap must see the request before anything buffers it.
app.add_middleware(
    MaxBodySizeMiddleware,
    max_bytes=settings.max_upload_bytes,
    prefixes=("/api/uploads",),
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

register_exception_handlers(app)
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness_check() -> dict[str, str]:
    from sqlalchemy import text

    from app.db.session import engine
    from app.services.storage_service import get_storage

    checks: dict[str, str] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        get_storage()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error: {type(exc).__name__}"

    checks["status"] = "ok" if all(v == "ok" for k, v in checks.items() if k != "status") else "degraded"
    return checks
