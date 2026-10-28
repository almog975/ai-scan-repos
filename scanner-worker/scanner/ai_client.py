"""Worker-side AI client: a thin wrapper over ai_adapters.Adapter (shared with review-api).

`provider` is the adapter name (8 adapters, see ai_adapters.ADAPTER_NAMES). `kind` comes from the DB
provider row (env AI_KIND, set by review-api) and is never derived from the adapter name; when it is
missing the client assumes "cloud" (the safe side: the report then says code left the network).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

from .ai_adapters import ADAPTER_NAMES, SPECS, Adapter

log = logging.getLogger(__name__)
PROVIDERS = set(ADAPTER_NAMES)
BASE_URL_REQUIRED = {a for a, (default, _, _) in SPECS.items() if default is None}
# Used when AI_MODEL is empty (the admin always sets a model; this is only a dev fallback).
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-sonnet-latest", "gemini": "gemini-1.5-pro",
                  "mistral": "mistral-small-latest", "groq": "llama-3.1-8b-instant", "ollama": "llama3"}


RETRY_TRIES = 3
RETRY_BASE_SECONDS = float(os.environ.get("AI_RETRY_BASE_SECONDS", "0.5"))
RETRY_MAX_SECONDS = 30.0
_sleep = time.sleep


def _retry_after(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return max(float(v), 0.0)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            return max((parsedate_to_datetime(v).timestamp() - time.time()), 0.0)
        except (TypeError, ValueError):
            return None


@dataclass
class AIClient:
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    kind: str = ""
    name: str = ""
    timeout: float = 120.0
    transport: httpx.BaseTransport | None = None

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"unknown AI provider {self.provider!r}")
        if self.provider in BASE_URL_REQUIRED and not self.base_url:
            raise ValueError(f"AI_BASE_URL is required for {self.provider}")
        if self.kind not in ("onprem", "cloud"):
            log.warning("AI_KIND %r missing/invalid: treating provider as cloud", self.kind)
            self.kind = "cloud"
        self._a = Adapter(self.provider, self.model, self.api_key, self.base_url, self.timeout, self.transport)
        self.base_url = self._a.base_url
        self.name = self.name or self.provider

    @property
    def is_cloud(self) -> bool:
        """True when code/findings leave the private network (DB kind)."""
        return self.kind == "cloud"

    def build_request(self, prompt: str) -> httpx.Request:
        return self._a.build_request(prompt)

    def complete_once(self, prompt: str) -> str:
        return self._a.complete(prompt)

    def complete(self, prompt: str) -> str:
        """Up to RETRY_TRIES tries on 429/5xx with exponential backoff, honouring Retry-After (capped)."""
        for n in range(1, RETRY_TRIES + 1):
            try:
                return self._a.complete(prompt)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if n == RETRY_TRIES or not (code == 429 or code >= 500):
                    raise
                delay = _retry_after(e.response.headers.get("retry-after"))
                delay = RETRY_BASE_SECONDS * 2 ** (n - 1) if delay is None else delay
                log.info("AI %s HTTP %d, retry %d/%d in %.1fs", self.provider, code, n, RETRY_TRIES - 1, delay)
                _sleep(min(delay, RETRY_MAX_SECONDS))
        raise AssertionError("unreachable")


def parse_ai_json(ans: str) -> tuple[str, dict | None]:
    """Split an answer into (markdown, trailing JSON object or None)."""
    import json
    import re
    s = (ans or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```\s*$", s, re.S) or re.search(r"(\{[^\n]*\})\s*$", s)
    if not m:
        return s, None
    try:
        j = json.loads(m.group(1))
    except ValueError:
        return s, None
    return (s[:m.start()].strip() or s), j if isinstance(j, dict) else None
