"""Worker -> review-api internal channel (worker is DB-less). Header X-Internal-Token."""
import logging

import httpx

log = logging.getLogger(__name__)


class Superseded(Exception):
    """review-api says this attempt is no longer current (paused/cancelled/relaunched)."""


class ReviewAPI:
    def __init__(self, base_url: str, token: str, transport=None):
        self.c = httpx.Client(base_url=base_url.rstrip("/"), timeout=15, transport=transport,
                              headers={"X-Internal-Token": token})

    def report(self, review_id: str, body: dict) -> None:
        """POST /internal/reviews/{id}/progress  {attempt, budget_no, stage, progress, state?, error?, commit_sha?}.
        409 => superseded (stop now). Other failures are logged: a heartbeat blip must not kill the scan;
        the watchdog relies on progress.json/updated_at as well."""
        try:
            r = self.c.post(f"/internal/reviews/{review_id}/progress", json=body)
        except httpx.HTTPError as exc:
            log.warning("progress update failed: %s", exc)
            return
        if r.status_code == 409:
            raise Superseded(r.text[:200])
        if r.status_code >= 400:
            log.warning("progress update rejected: %s %s", r.status_code, r.text[:200])
