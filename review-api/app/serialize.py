"""ORM -> contract payloads."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import AIProvider, ProviderKind, Review, ReviewAttempt


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


def review(r: Review, provider: AIProvider | None = None) -> dict:
    return {
        "id": r.id, "repo_url": r.repo_url, "ref": r.ref, "commit_sha": r.commit_sha, "depth": _v(r.depth),
        "state": _v(r.state), "attempt": r.attempt, "budget_no": r.budget_no,
        "files_done": r.files_done or 0, "files_total": r.files_total, "findings_count": r.findings_count or 0,
        "failure_reason": r.failure_reason, "provider": provider_ref(provider),
        "created_at": iso(r.created_at), "updated_at": iso(r.updated_at or r.created_at),
        "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
        "links": {"progress": f"/reviews/{r.id}/progress", "report": f"/reviews/{r.id}/report.json"},
    }


def attempt(a: ReviewAttempt) -> dict:
    return {"id": a.id, "review_id": a.review_id, "budget_no": a.budget_no, "n": a.n, "job_name": a.job_name,
            "started_at": iso(a.started_at), "ended_at": iso(a.ended_at), "outcome": _v(a.outcome), "error": a.error}
