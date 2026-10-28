"""review-api: creates reviews, launches scanner Jobs, serves reports."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from . import db as dbmod
from .auth import current_user, require_internal
from .config import Settings, get_settings
from .crypto import KeyCipher
from .launcher import make_launcher

log = logging.getLogger(__name__)


class KeyedLauncher:
    """Looks up the review owner's stored AI key and hands it to the real launcher
    as secret env (K8s: per-review Secret; local: process env). Used by the API and the watchdog."""

    def __init__(self, inner, settings):
        self.inner, self.s = inner, settings

    def secret_env_for(self, review_id: str) -> dict | None:
        with dbmod.session_factory()() as db:
            r = db.get(Review, review_id)
            if not r or not r.ai_provider:
                return None
            k = db.scalars(select(AIKey).where(AIKey.owner_id == r.owner_id, AIKey.provider == r.ai_provider)).first()
            env = {"AI_PROVIDER": r.ai_provider}
            if k:
                try:
                    env["AI_API_KEY"] = KeyCipher(self.s.ai_keys_encryption_key).decrypt(k.encrypted_key)
                except ValueError:
                    log.error("cannot decrypt AI key for review %s", review_id)
            return env

    def launch(self, review_id: str, attempt: int) -> str:
        return self.inner.launch(review_id, attempt, secret_env=self.secret_env_for(review_id))

    def cleanup(self, review_id: str) -> None:
        self.inner.cleanup(review_id)
from .metrics import REVIEWS_COMPLETED, REVIEWS_CREATED, REVIEWS_FAILED, REVIEWS_RUNNING
from .models import AIKey, Review, ReviewStatus
from .schemas import AIKeyIn, ProgressIn, ReviewCreate, ReviewOut
from .watchdog import watchdog_loop

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def create_app(settings: Settings | None = None, launcher=None, start_watchdog: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    dbmod.init_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        dbmod.Base.metadata.create_all(dbmod.get_engine())  # postgres uses Alembic
    base_launcher = launcher or make_launcher(settings)
    launcher = KeyedLauncher(base_launcher, settings)
    run_wd = settings.watchdog_enabled if start_watchdog is None else start_watchdog

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(watchdog_loop(dbmod.session_factory(), launcher, settings)) if run_wd else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="AI Scan Repos — review-api", version="0.1.0", lifespan=lifespan)
    app.state.launcher = launcher
    app.dependency_overrides[get_settings] = lambda: settings
    get_db = dbmod.get_db

    def _get(db, review_id: str, user: str | None = None) -> Review:
        r = db.get(Review, review_id)
        if not r or (user is not None and r.owner_id != user):
            raise HTTPException(404, "review not found")
        return r

    def _report_dir(review_id: str) -> Path:
        return Path(settings.reports_dir) / review_id

    # ---- probes / metrics ----
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(db=Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(503, "database not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics(db=Depends(get_db)):
        REVIEWS_RUNNING.set(db.scalar(select(func.count()).select_from(Review).where(Review.status == ReviewStatus.running)) or 0)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- reviews (called by gateway with X-Internal-Token + X-User-Id) ----
    @app.post("/reviews", response_model=ReviewOut, status_code=201)
    def create_review(body: ReviewCreate, user: str = Depends(current_user), db=Depends(get_db)):
        r = Review(owner_id=user, repo_url=body.repo_url, depth=body.depth, ai_provider=body.ai_provider,
                   status=ReviewStatus.running, attempts=1, last_heartbeat=datetime.now(timezone.utc))
        db.add(r)
        db.commit()
        r.job_name = launcher.launch(r.id, 1)
        db.commit()
        REVIEWS_CREATED.labels(depth=body.depth.value).inc()
        return r

    @app.get("/reviews", response_model=list[ReviewOut])
    def list_reviews(user: str = Depends(current_user), db=Depends(get_db)):
        return db.scalars(select(Review).where(Review.owner_id == user).order_by(Review.created_at.desc())).all()

    @app.get("/reviews/{review_id}", response_model=ReviewOut)
    def get_review(review_id: str, user: str = Depends(current_user), db=Depends(get_db)):
        return _get(db, review_id, user)

    @app.post("/reviews/{review_id}/pause", response_model=ReviewOut)
    def pause(review_id: str, user: str = Depends(current_user), db=Depends(get_db)):
        r = _get(db, review_id, user)
        if r.status not in (ReviewStatus.running, ReviewStatus.queued):
            raise HTTPException(409, f"cannot pause a {r.status.value} review")
        r.status = ReviewStatus.paused
        d = _report_dir(r.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "PAUSE").write_text("1")  # worker polls this file, checkpoints and exits
        db.commit()
        return r

    @app.post("/reviews/{review_id}/resume", response_model=ReviewOut)
    def resume(review_id: str, user: str = Depends(current_user), db=Depends(get_db)):
        r = _get(db, review_id, user)
        if r.status != ReviewStatus.paused:
            raise HTTPException(409, f"cannot resume a {r.status.value} review")
        (_report_dir(r.id) / "PAUSE").unlink(missing_ok=True)
        r.status = ReviewStatus.running
        r.attempts += 1
        r.last_heartbeat = datetime.now(timezone.utc)
        r.job_name = launcher.launch(r.id, r.attempts)
        db.commit()
        return r

    def _report_file(review_id, user, db, name) -> Path:
        r = _get(db, review_id, user)
        p = _report_dir(r.id) / name
        if r.status != ReviewStatus.completed or not p.is_file():
            raise HTTPException(404, "report not ready")
        return p

    @app.get("/reviews/{review_id}/report")
    def report_json(review_id: str, user: str = Depends(current_user), db=Depends(get_db)):
        return json.loads(_report_file(review_id, user, db, "report.json").read_text())

    @app.get("/reviews/{review_id}/report.md", response_class=PlainTextResponse)
    def report_md(review_id: str, user: str = Depends(current_user), db=Depends(get_db)):
        return _report_file(review_id, user, db, "report.md").read_text()

    # ---- AI keys (encrypted at rest) ----
    @app.put("/ai-keys", status_code=204)
    def put_key(body: AIKeyIn, user: str = Depends(current_user), db=Depends(get_db)):
        cipher = KeyCipher(settings.ai_keys_encryption_key)
        existing = db.scalars(select(AIKey).where(AIKey.owner_id == user, AIKey.provider == body.provider)).first()
        if existing:
            existing.encrypted_key = cipher.encrypt(body.api_key)
        else:
            db.add(AIKey(owner_id=user, provider=body.provider, encrypted_key=cipher.encrypt(body.api_key)))
        db.commit()
        return Response(status_code=204)

    @app.get("/ai-keys")
    def list_keys(user: str = Depends(current_user), db=Depends(get_db)):
        return [{"provider": k.provider, "configured": True}
                for k in db.scalars(select(AIKey).where(AIKey.owner_id == user)).all()]

    # ---- internal: worker heartbeat/progress ----
    @app.get("/internal/reviews/{review_id}", response_model=ReviewOut, dependencies=[Depends(require_internal)])
    def internal_get(review_id: str, db=Depends(get_db)):
        return _get(db, review_id)

    @app.post("/internal/reviews/{review_id}/progress", response_model=ReviewOut,
              dependencies=[Depends(require_internal)])
    def progress(review_id: str, body: ProgressIn, db=Depends(get_db)):
        r = _get(db, review_id)
        if r.status in (ReviewStatus.completed, ReviewStatus.failed):
            return r
        r.progress = body.progress
        r.last_heartbeat = datetime.now(timezone.utc)
        if body.message is not None:
            r.message = body.message
        if body.ai_provider:
            r.ai_provider = body.ai_provider
        if body.report_path:
            r.report_path = body.report_path
        if body.status is not None and not (r.status == ReviewStatus.paused and body.status == ReviewStatus.running):
            r.status = body.status
            if body.status == ReviewStatus.completed:
                r.progress = 100
                REVIEWS_COMPLETED.inc()
            elif body.status == ReviewStatus.failed:
                REVIEWS_FAILED.inc()
        db.commit()
        if r.status in (ReviewStatus.completed, ReviewStatus.failed):
            launcher.cleanup(r.id)
        return r

    return app


def get_app():  # uvicorn --factory app.main:get_app
    return create_app()
