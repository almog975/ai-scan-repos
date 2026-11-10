"""Phase 7: the worker reports AI-call deltas in its heartbeat (review-api folds them into ai_provider_*)."""
from scanner.health import REGISTRY
from scanner.run import Worker
from tests.test_worker import FakeAI, FakeAPI, cfg, fake_clone, fake_scanner


def _ai_totals(calls):
    out = {"calls": 0, "errors": 0, "seconds": 0.0}
    for b in calls:
        for k in out:
            out[k] += b.get("ai", {}).get(k, 0)
    return out


def test_ai_deltas_sum_to_calls_made(tmp_path):
    api, ai = FakeAPI(), FakeAI()
    Worker(cfg(tmp_path, "brief"), api, ai, fake_scanner, fake_clone).run()
    t = _ai_totals(api.calls)
    assert t["calls"] == len(ai.prompts) == 11 and t["errors"] == 0 and t["seconds"] >= 0
    assert all(set(b.get("ai", {})) <= {"calls", "errors", "seconds"} for b in api.calls)
    assert any("ai" not in b for b in api.calls)  # only sent when there is something to report


def test_ai_errors_counted(tmp_path):
    api = FakeAPI()
    e0 = REGISTRY.get_sample_value("scan_ai_calls_total", {"kind": "unknown", "outcome": "error"}) or 0
    Worker(cfg(tmp_path, "brief"), api, FakeAI(fail=True), fake_scanner, fake_clone).run()
    t = _ai_totals(api.calls)
    assert t["calls"] >= 1 and t["errors"] == t["calls"]
    assert REGISTRY.get_sample_value("scan_ai_calls_total", {"kind": "unknown", "outcome": "error"}) == e0 + t["calls"]


def test_failed_report_keeps_delta_for_next_beat(tmp_path):
    w = Worker(cfg(tmp_path), FakeAPI(), FakeAI(), fake_scanner, fake_clone)

    class Down:
        def report(self, rid, body):
            raise ConnectionError("blip")

    w._ai_stats = {"calls": 2, "errors": 1, "seconds": 0.5}
    w.api = Down()
    try:
        w._send({"stage": "ai"})
    except ConnectionError:
        pass
    assert w._ai_stats["calls"] == 2  # restored, not lost
    ok = FakeAPI()
    w.api = ok
    w._send({"stage": "ai"})
    assert ok.calls[0]["ai"] == {"calls": 2, "errors": 1, "seconds": 0.5} and w._ai_stats["calls"] == 0
