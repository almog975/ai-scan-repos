"""Glue between reviews and the launcher: builds the JobSpec and the per-review AI secret env."""
from __future__ import annotations

import logging

from sqlalchemy.orm import object_session

from .crypto import KeyCipher
from .launcher import JobSpec
from .models import AIProvider
from .providers import adapter_name

log = logging.getLogger(__name__)


class ReviewJobs:
    def __init__(self, inner, settings):
        self.inner, self.s = inner, settings

    def secret_env(self, r) -> dict | None:
        if not r.provider_id:
            return None
        db = object_session(r)
        p = db.get(AIProvider, r.provider_id) if db else None
        if p is None:
            return None
        kind = getattr(p.kind, "value", p.kind)
        env = {"AI_PROVIDER": adapter_name(p.base_url, kind), "AI_MODEL": p.model, "AI_BASE_URL": p.base_url or ""}
        if p.api_key_enc:
            try:
                env["AI_API_KEY"] = KeyCipher(self.s.ai_keys_encryption_key).decrypt(p.api_key_enc, p.key_nonce, p.id)
            except ValueError:
                log.error("cannot decrypt AI key for provider %s", p.id)
        return env

    def launch(self, r) -> str:
        return self.inner.launch(JobSpec.of(r), secret_env=self.secret_env(r))

    def delete(self, job_name: str) -> None:
        self.inner.delete(job_name)

    def cleanup(self, review_id: str) -> None:
        self.inner.cleanup(review_id)

    def status(self, job_name: str) -> str:
        return self.inner.status(job_name) if hasattr(self.inner, "status") else "active"
