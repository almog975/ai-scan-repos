"""review-api (contract: contracts/review-api.openapi.yaml)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import Depends, FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import db as dbmod
from . import errors
from .config import Settings, get_settings
from .errors import ApiError
from .github import HttpGitHub
from .jobs import ReviewJobs
from .launcher import make_launcher
from .crypto import parse_key
from .metrics import install_db_collector, install_request_metrics
from .routers import control, misc, providers, reviews


def create_app(settings: Settings | None = None, **deps) -> FastAPI:
    settings = settings or get_settings()
    dbmod.init_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        from . import models  # noqa: F401
        dbmod.Base.metadata.create_all(dbmod.get_engine())  # postgres uses Alembic
    start_watchdog = deps.pop("start_watchdog", True) and settings.watchdog_enabled

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = None
        if start_watchdog:
            from .watchdog import watchdog_loop
            task = asyncio.create_task(watchdog_loop(dbmod.session_factory(), app.state.deps["launcher"], settings))
        yield
        if task:
            task.cancel()

    app = FastAPI(title="AI Scan Repos — review-api", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    deps.setdefault("github", None) or deps.update(github=HttpGitHub(settings.github_token))
    deps["launcher"] = ReviewJobs(deps.get("launcher") or make_launcher(settings), settings)
    app.state.deps = deps
    app.dependency_overrides[get_settings] = lambda: settings
    errors.install(app)
    install_request_metrics(app)
    install_db_collector(dbmod.session_factory, settings.heartbeat_timeout_seconds)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(db=Depends(dbmod.get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise ApiError(503, "INTERNAL", "database not ready")
        try:
            parse_key(settings.ai_keys_encryption_key)
        except ValueError as e:
            raise ApiError(503, "INTERNAL", f"not ready: {e}")
        # Readiness gates on what serving requests needs (DB + key). The k8s API only matters for launching
        # scans; the watchdog retries launches, so an API blip (or Docker Desktop without API egress) must not
        # pull every review-api pod out of the Service. Its state is reported in checks.kube instead.
        checks = {"db": "ok", "kube": "ok", "scanner_image": "ok"}
        try:
            deps["launcher"].ready()
        except Exception as e:  # noqa: BLE001
            logging.getLogger("review-api").warning("readyz: kubernetes API unavailable: %s", e)
            checks["kube"] = "unavailable"
            if settings.readyz_require_kube:
                raise ApiError(503, "INTERNAL", "job launcher (kubernetes API) not ready")
        if settings.job_launcher == "kubernetes" and settings.scanner_image_problem():
            checks["scanner_image"] = "unset" if not settings.scanner_image.strip() else "invalid"
        degraded = any(v != "ok" for v in checks.values())
        return {"status": "degraded" if degraded else "ready", "checks": checks}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(reviews.router)
    app.include_router(control.router)
    app.include_router(providers.router)
    app.include_router(misc.router)
    return app


def get_app():  # uvicorn --factory app.main:get_app
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper())
    bad = s.problems()
    if bad:  # fail fast: the pod crash-loops with a clear message instead of running with dev defaults
        for m in bad:
            logging.getLogger("review-api").critical("config: %s", m)
        raise SystemExit("invalid production config: " + "; ".join(bad))
    return create_app(s)
