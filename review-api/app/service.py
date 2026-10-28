"""Attempt bookkeeping shared by the API and the watchdog."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .models import Outcome, Review, ReviewAttempt, State
from .state import transition


def now():
    return datetime.now(timezone.utc)


def current_attempt(db, r: Review) -> ReviewAttempt | None:
    return db.scalars(select(ReviewAttempt).where(ReviewAttempt.review_id == r.id, ReviewAttempt.budget_no == r.budget_no,
                                                  ReviewAttempt.n == r.attempt)).first()


def end_attempt(db, r: Review, outcome: Outcome, error: str | None = None) -> None:
    a = current_attempt(db, r)
    if a and a.ended_at is None:
        a.ended_at, a.outcome, a.error = now(), outcome, error


def start_attempt(db, r: Review, launcher, *, mark_running: bool = True) -> ReviewAttempt:
    """Launch the Job for (budget_no, attempt) and record a review_attempts row.
    mark_running=False keeps the review in `retrying` until the worker's first heartbeat."""
    job = launcher.launch(r)
    a = current_attempt(db, r)
    if a is None:
        a = ReviewAttempt(review_id=r.id, budget_no=r.budget_no, n=r.attempt)
        db.add(a)
    a.job_name, a.started_at, a.ended_at, a.outcome, a.error = job, now(), None, None, None
    if mark_running:
        transition(r, State.running)
    r.last_heartbeat = now()
    return a
