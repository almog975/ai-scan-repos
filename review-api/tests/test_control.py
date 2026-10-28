import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db as dbmod
from app.models import Outcome, Review, ReviewAttempt, State
from contract import check, spec
from tests.conftest import TOKEN

URL = "https://github.com/a/b"
SHA = "c" * 40
WORKER = Path(__file__).resolve().parents[2] / "scanner-worker"


class FakeK8s:
    def __init__(self):
        self.launched, self.deleted, self.cleaned = [], [], []

    def launch(self, r):
        self.launched.append((r.budget_no, r.attempt))
        return f"scan-{r.id}-a{(r.budget_no - 1) * 5 + r.attempt}"

    def delete(self, name):
        self.deleted.append(name)

    def cleanup(self, rid, budget_no=1):
        self.cleaned.append(rid)

    def status(self, name):
        return "active"


@pytest.fixture
def k8s(app, settings):
    settings.sse_interval_seconds = 0.01
    settings.sse_max_seconds = 5
    f = FakeK8s()
    app.state.deps["launcher"] = f
    return f


def create(client, H, depth="quick"):
    r = client.post("/reviews", json={"repo_url": URL, "depth": depth}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def set_state(rid, state, **kw):
    with dbmod.session_factory()() as db:
        r = db.get(Review, rid)
        r.state = state
        for k, v in kw.items():
            setattr(r, k, v)
        db.commit()


def write_progress(settings, rid, files, attempt=1, budget=1, stamp="2026-10-28T10:00:00Z"):
    doc = {"schema": "progress/v1", "review_id": rid, "state": "running", "stage": "semgrep", "attempt": attempt,
           "budget_no": budget, "current_file": None, "updated_at": stamp,
           "totals": {"files": len(files), "done": sum(s == "done" for _, s in files), "skipped": 0, "error": 0,
                      "findings": 1},
           "files": [{"path": p, "status": s, "findings_count": 1 if s == "done" else 0} for p, s in files]}
    d = Path(settings.reports_dir) / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(doc))
    return doc


def beat(client, rid, **kw):
    body = {"attempt": 1, "budget_no": 1, "stage": "semgrep", "progress": 20, **kw}
    return client.post(f"/internal/reviews/{rid}/progress", json=body, headers={"X-Internal-Token": TOKEN})


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(ln.split(": ", 1) for ln in block.splitlines() if not ln.startswith(":"))
        if lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_progress_json_snapshot(client, H, k8s, settings):
    rid = create(client, H)["id"]
    snap = client.get(f"/reviews/{rid}/progress?format=json", headers=H).json()
    check("Progress", snap)
    assert snap["files"] == [] and snap["state"] == "queued"  # running on the worker's first heartbeat
    write_progress(settings, rid, [("a.py", "done"), ("b.py", "pending")])
    snap = client.get(f"/reviews/{rid}/progress?format=json", headers=H).json()
    check("progress.schema.json", snap)
    assert snap["totals"]["files"] == 2
    assert client.get(f"/reviews/{rid}/progress?format=xml", headers=H).status_code == 400


def test_sse_events_validate_and_done_closes(client, H, k8s, settings):
    rid = create(client, H)["id"]
    write_progress(settings, rid, [("a.py", "done")])
    set_state(rid, State.completed)
    with client.stream("GET", f"/reviews/{rid}/progress", headers=H) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = sse(r.read().decode())
    kinds = [e for e, _ in events]
    assert kinds == ["state", "progress", "done"]
    for e, d in events:
        if e == "progress":
            check("progress.schema.json", d)
    assert events[0][1] == {"state": "completed", "attempt": 1, "budget_no": 1}
    assert events[-1][1] == {"state": "completed"}


def test_sse_streams_changes(client, H, k8s, settings):
    import threading
    import time
    rid = create(client, H)["id"]
    write_progress(settings, rid, [("a.py", "pending")])

    def later():
        time.sleep(0.6)
        write_progress(settings, rid, [("a.py", "done")], stamp="2026-10-28T10:00:05Z")
        time.sleep(0.6)
        set_state(rid, State.cancelled)
    threading.Thread(target=later).start()
    with client.stream("GET", f"/reviews/{rid}/progress", headers=H) as r:
        events = sse(r.read().decode())
    progress = [d for e, d in events if e == "progress"]
    assert len(progress) >= 2 and progress[0]["files"][0]["status"] == "pending"
    assert any(d["files"][0]["status"] == "done" for d in progress)
    assert [d["state"] for e, d in events if e == "state"] == ["queued", "cancelled"]
    assert events[-1] == ("done", {"state": "cancelled"})
    for d in progress:
        check("progress.schema.json", d)


def test_files_pagination(client, H, k8s, settings):
    rid = create(client, H)["id"]
    write_progress(settings, rid, [(f"f{i:02}.py", "done" if i % 2 else "pending") for i in range(25)])
    p1 = client.get(f"/reviews/{rid}/files?page_size=10", headers=H).json()
    check("ReviewFilePage", p1)
    assert len(p1["items"]) == 10 and p1["total"] == 25 and p1["page"] == 1
    p3 = client.get(f"/reviews/{rid}/files?page=3&page_size=10", headers=H).json()
    assert len(p3["items"]) == 5
    d = client.get(f"/reviews/{rid}/files?status=done&page_size=100", headers=H).json()
    assert d["total"] == 12 and all(f["status"] == "done" for f in d["items"])
    for q in ("page=0", "page_size=101", "status=bogus"):
        r = client.get(f"/reviews/{rid}/files?{q}", headers=H)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_pause_resume_cancel(client, H, k8s):
    rid = create(client, H)["id"]
    r = client.post(f"/reviews/{rid}/pause", headers=H)
    assert r.status_code == 202 and r.json()["state"] == "paused"
    check("Review", r.json())
    assert r.json()["paused_at"] and r.json()["paused_by"]["login"]
    assert k8s.deleted == [f"scan-{rid}-a1"]
    assert client.post(f"/reviews/{rid}/pause", headers=H).status_code == 409
    r = client.post(f"/reviews/{rid}/resume", headers=H)
    assert r.status_code == 202 and r.json()["state"] == "queued" and r.json()["attempt"] == 1
    check("Review", r.json())
    assert k8s.launched == [(1, 1), (1, 1)]  # same attempt continues its checkpoint
    assert client.post(f"/reviews/{rid}/resume", headers=H).status_code == 409
    assert beat(client, rid).json()["state"] == "running"  # first heartbeat
    r = client.post(f"/reviews/{rid}/cancel", headers=H)
    assert r.status_code == 202 and r.json()["state"] == "cancelled"
    check("Review", r.json())
    assert r.json()["cancelled_at"] and r.json()["cancelled_by"]["id"] and r.json()["paused_by"] is None
    assert k8s.cleaned == [rid] and len(k8s.deleted) == 2
    assert client.post(f"/reviews/{rid}/cancel", headers=H).status_code == 409
    att = client.get(f"/reviews/{rid}/attempts", headers=H).json()
    assert [a["outcome"] for a in att] == ["cancelled"]
    assert beat(client, rid).status_code == 409  # Job must stop


def test_resume_from_stuck_uses_next_attempt(client, H, k8s):
    rid = create(client, H)["id"]
    set_state(rid, State.stuck)
    r = client.post(f"/reviews/{rid}/resume", headers=H)
    assert r.status_code == 202 and r.json()["attempt"] == 2 and k8s.launched[-1] == (1, 2)
    assert r.json()["state"] == "retrying"
    assert client.post(f"/reviews/{rid}/resume", headers=H).status_code == 409  # no double Job
    assert len(k8s.launched) == 2
    set_state(rid, State.stuck, attempt=5)
    r = client.post(f"/reviews/{rid}/resume", headers=H)  # retry now: budget exhausted -> failed
    assert r.status_code == 202 and r.json()["state"] == "failed"
    assert r.json()["failure_code"] == "MAX_ATTEMPTS_EXCEEDED" and len(k8s.launched) == 2


def test_report_endpoints(client, H, k8s, settings):
    rid = create(client, H)["id"]
    assert client.get(f"/reviews/{rid}/report.json", headers=H).status_code == 409
    set_state(rid, State.completed)
    r = client.get(f"/reviews/{rid}/report.md", headers=H)  # retention removed it
    assert r.status_code == 410 and r.json()["error"]["code"] == "REPORT_EXPIRED"
    check("Error", r.json())
    d = Path(settings.reports_dir) / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("# Security review\n")
    r = client.get(f"/reviews/{rid}/report.md", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")


def test_internal_progress_auth_and_staleness(client, H, k8s):
    rid = create(client, H)["id"]
    assert client.post(f"/internal/reviews/{rid}/progress", json={}).status_code == 401
    assert beat(client, rid, attempt=2).status_code == 409
    assert beat(client, rid, bogus=1).status_code == 400
    assert beat(client, "00000000-0000-0000-0000-000000000000").status_code == 404
    r = beat(client, rid, outcome="error", error="semgrep crashed")
    assert r.status_code == 200
    with dbmod.session_factory()() as db:
        a = db.query(ReviewAttempt).filter_by(review_id=rid).one()
        assert a.outcome == Outcome.error and a.error == "semgrep crashed"
    assert beat(client, rid).status_code == 409  # attempt ended
    assert "/internal/reviews/{id}/progress" not in spec()["paths"]  # internal, not public contract


@pytest.mark.parametrize("depth", ["quick", "brief"])
def test_end_to_end_with_real_worker(client, H, k8s, settings, depth):
    """scanner-worker -> internal progress -> completed; report.json served and contract-valid."""
    sys.path.insert(0, str(WORKER))
    try:
        from scanner.api_client import ReviewAPI
        from scanner.config import WorkerConfig
        from scanner.run import Worker
        from scanner.tools import make_finding
    finally:
        sys.path.remove(str(WORKER))
    rv = create(client, H, depth)
    rid = rv["id"]

    def clone(url, dest, timeout, ref="", sha=""):
        dest.mkdir(parents=True)
        (dest / "a.py").write_text("eval(input())\n")
        return sha or SHA

    def scanner(name, src, t):
        return {"status": "ok", "findings": [make_finding(name, "sast", f"{name}.r", "high", "a.py", 1, "t", "d")]}

    cfg = WorkerConfig(review_id=rid, repo_url=URL, depth=depth, attempt=1, budget_no=1, commit_sha=rv["commit_sha"],
                       reports_dir=settings.reports_dir, work_dir=str(Path(settings.reports_dir).parent / "work"),
                       ai_provider="none")
    api = ReviewAPI("http://testserver", TOKEN, transport=client._transport)
    assert Worker(cfg, api, None, scanner, clone).run() == "completed"
    r = client.get(f"/reviews/{rid}", headers=H).json()
    assert r["state"] == "completed" and r["findings_count"] == 3 and r["files_total"] == 1
    rep = client.get(f"/reviews/{rid}/report.json", headers=H)
    assert rep.status_code == 200
    check("report.schema.json", rep.json())
    assert client.get(f"/reviews/{rid}/report.md", headers=H).text.startswith("# Security review")
    att = client.get(f"/reviews/{rid}/attempts", headers=H).json()
    assert [a["outcome"] for a in att] == ["success"] and k8s.cleaned == [rid]


def test_partial_report_for_failed_and_cancelled(client, H, k8s, settings):
    rid = create(client, H)["id"]
    d = Path(settings.reports_dir) / rid
    d.mkdir(parents=True, exist_ok=True)
    set_state(rid, State.cancelled, started_at=datetime.now(timezone.utc))
    r = client.get(f"/reviews/{rid}/report.json", headers=H)
    assert r.status_code == 409  # no partial written
    (d / "report.partial.json").write_text(json.dumps({"partial": True}))
    (d / "report.partial.md").write_text("# Security review\n\n> Partial report\n")
    assert client.get(f"/reviews/{rid}/report.json", headers=H).json() == {"partial": True}
    set_state(rid, State.failed)
    assert "Partial" in client.get(f"/reviews/{rid}/report.md", headers=H).text
    import shutil
    shutil.rmtree(d)
    r = client.get(f"/reviews/{rid}/report.json", headers=H)
    assert r.status_code == 410 and r.json()["error"]["code"] == "REPORT_EXPIRED"


def test_cancel_from_every_active_state(client, H, k8s):
    for st in (State.queued, State.running, State.paused, State.stuck, State.retrying):
        rid = create(client, H)["id"]
        set_state(rid, st)
        r = client.post(f"/reviews/{rid}/cancel", headers=H)
        assert r.status_code == 202 and r.json()["state"] == "cancelled", st
        for again in ("cancel", "pause", "resume"):
            assert client.post(f"/reviews/{rid}/{again}", headers=H).status_code == 409


def test_queue_position(client, H, k8s):
    a = create(client, H)["id"]
    set_state(a, State.queued, commit_sha="d" * 40)  # not a duplicate of the next one
    b = create(client, H)
    assert b["queue_position"] == 2
    assert client.get(f"/reviews/{a}", headers=H).json()["queue_position"] == 1
    beat(client, a)
    assert client.get(f"/reviews/{a}", headers=H).json()["queue_position"] is None
    assert client.get(f"/reviews/{b['id']}", headers=H).json()["queue_position"] == 1


def test_resume_provider_checks(client, H, A, k8s):
    from app.models import AIProvider, ProviderKind
    with dbmod.session_factory()() as db:
        p = AIProvider(name="p", kind=ProviderKind.onprem, base_url="http://ollama:11434", model="m", enabled=True)
        db.add(p)
        db.commit()
        pid = p.id
    rid = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "provider_id": pid}, headers=H).json()["id"]
    client.post(f"/reviews/{rid}/pause", headers=H)
    with dbmod.session_factory()() as db:
        db.get(AIProvider, pid).enabled = False
        db.commit()
    r = client.post(f"/reviews/{rid}/resume", headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    check("Error", r.json())
    with dbmod.session_factory()() as db:
        p = db.get(AIProvider, pid)
        p.enabled, p.kind = True, ProviderKind.cloud
        db.commit()
    r = client.post(f"/reviews/{rid}/resume", headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"
    r = client.post(f"/reviews/{rid}/resume", json={"ack_cloud": True}, headers=H)
    assert r.status_code == 202 and r.json()["state"] == "queued"
