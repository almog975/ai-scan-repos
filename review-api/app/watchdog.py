"""Stalled-job watchdog: relaunch from checkpoint, give up after max attempts."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select

from .metrics import REVIEWS_FAILED, REVIEWS_RETRIED
from .models import Review, ReviewStatus

log = logging.getLogger(__name__)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def check_stalled(db, launcher, *, timeout_seconds: int, max_attempts: int,
                  now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> list[str]:
    """One watchdog pass. Returns list of review ids acted on."""
    t = now()
    cutoff = t - timedelta(seconds=timeout_seconds)
    acted = []
    running = db.scalars(select(Review).where(Review.status == ReviewStatus.running)).all()
    for r in running:
        hb = _aware(r.last_heartbeat) or _aware(r.updated_at)
        if hb and hb > cutoff:
            continue
        acted.append(r.id)
        if r.attempts >= max_attempts:
            r.status = ReviewStatus.failed
            r.message = f"no progress for {timeout_seconds}s after {r.attempts} attempts"
            REVIEWS_FAILED.inc()
            if hasattr(launcher, "cleanup"):
                launcher.cleanup(r.id)
            log.warning("review %s failed after %s attempts", r.id, r.attempts)
        else:
            r.attempts += 1
            r.job_name = launcher.launch(r.id, r.attempts)
            r.last_heartbeat = t  # grace period for the new job
            r.message = f"relaunched (attempt {r.attempts}) — resuming from checkpoint"
            REVIEWS_RETRIED.inc()
            log.info("review %s relaunched attempt %s", r.id, r.attempts)
    db.commit()
    return acted


async def watchdog_loop(session_factory, launcher, settings):
    while True:
        try:
            with session_factory() as db:
                check_stalled(db, launcher, timeout_seconds=settings.heartbeat_timeout_seconds,
                              max_attempts=settings.max_attempts)
        except Exception:  # pragma: no cover - keep loop alive
            log.exception("watchdog pass failed")
        await asyncio.sleep(settings.watchdog_interval_seconds)
