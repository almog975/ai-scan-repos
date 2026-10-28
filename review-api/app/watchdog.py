"""Watchdog: detect stuck attempts and relaunch (max 5 attempts per budget).

A running/retrying review is stuck when
  * its heartbeat is older than HEARTBEAT_TIMEOUT_SECONDS, or
  * its Job failed / disappeared (backoffLimit 0, activeDeadlineSeconds 21600), or
  * the worker reported outcome=error for the current attempt.
Then: attempt row ends (stuck|error), the old Job is deleted (propagationPolicy Background) and attempt n+1
of the same budget is launched (review -> retrying until the worker's first heartbeat). After attempt 5 the
review is `failed`; POST /reviews/{id}/retry opens a new budget.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .models import Outcome, Review, ReviewAttempt, State
from .service import current_attempt, end_attempt, now, start_attempt
from .state import next_attempt, transition

log = logging.getLogger(__name__)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def stuck_reason(r: Review, attempt, job_status: str, now: datetime, timeout: int) -> str | None:
    if attempt is not None and attempt.outcome == Outcome.error:
        return f"attempt error: {attempt.error or 'worker reported error'}"
    if job_status in ("failed", "missing"):
        return f"job {job_status}"
    hb = _aware(r.last_heartbeat) or _aware(r.started_at) or _aware(r.created_at)
    if hb is None or now - hb > timedelta(seconds=timeout):
        return f"no heartbeat for {timeout}s"
    return None


def relaunch(db, r: Review, jobs, reason: str) -> str:
    """stuck handling for one review; returns 'relaunched' or 'failed'."""
    a = current_attempt(db, r)
    if a is not None and a.ended_at is None:
        end_attempt(db, r, Outcome.stuck, reason)
    if a is not None and a.job_name:
        try:
            jobs.delete(a.job_name)
        except Exception:  # noqa: BLE001 - relaunch anyway; the launcher replaces a lingering Job
            log.exception("could not delete job %s", a.job_name)
    if State(r.state) == State.running:
        transition(r, State.stuck, reason=reason)
    n = next_attempt(r)
    if not n:
        transition(r, State.failed, reason=f"{r.attempt} attempts exhausted in budget {r.budget_no}: {reason}")
        jobs.cleanup(r.id)
        return "failed"
    if State(r.state) == State.stuck:
        transition(r, State.retrying)
    r.attempt = n
    try:
        start_attempt(db, r, jobs, mark_running=False)
    except Exception as exc:  # noqa: BLE001 - next sweep sees a missing Job and moves on to n+1
        log.exception("relaunch of %s attempt %d failed", r.id, n)
        db.add(ReviewAttempt(review_id=r.id, budget_no=r.budget_no, n=n, started_at=now(), ended_at=now(),
                             outcome=Outcome.error, error=f"launch failed: {exc}"[:500]))
    return "relaunched"


def sweep(db, jobs, settings, at: datetime | None = None) -> dict[str, str]:
    at = at or datetime.now(timezone.utc)
    acted: dict[str, str] = {}
    rows = db.scalars(select(Review).where(Review.state.in_([State.running, State.retrying]))).all()
    for r in rows:
        a = current_attempt(db, r)
        status = jobs.status(a.job_name) if a is not None and a.job_name else "missing"
        reason = stuck_reason(r, a, status, at, settings.heartbeat_timeout_seconds)
        if reason:
            log.warning("review %s attempt %d-%d stuck: %s", r.id, r.budget_no, r.attempt, reason)
            acted[r.id] = relaunch(db, r, jobs, reason)
    db.commit()
    return acted


async def watchdog_loop(session_factory, jobs, settings):
    while True:
        try:
            with session_factory() as db:
                sweep(db, jobs, settings)
        except Exception:  # pragma: no cover - keep loop alive
            log.exception("watchdog pass failed")
        await asyncio.sleep(settings.watchdog_interval_seconds)
