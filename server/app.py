"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path so `utils/` and `modules/` are importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.database import Database
from server.deps import set_db
from server.job_manager import JobManager
from server.routers import config as config_router
from server.routers import jobs as jobs_router
from server.routers import memory_workspace as memory_workspace_router
from server.routers import profiles as profiles_router
from server.routers import reports as reports_router
from utils.config import (
    ENV_PATH,
    RESULTS_DIR,
    get_runtime_override_header_name,
    parse_runtime_override_header,
    runtime_config_override,
)
from utils.env_runtime_access import (
    env_runtime_auth_override,
    get_env_runtime_auth_header_name,
    parse_env_runtime_auth_header,
)
from utils.llm import cleanup_stale_multipart_uploads
from utils.logger import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database()
    set_db(db)

    manager = JobManager(db)
    reconciled_jobs = manager.reconcile_orphaned_jobs()
    if reconciled_jobs:
        log.warning("Reconciled %s stale job(s) during startup", reconciled_jobs)

    try:
        stale_uploads = await asyncio.to_thread(cleanup_stale_multipart_uploads)
        if stale_uploads:
            log.warning(
                "Aborted %s stale incomplete R2 multipart upload(s) during startup",
                stale_uploads,
            )
    except Exception as exc:
        log.warning(
            "Failed to clean stale R2 multipart uploads during startup: %s", exc
        )

    yield
    db.close()
    set_db(None)


app = FastAPI(
    title="Paper Agents",
    description="Automated Academic Paper Discovery, Analysis & Interpretation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for dev (Vite dev server on :5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bind_request_runtime_override(request: Request, call_next):
    raw_header = request.headers.get(get_runtime_override_header_name())
    raw_env_auth_header = request.headers.get(get_env_runtime_auth_header_name())
    try:
        override = parse_runtime_override_header(raw_header)
        env_auth = parse_env_runtime_auth_header(
            raw_env_auth_header,
            env_path=ENV_PATH,
            user_agent=request.headers.get("user-agent", ""),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    with env_runtime_auth_override(env_auth):
        with runtime_config_override(override):
            return await call_next(request)

# API routers
app.include_router(config_router.router, prefix="/api")
app.include_router(jobs_router.router, prefix="/api")
app.include_router(profiles_router.router, prefix="/api")
app.include_router(memory_workspace_router.router, prefix="/api")
app.include_router(reports_router.router, prefix="/api")

# Serve report assets (figures)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/results", StaticFiles(directory=str(RESULTS_DIR)), name="results")


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except (FileNotFoundError, StarletteHTTPException) as exc:
            if getattr(exc, "status_code", 404) != 404:
                raise

            request_path = scope.get("path", "").lstrip("/")
            accept = (
                dict(scope.get("headers") or []).get(b"accept", b"").decode("latin-1")
            )
            if request_path in {"api", "results"} or request_path.startswith(
                ("api/", "results/")
            ):
                raise
            if "text/html" not in accept:
                raise
            return await super().get_response("index.html", scope)


# Serve React build (production) — must be last
_WEB_DIST = _PROJECT_ROOT / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(_WEB_DIST), html=True), name="frontend")


@app.get("/")
async def root():
    return {
        "message": "Welcome to the Paper Agents API. Visit /docs for API documentation."
    }
