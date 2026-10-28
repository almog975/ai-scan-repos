"""/repos/check and /admin/settings."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from .. import settings_store
from ..auth import current_user, require_admin
from ..db import get_db
from ..errors import not_found, validation
from ..github import split
from ..models import AIProvider, User
from ..schemas import Closed, normalize_github_url

router = APIRouter()


class SettingsBody(Closed):
    default_provider_id: str | None
    max_concurrent_reviews: int = Field(ge=1)
    max_repo_size_mb: int = Field(ge=1)


@router.get("/repos/check")
def check_repo(request: Request, url: str = Query(...), ref: str | None = Query(None, min_length=1, max_length=255),
               _: User = Depends(current_user)):
    try:
        url = normalize_github_url(url)
    except ValueError as e:
        raise validation(str(e))
    gh = request.app.state.deps["github"]
    owner, name = split(url)
    info = gh.repo(owner, name)
    if ref:
        gh.resolve(owner, name, ref)  # 404 when the ref does not exist
    return {"accessible": True, "private": info.private, "ref": ref or info.default_branch,
            "default_branch": info.default_branch, "size_mb": round(info.size_kb / 1024, 2)}


@router.get("/admin/settings")
def get_settings(_: User = Depends(require_admin), db=Depends(get_db)):
    return settings_store.load(db)


@router.put("/admin/settings")
def put_settings(body: SettingsBody, admin: User = Depends(require_admin), db=Depends(get_db)):
    if body.default_provider_id is not None:
        try:
            uuid.UUID(body.default_provider_id)
        except ValueError:
            raise validation("default_provider_id must be a uuid")
        if db.get(AIProvider, body.default_provider_id) is None:
            raise validation("default_provider_id: provider not found")  # contract: 400, not 404
    return settings_store.save(db, body.model_dump(), admin.id)
