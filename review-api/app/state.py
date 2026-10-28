"""Review state machine (states = common.schema.json#/$defs/state).

attempt = 1..MAX_ATTEMPTS within a budget; POST /retry (only from failed) opens budget_no+1 at attempt 1.
Illegal transitions raise 409 CONFLICT.
"""
from __future__ import annotations
from .metrics import REVIEWS

from datetime import datetime, timezone

from .errors import conflict
from .models import Review, State

MAX_ATTEMPTS = 5
ACTIVE = {State.queued, State.running, State.paused, State.stuck, State.retrying}
TERMINAL = {State.completed, State.failed, State.cancelled}

TRANSITIONS: dict[State, set[State]] = {
    State.queued: {State.running, State.paused, State.cancelled, State.failed},
    State.running: {State.paused, State.stuck, State.completed, State.failed, State.cancelled},
    State.paused: {State.queued, State.running, State.cancelled},
    State.stuck: {State.queued, State.retrying, State.failed, State.cancelled},
    State.retrying: {State.running, State.paused, State.failed, State.cancelled},
    State.failed: {State.retrying},
    State.completed: set(),
    State.cancelled: set(),
}


def can(src: State, dst: State) -> bool:
    return dst in TRANSITIONS[src]


def transition(r: Review, dst: State, *, reason: str | None = None) -> Review:
    src = State(r.state)
    if not can(src, dst):
        raise conflict(f"cannot move review from {src.value} to {dst.value}", state=src.value)
    now = datetime.now(timezone.utc)
    r.state = dst
    REVIEWS.labels(dst.value).inc()
    if dst == State.running and r.started_at is None:
        r.started_at = now
    if dst == State.paused:
        r.paused_at = now
    if dst in TERMINAL:
        r.finished_at = now
    elif r.finished_at is not None:
        r.finished_at = None  # retry reopens a failed review
    if dst == State.failed and reason:
        r.failure_reason = reason
    return r


def next_attempt(r: Review) -> int:
    """Attempt number for a watchdog relaunch inside the current budget (0 if exhausted)."""
    return r.attempt + 1 if r.attempt < MAX_ATTEMPTS else 0


def open_new_budget(r: Review) -> None:
    """POST /retry: failed -> retrying with budget_no+1, attempt 1."""
    transition(r, State.retrying)
    r.budget_no += 1
    r.attempt = 1
    r.failure_reason = None
