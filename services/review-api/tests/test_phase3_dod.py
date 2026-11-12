"""Phase 3 DoD on the real local stack (uvicorn + LocalLauncher + scanner-worker subprocess + sqlite file DB).
(a) SIGKILL/resume without duplicate ids: test_phase3_recovery.py::test_worker_sigkill_*.
(b) 5 consecutive failures -> failed with an honest reason + 5 review_attempts rows.
(c) pause / resume / cancel; cancel stops the worker, cleans /work and keeps the partial report."""
from pathlib import Path

import pytest

from app import db as dbmod
from app.models import ReviewAttempt
from contract import check
from phase3_harness import WORKER_PY, Stack, ids

pytestmark = [pytest.mark.skipif(not WORKER_PY or not Path(WORKER_PY).exists(), reason="AISCAN_WORKER_PYTHON not set")]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    s = Stack(tmp_path, monkeypatch)
    yield s
    s.hang_trivy(False)
    s.close()


def test_b_five_failures_failed_with_reason(stack):
    stack.repo.rename(stack.repo.with_name("origin.hidden"))  # every clone fails
    rid = stack.create()
    for n in range(1, 6):
        stack.wait(lambda: not stack.worker_pids(rid), what=f"attempt {n} exit")
        stack.wait(lambda: stack.get(rid)["attempt"] == n, what=f"attempt {n}")
        stack.sweep()
    r = stack.get(rid)
    check("Review", r)
    assert r["state"] == "failed" and r["failure_code"] == "MAX_ATTEMPTS_EXCEEDED"
    assert r["failure_detail"].startswith("5 attempts exhausted in budget 1; last error (CLONE_FAILED)"), r
    with dbmod.session_factory()() as db:
        rows = db.query(ReviewAttempt).filter_by(review_id=rid, budget_no=1).all()
        assert len(rows) == 5 and all(a.ended_at is not None for a in rows)
    assert stack.http.get(f"/reviews/{rid}/report.json", headers=stack.H).status_code == 409  # nothing scanned


def test_c_pause_resume_cancel_cleans_work_keeps_partial(stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    assert stack.post(rid, "pause").json()["state"] == "paused"
    stack.wait(lambda: not stack.worker_pids(rid), what="worker stopped on pause")
    assert stack.work_empty() and stack.get(rid)["paused_by"]["login"]
    r = stack.post(rid, "resume")
    assert r.status_code == 202 and r.json()["attempt"] == 1  # pause does not consume an attempt
    stack.wait_trivy_started()
    c = stack.post(rid, "cancel")
    assert c.status_code == 202 and c.json()["state"] == "cancelled" and c.json()["cancelled_by"]["login"]
    check("Review", c.json())
    stack.wait(lambda: not stack.worker_pids(rid), what="worker stopped on cancel")
    stack.wait(stack.work_empty, what="/work cleaned")
    rep = stack.report(rid)  # partial results kept
    check("report.schema.json", rep)
    assert rep["partial"] is True and len(ids(rep)) == len(set(ids(rep)))
    assert stack.post(rid, "cancel").status_code == 409 and stack.post(rid, "resume").status_code == 409
    with dbmod.session_factory()() as db:
        assert db.query(ReviewAttempt).filter_by(review_id=rid).count() == 1
