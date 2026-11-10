"""Reconciler (every WATCHDOG_INTERVAL_SECONDS, default 60): detect stuck attempts and relaunch them.

Two phases, so `stuck` is visible for at least one cycle:

0. queued|retrying (waiting for the first heartbeat) whose Job is missing for more than QUEUED_JOB_TIMEOUT_SECONDS
   (15 min) since launch: the Job is created again for the SAME attempt (no attempt consumed) -> 'requeued'.
1. queued|running|retrying -> stuck when
     * the worker reported outcome=error for the current attempt, or
     * its Job failed (backoffLimit 0, activeDeadlineSeconds 21600) or a running review's Job disappeared, or
     * the heartbeat (for queued/retrying: the launch time) is older than HEARTBEAT_TIMEOUT_SECONDS on TWO
       consecutive passes: the first pass only records overdue_since ('overdue'); a heartbeat in between resets it.
   The attempt row ends (stuck|error), the Job is deleted (propagationPolicy Background), stuck_since=now and
   next_retry_at=now+interval.
2. stuck with next_retry_at <= now -> attempt n+1 of the same budget (review `retrying` until the worker's first
   heartbeat), or, after attempt MAX_ATTEMPTS, `failed` with failure_code MAX_ATTEMPTS_EXCEEDED and an honest
   failure_detail. POST /reviews/{id}/retry then opens a new budget.

An attempt is only consumed when a new Job starts after a failure/stuck; resuming a user pause reuses it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .metrics import RECONCILER_ACTIONS, RECONCILER_DURATION, RECONCILER_RUNS, SCAN_ATTEMPTS, STUCK_DETECTIONS, stuck_label
from .models import FailureCode, Outcome, Review, ReviewAttempt, State
from .service import current_attempt, end_attempt, now, start_attempt
from .state import MAX_ATTEMPTS, classify, next_attempt, transition

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


def _last_error(db, r: Review) -> str:
    a = current_attempt(db, r)
    return (a.error if a is not None and a.error else None) or "stuck"


def mark_stuck(db, r: Review, jobs, reason: str, interval: int, at: datetime | None = None) -> None:
    a = current_attempt(db, r)
    if a is not None and a.ended_at is None:
        end_attempt(db, r, Outcome.stuck, reason)
    if a is not None and a.job_name:
        try:
            jobs.delete(a.job_name)
        except Exception:  # noqa: BLE001 - the launcher replaces a lingering Job on relaunch
            log.exception("could not delete job %s", a.job_name)
    transition(r, State.stuck)
    r.stuck_since = at or now()
    r.next_retry_at = r.stuck_since + timedelta(seconds=interval)


def relaunch(db, r: Review, jobs) -> str:
    """stuck -> retrying with attempt n+1 (or failed after MAX_ATTEMPTS). Returns 'relaunched' or 'failed'."""
    last = _last_error(db, r)
    n = next_attempt(r)
    if not n:
        transition(r, State.failed, code=FailureCode.MAX_ATTEMPTS_EXCEEDED,
                   reason=f"{r.attempt} attempts exhausted in budget {r.budget_no}; last error "
                          f"({classify(last).value}): {last}"[:2000])
        jobs.cleanup(r.id, r.budget_no)
        return "failed"
    transition(r, State.retrying)
    r.attempt = n
    try:
        start_attempt(db, r, jobs, mark_running=False)
    except Exception as exc:  # noqa: BLE001 - next sweep sees the errored attempt and moves on to n+1
        log.exception("relaunch of %s attempt %d failed", r.id, n)
        db.add(ReviewAttempt(review_id=r.id, budget_no=r.budget_no, n=n, started_at=now(), ended_at=None,
                             outcome=Outcome.error, error=f"launch failed: {exc}"[:500]))
        SCAN_ATTEMPTS.labels("error").inc()
    return "relaunched"


def _launched_at(r: Review, a) -> datetime | None:
    return _aware(a.started_at if a is not None and a.started_at else None) or _aware(r.last_heartbeat) \
        or _aware(r.created_at)


def sweep(db, jobs, settings, at: datetime | None = None) -> dict[str, str]:
    """One reconciler pass, instrumented (reconciler_runs_total / reconciler_run_duration_seconds)."""
    t0 = time.perf_counter()
    try:
        acted = _sweep(db, jobs, settings, at)
    except Exception:
        RECONCILER_RUNS.labels("error").inc()
        raise
    finally:
        RECONCILER_DURATION.observe(time.perf_counter() - t0)
    RECONCILER_RUNS.labels("ok").inc()
    return acted


def _sweep(db, jobs, settings, at: datetime | None = None) -> dict[str, str]:
    """One reconciler pass. Returns {review_id: requeued|overdue|stuck|relaunched|failed} for reviews acted on."""
    at = at or datetime.now(timezone.utc)
    acted: dict[str, str] = {}
    rows = db.scalars(select(Review).where(Review.state.in_(
        [State.queued, State.running, State.retrying, State.stuck])).with_for_update(skip_locked=True)).all()
    for r in rows:
        st = State(r.state)
        if st == State.stuck:
            due = _aware(r.next_retry_at)
            if due is None or due <= at:
                acted[r.id] = relaunch(db, r, jobs)
            continue
        a = current_attempt(db, r)
        status = jobs.status(a.job_name) if a is not None and a.job_name else "missing"
        errored = a is not None and a.outcome == Outcome.error
        if st in (State.queued, State.retrying) and status == "missing" and not errored:
            launched = _launched_at(r, a)
            if launched is None or at - launched > timedelta(seconds=settings.queued_job_timeout_seconds):
                log.warning("review %s %s without a Job: recreating attempt %d-%d", r.id, st.value, r.budget_no, r.attempt)
                try:
                    start_attempt(db, r, jobs, mark_running=False)  # same (budget, attempt): not counted
                    acted[r.id] = "requeued"
                except Exception:  # noqa: BLE001 - try again next pass
                    log.exception("recreating the Job of %s failed", r.id)
            continue
        reason = stuck_reason(r, a, status, at, settings.heartbeat_timeout_seconds)
        if not (errored or status in ("failed", "missing")):  # heartbeat threshold: two consecutive passes
            if reason is None:
                r.overdue_since = None
            elif r.overdue_since is None:  # first pass over the threshold: wait for a second one
                r.overdue_since = at
                acted[r.id] = "overdue"
                continue
        if reason:
            log.warning("review %s attempt %d-%d stuck: %s", r.id, r.budget_no, r.attempt, reason)
            mark_stuck(db, r, jobs, reason, settings.watchdog_interval_seconds, at)
            STUCK_DETECTIONS.labels(stuck_label(reason)).inc()
            acted[r.id] = "stuck"
    db.commit()
    for action in acted.values():
        RECONCILER_ACTIONS.labels(action).inc()
    return acted


async def watchdog_loop(session_factory, jobs, settings):
    while True:
        try:
            with session_factory() as db:
                sweep(db, jobs, settings)
        except Exception:  # pragma: no cover - keep loop alive
            log.exception("watchdog pass failed")
        await asyncio.sleep(settings.watchdog_interval_seconds)
