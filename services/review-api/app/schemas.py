"""Pydantic request models (closed, mirroring contracts/review-api.openapi.yaml) + response builders."""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Depth

GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


def normalize_github_url(url: str) -> str:
    url = (url or "").strip()
    m = GITHUB_RE.match(url)
    if not m or m.group(2) in {".", ".."}:
        raise ValueError("repo_url must look like https://github.com/<owner>/<repo>")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewCreate(Closed):
    repo_url: str
    ref: str = Field(default="HEAD", min_length=1, max_length=255)
    depth: Depth
    provider_id: str | None = None
    ack_cloud: bool = False

    @field_validator("repo_url")
    @classmethod
    def _v(cls, v: str) -> str:
        return normalize_github_url(v)
