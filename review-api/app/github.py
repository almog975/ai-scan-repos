"""Minimal GitHub REST client: repo metadata + ref -> commit SHA. Injected (fake) in tests."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .errors import ApiError, not_found

SHA_RE = re.compile(r"^[a-f0-9]{40}$")


@dataclass
class RepoInfo:
    private: bool
    default_branch: str
    size_kb: int


class GitHub(Protocol):
    def repo(self, owner: str, name: str) -> RepoInfo: ...

    def resolve(self, owner: str, name: str, ref: str) -> str: ...


def split(repo_url: str) -> tuple[str, str]:
    owner, name = repo_url.rstrip("/").split("/")[-2:]
    return owner, name


class HttpGitHub:
    def __init__(self, token: str = "", base: str = "https://api.github.com", client: httpx.Client | None = None):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        self.c = client or httpx.Client(base_url=base, headers=h, timeout=10)

    def _get(self, path: str) -> dict:
        r = self.c.get(path)
        if r.status_code == 404:
            raise not_found("repository or ref")
        if r.status_code in (401, 403) and r.headers.get("x-ratelimit-remaining") == "0":
            raise ApiError(429, "RATE_LIMITED", "GitHub rate limit", headers={"Retry-After": "60"})
        if r.status_code in (401, 403):
            raise ApiError(401, "UNAUTHORIZED", "GitHub authorization required")
        r.raise_for_status()
        return r.json()

    def repo(self, owner, name):
        j = self._get(f"/repos/{owner}/{name}")
        return RepoInfo(bool(j.get("private")), j.get("default_branch") or "main", int(j.get("size") or 0))

    def resolve(self, owner, name, ref):
        if SHA_RE.match(ref):
            return ref
        if ref == "HEAD":
            ref = self.repo(owner, name).default_branch
        return self._get(f"/repos/{owner}/{name}/commits/{ref}")["sha"]
