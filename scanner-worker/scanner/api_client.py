"""Talks to review-api's internal endpoints (worker is DB-less)."""
import logging

import httpx

log = logging.getLogger(__name__)


class ReviewAPI:
    def __init__(self, base_url: str, token: str, transport=None):
        self.c = httpx.Client(base_url=base_url.rstrip("/"), timeout=15, transport=transport,
                              headers={"X-Internal-Token": token})

    def get_review(self, review_id: str) -> dict:
        r = self.c.get(f"/internal/reviews/{review_id}")
        r.raise_for_status()
        return r.json()

    def progress(self, review_id: str, progress: int, *, status=None, message=None, report_path=None, ai_provider=None):
        body = {"progress": progress, "status": status, "message": message,
                "report_path": report_path, "ai_provider": ai_provider}
        try:
            self.c.post(f"/internal/reviews/{review_id}/progress",
                        json={k: v for k, v in body.items() if v is not None}).raise_for_status()
        except httpx.HTTPError as exc:  # heartbeat failure must not kill the scan
            log.warning("progress update failed: %s", exc)
