"""review-api (contract: contracts/review-api.openapi.yaml)."""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import db as dbmod
from . import errors
from .config import Settings, get_settings
from .errors import ApiError

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def create_app(settings: Settings | None = None, **deps) -> FastAPI:
    settings = settings or get_settings()
    dbmod.init_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        from . import models  # noqa: F401
        dbmod.Base.metadata.create_all(dbmod.get_engine())  # postgres uses Alembic
    app = FastAPI(title="AI Scan Repos — review-api", version="0.1.0")
    app.state.settings = settings
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
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def get_app():  # uvicorn --factory app.main:get_app
    return create_app()
