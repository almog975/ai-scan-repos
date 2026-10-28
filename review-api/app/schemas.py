import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Depth, ReviewStatus

GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


def normalize_github_url(url: str) -> str:
    url = (url or "").strip()
    m = GITHUB_RE.match(url)
    if not m or m.group(2) in {".", ".."}:
        raise ValueError("repo_url must look like https://github.com/<owner>/<repo>")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


class ReviewCreate(BaseModel):
    repo_url: str
    depth: Depth = Depth.quick
    ai_provider: str | None = Field(default=None, max_length=64)

    @field_validator("repo_url")
    @classmethod
    def _v(cls, v: str) -> str:
        return normalize_github_url(v)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    repo_url: str
    depth: Depth
    status: ReviewStatus
    progress: int
    attempts: int
    last_heartbeat: datetime | None
    job_name: str | None
    ai_provider: str | None
    message: str | None
    created_at: datetime


class ProgressIn(BaseModel):
    progress: int = Field(ge=0, le=100)
    status: ReviewStatus | None = None
    message: str | None = Field(default=None, max_length=2000)
    report_path: str | None = None
    ai_provider: str | None = None


class AIKeyIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1)
