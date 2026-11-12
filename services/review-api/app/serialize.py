"""ORM -> contract payloads."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from .models import AIProvider, ProviderKind, Review, ReviewAttempt, State, User


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _v(x):
    return getattr(x, "value", x)


def provider_ref(p: AIProvider | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "kind": _v(p.kind), "cloud_warning": _v(p.kind) == ProviderKind.cloud.value}


def user_ref(db, user_id: str | None) -> dict | None:
    u = db.get(User, user_id) if db is not None and user_id else None
    return {"id": u.id, "login": u.login} if u else None


def queue_position(db, r: Review) -> int | None:
    """1-based position among queued reviews by created_at (no ETA)."""
    if db is None or _v(r.state) != State.queued.value:
        return None
    ahead = db.scalar(select(func.count()).select_from(Review).where(
        Review.state == State.queued, Review.created_at < r.created_at)) or 0
    return ahead + 1


def review(r: Review, provider: AIProvider | None = None) -> dict:
    db = object_session(r)
    return {
        "id": r.id, "repo_url": r.repo_url, "ref": r.ref, "commit_sha": r.commit_sha, "depth": _v(r.depth),
        "state": _v(r.state), "attempt": r.attempt, "budget_no": r.budget_no,
        "files_done": r.files_done or 0, "files_total": r.files_total, "findings_count": r.findings_count or 0,
        "failure_code": _v(r.failure_code), "failure_detail": r.failure_reason,
        "failure_reason": r.failure_reason,  # deprecated alias of failure_detail
        "queue_position": queue_position(db, r),
        "stuck_since": iso(r.stuck_since), "next_retry_at": iso(r.next_retry_at),
        "paused_at": iso(r.paused_at), "paused_by": user_ref(db, r.paused_by),
        "cancelled_at": iso(r.cancelled_at), "cancelled_by": user_ref(db, r.cancelled_by),
        "provider": provider_ref(provider),
        "created_at": iso(r.created_at), "updated_at": iso(r.updated_at or r.created_at),
        "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
        "links": {"progress": f"/reviews/{r.id}/progress", "report": f"/reviews/{r.id}/report.json"},
    }


def attempt(a: ReviewAttempt) -> dict:
    return {"id": a.id, "review_id": a.review_id, "budget_no": a.budget_no, "n": a.n, "job_name": a.job_name,
            "started_at": iso(a.started_at), "ended_at": iso(a.ended_at), "outcome": _v(a.outcome), "error": a.error}
