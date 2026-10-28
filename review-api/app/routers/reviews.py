"""/reviews CRUD (contract: createReview, listReviews, getReview)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .. import serialize
from .. import settings_store
from ..auth import current_user
from ..db import get_db
from ..errors import ApiError, conflict, not_found, validation
from ..github import split
from ..models import AIProvider, ProviderKind, Review, State, User
from ..schemas import ReviewCreate
from ..service import start_attempt
from ..state import ACTIVE

router = APIRouter()


def get_review(db, review_id: str, user: User) -> Review:
    try:
        uuid.UUID(review_id)
    except ValueError:
        raise validation("id must be a uuid")
    r = db.get(Review, review_id)
    if r is None or (r.user_id != user.id and getattr(user.role, "value", user.role) != "admin"):
        raise not_found("review")
    return r


def out(db, r: Review) -> dict:
    return serialize.review(r, db.get(AIProvider, r.provider_id) if r.provider_id else None)


def _provider(db, provider_id: str | None, ack_cloud: bool, cfg: dict) -> AIProvider | None:
    pid = provider_id or cfg.get("default_provider_id")
    if not pid:
        return None
    try:
        uuid.UUID(str(pid))
    except ValueError:
        raise validation("provider_id must be a uuid")
    p = db.get(AIProvider, str(pid))
    if p is None or not p.enabled:
        raise not_found("provider")
    if getattr(p.kind, "value", p.kind) == ProviderKind.cloud.value and not ack_cloud:
        raise ApiError(422, "CLOUD_ACK_REQUIRED",
                       "provider is a cloud provider: code will leave the cluster; resend with ack_cloud=true",
                       {"provider_id": p.id})
    return p


@router.post("/reviews", status_code=201)
def create_review(body: ReviewCreate, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    deps = request.app.state.deps
    cfg = settings_store.load(db)
    provider = _provider(db, body.provider_id, body.ack_cloud, cfg)
    owner, name = split(body.repo_url)
    gh = deps["github"]
    info = gh.repo(owner, name)
    if info.size_kb / 1024 > cfg["max_repo_size_mb"]:
        raise validation("repository too large", size_mb=round(info.size_kb / 1024, 1), max_repo_size_mb=cfg["max_repo_size_mb"])
    sha = gh.resolve(owner, name, body.ref)
    dup = db.scalars(select(Review).where(Review.repo_url == body.repo_url, Review.commit_sha == sha,
                                          Review.state.in_(list(ACTIVE)))).first()
    if dup:
        raise conflict("an active review exists for this repo and commit", review_id=dup.id)
    active = db.scalar(select(func.count()).select_from(Review).where(Review.state.in_(
        [State.running, State.retrying, State.queued]))) or 0
    if active >= cfg["max_concurrent_reviews"]:
        raise ApiError(429, "RATE_LIMITED", "too many concurrent reviews", headers={"Retry-After": "60"})
    r = Review(user_id=user.id, repo_url=body.repo_url, repo_private=info.private, ref=body.ref, commit_sha=sha,
               depth=body.depth, provider_id=provider.id if provider else None, state=State.queued,
               attempt=1, budget_no=1)
    db.add(r)
    db.flush()
    start_attempt(db, r, deps["launcher"])
    db.commit()
    return JSONResponse(out(db, r), 201, headers={"Location": f"/reviews/{r.id}"})


@router.get("/reviews")
def list_reviews(state: State | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 user: User = Depends(current_user), db=Depends(get_db)):
    q = select(Review).where(Review.user_id == user.id)
    if state:
        q = q.where(Review.state == state)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Review.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [out(db, r) for r in rows], "page": page, "page_size": page_size, "total": total}


@router.get("/reviews/{id}")
def read_review(id: str, user: User = Depends(current_user), db=Depends(get_db)):
    return out(db, get_review(db, id, user))
