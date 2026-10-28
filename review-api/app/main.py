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
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(reviews.router)
    app.include_router(control.router)
    app.include_router(providers.router)
    app.include_router(misc.router)
    return app


def get_app():  # uvicorn --factory app.main:get_app
    return create_app()
