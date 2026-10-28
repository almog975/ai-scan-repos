"""Prometheus metrics for review-api (scraped on :8001/metrics)."""
import time

from prometheus_client import Counter, Histogram

REVIEWS = Counter("reviews_total", "Reviews entering a state (created = queued)", ["state"])
SCAN_ATTEMPTS = Counter("scan_attempts_total", "Finished scanner attempts", ["outcome"])
JOB_LAUNCH_ERRORS = Counter("job_launch_errors_total", "Scanner Job launches that raised")
REQUESTS = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route", "status"])


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
