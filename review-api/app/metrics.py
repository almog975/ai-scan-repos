"""Prometheus metrics for review-api (scraped on :8001/metrics). See docs/monitoring.md.

Low cardinality only: labels are bounded enums (state, outcome, kind, stage, method, route template, status).
Never a review_id, user, repo or URL.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily

log = logging.getLogger(__name__)

# ---- review lifecycle ---------------------------------------------------------------------------
REVIEWS = Counter("reviews_total", "Reviews entering a state (created = queued)", ["state"])
SCAN_ATTEMPTS = Counter("scan_attempts_total", "Finished scanner attempts", ["outcome"])
JOB_LAUNCH_ERRORS = Counter("job_launch_errors_total", "Scanner Job launches that raised")
REVIEW_ATTEMPTS = Histogram("review_attempts", "Attempts used by a review (current budget) when it ends",
                            ["state"], buckets=(1, 2, 3, 4, 5))
REVIEW_DURATION = Histogram("review_duration_seconds", "created -> completed|failed|cancelled", ["state"],
                            buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 21600, 86400))

# ---- reconciler ---------------------------------------------------------------------------------
RECONCILER_RUNS = Counter("reconciler_runs_total", "Reconciler passes", ["result"])  # ok | error
RECONCILER_DURATION = Histogram("reconciler_run_duration_seconds", "Reconciler pass duration",
                                buckets=(.01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30))
STUCK_DETECTIONS = Counter("stuck_detections_total", "Attempts marked stuck by the reconciler")
RECONCILER_ACTIONS = Counter("reconciler_actions_total", "Reconciler actions", ["action"])
# action: requeued | overdue | stuck | relaunched | failed

# ---- AI providers (kind = onprem | cloud, from the DB row) --------------------------------------
AI_REQUESTS = Counter("ai_provider_requests_total", "AI provider calls", ["kind", "outcome", "source"])
# outcome: ok | error ; source: test (admin connection test in review-api) | worker (scanner, via heartbeat)
AI_SECONDS = Counter("ai_provider_request_seconds_total", "Total time spent in AI provider calls", ["kind", "source"])
AI_LATENCY = Histogram("ai_provider_request_duration_seconds", "AI provider call latency (review-api calls)",
                       ["kind"], buckets=(.1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120))

# ---- worker (reported through the heartbeat; the scanner Job is short-lived and DB-less) ---------
WORKER_HEARTBEATS = Counter("worker_heartbeats_total", "Heartbeats received from scanner Jobs", ["stage"])
WORKER_STAGES = ("start", "clone", "inventory", "gitleaks", "semgrep", "trivy", "ai", "report", "done")

# ---- HTTP ---------------------------------------------------------------------------------------
REQUESTS = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route", "status"])

TERMINAL = ("completed", "failed", "cancelled")


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def observe_terminal(r, state: str) -> None:
    """Called by state.transition() when a review reaches a terminal state."""
    if state not in TERMINAL:
        return
    REVIEW_ATTEMPTS.labels(state).observe(r.attempt or 1)
    created = _aware(r.created_at)
    if created is not None:
        REVIEW_DURATION.labels(state).observe(max(0.0, (datetime.now(timezone.utc) - created).total_seconds()))


def observe_ai(kind: str | None, source: str, seconds: float, ok: bool, calls: int = 1) -> None:
    k = kind if kind in ("onprem", "cloud") else "unknown"
    AI_REQUESTS.labels(k, "ok" if ok else "error", source).inc(calls)
    AI_SECONDS.labels(k, source).inc(max(0.0, seconds))
    if source == "test":
        AI_LATENCY.labels(k).observe(max(0.0, seconds))


def worker_stage(stage: str) -> str:
    return stage if stage in WORKER_STAGES else "other"


class DbStateCollector:
    """Scrape-time gauges from the DB (any replica gives the same numbers; alert on max()).
    reviews_by_state{state}, reviews_heartbeat_overdue, review_oldest_heartbeat_age_seconds."""

    STATES = ("queued", "running", "paused", "stuck", "retrying", "completed", "failed", "cancelled")

    def __init__(self, session_factory, heartbeat_timeout: int):
        self.sf, self.timeout = session_factory, heartbeat_timeout

    def describe(self):  # no DB access at registration time
        return []

    def collect(self):
        from sqlalchemy import func, select

        from .models import Review, State
        up = GaugeMetricFamily("review_db_metrics_up", "1 if the DB-backed gauges could be read")
        sf = self.sf() if callable(self.sf) else None
        if sf is None:
            up.add_metric([], 0)
            yield up
            return
        try:
            with sf() as db:
                counts = dict(db.execute(select(Review.state, func.count()).group_by(Review.state)).all())
                beats = db.execute(select(Review.last_heartbeat, Review.started_at, Review.created_at)
                                   .where(Review.state == State.running)).all()
        except Exception:  # noqa: BLE001 - never fail a scrape
            log.warning("metrics: DB read failed", exc_info=True)
            up.add_metric([], 0)
            yield up
            return
        g = GaugeMetricFamily("reviews_by_state", "Reviews currently in each state", labels=["state"])
        by = {getattr(k, "value", k): v for k, v in counts.items()}
        for s in self.STATES:
            g.add_metric([s], by.get(s, 0))
        yield g
        now = datetime.now(timezone.utc)
        ages = [(now - _aware(hb or st or cr)).total_seconds() for hb, st, cr in beats if (hb or st or cr)]
        od = GaugeMetricFamily("reviews_heartbeat_overdue",
                               "Running reviews whose last heartbeat is older than HEARTBEAT_TIMEOUT_SECONDS")
        od.add_metric([], sum(a > self.timeout for a in ages))
        yield od
        age = GaugeMetricFamily("review_oldest_heartbeat_age_seconds", "Oldest heartbeat age among running reviews")
        age.add_metric([], max(ages, default=0.0))
        yield age
        up.add_metric([], 1)
        yield up


_collector: DbStateCollector | None = None


def install_db_collector(session_factory, heartbeat_timeout: int, registry=REGISTRY) -> DbStateCollector:
    """Idempotent: create_app() may run many times in one process (tests)."""
    global _collector
    if _collector is not None:
        try:
            registry.unregister(_collector)
        except KeyError:
            pass
    _collector = DbStateCollector(session_factory, heartbeat_timeout)
    registry.register(_collector)
    return _collector


def install_request_metrics(app):
    @app.middleware("http")
    async def _timed(request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            route = request.scope.get("route")
            REQUESTS.labels(request.method, getattr(route, "path", "unmatched"), str(status)).observe(
                time.perf_counter() - t0)
