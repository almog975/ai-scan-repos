"""Phase 7: /metrics exposes the documented, low-cardinality review-api metrics (docs/monitoring.md)."""
import re
from datetime import datetime, timedelta, timezone

from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families

from app import db as dbmod
from app.models import AIProvider, Review, State
from app.watchdog import sweep
from tests.test_control import FakeK8s, beat, create, k8s  # noqa: F401 - fixture
from tests.test_providers import W
from tests.test_watchdog import FakeK8s as WatchK8s

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


def scrape(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    return r.text, [s for f in text_string_to_metric_families(r.text) for s in f.samples]


def val(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_reviews_by_state_gauge_from_db(client, H, k8s):  # noqa: F811
    a, b = create(client, H)["id"], create(client, H)["id"]
    with dbmod.session_factory()() as db:
        db.get(Review, b).state = State.stuck
        db.commit()
    text, samples = scrape(client)
    by = {s.labels["state"]: s.value for s in samples if s.name == "reviews_by_state"}
    assert set(by) == {"queued", "running", "paused", "stuck", "retrying", "completed", "failed", "cancelled"}
    assert by["stuck"] == 1 and by["queued"] + by["running"] == 1
    assert val("review_db_metrics_up") == 1


def test_heartbeat_overdue_gauge(client, H, k8s, settings):  # noqa: F811
    rid = create(client, H)["id"]
    with dbmod.session_factory()() as db:
        r = db.get(Review, rid)
        r.state = State.running
        r.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=settings.heartbeat_timeout_seconds + 60)
        db.commit()
    scrape(client)
    assert val("reviews_heartbeat_overdue") == 1
    assert val("review_oldest_heartbeat_age_seconds") > settings.heartbeat_timeout_seconds


def test_terminal_transition_observes_attempts_and_duration(client, H, k8s):  # noqa: F811
    n0 = val("review_attempts_count", state="cancelled")
    d0 = val("review_duration_seconds_count", state="cancelled")
    rid = create(client, H)["id"]
    assert client.post(f"/reviews/{rid}/cancel", headers=H).status_code == 202
    assert val("review_attempts_count", state="cancelled") == n0 + 1
    assert val("review_duration_seconds_count", state="cancelled") == d0 + 1


def test_reconciler_runs_and_stuck_detections(app, client, H, settings):
    k8s = app.state.deps["launcher"] = WatchK8s()
    ok0, st0 = val("reconciler_runs_total", result="ok"), val("stuck_detections_total")
    rid = create(client, H)["id"]
    now = datetime.now(timezone.utc)
    with dbmod.session_factory()() as db:
        assert sweep(db, k8s, settings, at=now + timedelta(minutes=20)) == {rid: "overdue"}
        assert sweep(db, k8s, settings, at=now + timedelta(minutes=20.5)) == {rid: "stuck"}
    assert val("reconciler_runs_total", result="ok") == ok0 + 2
    assert val("reconciler_run_duration_seconds_count") >= 1
    assert val("stuck_detections_total") == st0 + 1
    assert val("reconciler_actions_total", action="stuck") >= 1


def test_worker_ai_stats_via_heartbeat(client, H, A, k8s):  # noqa: F811
    p = client.post("/admin/ai-providers", json={**W, "name": "m-ai"}, headers=A).json()
    rid = create(client, H)["id"]
    with dbmod.session_factory()() as db:
        db.get(Review, rid).provider_id = p["id"]
        kind = db.get(AIProvider, p["id"]).kind.value
        db.commit()
    ok0 = val("ai_provider_requests_total", kind=kind, outcome="ok", source="worker")
    er0 = val("ai_provider_requests_total", kind=kind, outcome="error", source="worker")
    h0 = val("worker_heartbeats_total", stage="ai")
    r = beat(client, rid, stage="ai", ai={"calls": 3, "errors": 1, "seconds": 1.5})
    assert r.status_code == 200, r.text
    assert val("ai_provider_requests_total", kind=kind, outcome="ok", source="worker") == ok0 + 2
    assert val("ai_provider_requests_total", kind=kind, outcome="error", source="worker") == er0 + 1
    assert val("worker_heartbeats_total", stage="ai") == h0 + 1
    assert beat(client, rid, ai={"calls": 1, "errors": 0, "seconds": 1, "x": 1}).status_code == 400
    assert beat(client, rid, stage="weird").status_code == 200
    assert val("worker_heartbeats_total", stage="other") >= 1  # unknown stages are bucketed


def test_admin_provider_test_latency(client, A):
    p = client.post("/admin/ai-providers", json={**W, "name": "m-ai2"}, headers=A).json()
    c0 = val("ai_provider_request_duration_seconds_count", kind=p["kind"])
    assert client.post(f"/admin/ai-providers/{p['id']}/test", headers=A).status_code == 200
    assert val("ai_provider_request_duration_seconds_count", kind=p["kind"]) == c0 + 1
    assert val("ai_provider_requests_total", kind=p["kind"], outcome="ok", source="test") >= 1


def test_http_metrics_and_no_high_cardinality_labels(client, H, k8s):  # noqa: F811
    rid = create(client, H)["id"]
    client.get(f"/reviews/{rid}", headers=H)
    text, samples = scrape(client)
    assert any(s.name.startswith("http_request_duration_seconds") and s.labels.get("route") == "/reviews/{id}"
               for s in samples)
    for s in samples:
        for k, v in s.labels.items():
            assert k not in ("review_id", "user", "user_id", "repo", "repo_url", "id"), s
            assert not UUID.match(v), s
    assert rid not in text
