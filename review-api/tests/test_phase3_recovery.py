"""Phase 3 recovery / chaos tests: real review-api (uvicorn, sqlite file DB) + real scanner-worker subprocesses
(LocalLauncher) with shimmed scanners. The watchdog is driven by explicit sweep() calls with an injected clock.

Needs AISCAN_WORKER_PYTHON (a python with scanner-worker requirements), like test_e2e_local.py."""
import os
import signal
from pathlib import Path

import pytest

from app import db as dbmod
from app.models import Review, ReviewAttempt
from contract import check
from phase3_harness import WORKER_PY, Stack, ids, later

pytestmark = [pytest.mark.skipif(not WORKER_PY or not Path(WORKER_PY).exists(), reason="AISCAN_WORKER_PYTHON not set")]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    s = Stack(tmp_path, monkeypatch)
    yield s
    s.hang_trivy(False)
    s.close()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    """Finding ids of one uninterrupted run (separate stack), to compare resumed runs against."""
    mp = pytest.MonkeyPatch()
    s = Stack(tmp_path_factory.mktemp("baseline"), mp)
    try:
        rid = s.create()
        s.wait_state(rid, "completed")
        return sorted(ids(s.report(rid)))
    finally:
        s.close()
        mp.undo()


def assert_clean_report(s, rid, baseline, attempt, budget_no=1, killed=0):
    rep = s.report(rid)
    check("report.schema.json", rep)
    got = ids(rep)
    assert len(got) == len(set(got)), "duplicate finding ids in report.json"
    assert sorted(got) == baseline
    assert (rep["attempt"], rep["budget_no"]) == (attempt, budget_no)
    # a SIGKILLed worker cannot run its `finally`; in K8s /work is a per-pod emptyDir so that copy dies with the pod.
    # Locally (shared WORK_DIR) exactly the killed attempts' dirs may remain; the finishing attempt must clean its own.
    left = list((s.tmp / "work").iterdir())
    assert len(left) == killed, f"clone dirs left behind: {left}"
    return rep


def test_uninterrupted_run_completes(baseline, stack):
    rid = stack.create()
    r = stack.wait_state(rid, "completed")
    assert (r["attempt"], r["budget_no"]) == (1, 1)
    assert_clean_report(stack, rid, baseline, 1)
    assert {t: stack.calls(t) for t in ("semgrep", "gitleaks", "trivy")} == {"semgrep": 1, "gitleaks": 1, "trivy": 1}


# 1 ---------------------------------------------------------------------------------------------
def test_worker_sigkill_mid_scan_resumes_from_checkpoint(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()  # semgrep + gitleaks done and checkpointed
    assert stack.get(rid)["state"] == "running"
    stack.sigkill_worker(rid)
    stack.hang_trivy(False)
    assert stack.sweep() == {rid: "relaunched"}  # job failed (rc -9) -> attempt 2 of budget 1
    r = stack.wait_state(rid, "completed")
    assert (r["attempt"], r["budget_no"]) == (2, 1)
    assert_clean_report(stack, rid, baseline, attempt=2, killed=1)
    # resumed from the checkpoint: finished scanners were not re-run
    assert (stack.calls("semgrep"), stack.calls("gitleaks"), stack.calls("trivy")) == (1, 1, 2)
    with dbmod.session_factory()() as db:
        outs = [(a.n, getattr(a.outcome, "value", a.outcome)) for a in
                db.query(ReviewAttempt).filter_by(review_id=rid).order_by(ReviewAttempt.n)]
    assert outs == [(1, "stuck"), (2, "success")]


def test_worker_sigkill_twice_still_completes(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    for _ in range(2):
        stack.wait_trivy_started()
        stack.sigkill_worker(rid)
        assert stack.sweep() == {rid: "relaunched"}
    stack.hang_trivy(False)
    r = stack.wait_state(rid, "completed")
    assert r["attempt"] == 3
    assert_clean_report(stack, rid, baseline, attempt=3, killed=2)
    assert stack.calls("semgrep") == 1


# 2 ---------------------------------------------------------------------------------------------
def test_review_api_restart_mid_review_state_persists_and_worker_reconnects(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    before = stack.get(rid)
    stack.restart()  # API down then up again on the same DB/port; the worker process keeps running
    after = stack.get(rid)
    for k in ("state", "attempt", "budget_no", "commit_sha", "depth", "repo_url"):
        assert after[k] == before[k], k
    assert after["state"] == "running"
    stack.hang_trivy(False)  # worker finishes and reports success to the *new* API instance
    r = stack.wait_state(rid, "completed")
    assert r["attempt"] == 1
    assert_clean_report(stack, rid, baseline, attempt=1)


def test_review_api_down_during_heartbeats_worker_survives(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    stack.stop()
    stack.hang_trivy(False)  # worker posts trivy/ai/report heartbeats while the API is down (logged, not fatal)
    stack.wait(lambda: (Path(stack.settings.reports_dir) / rid / "report.json").exists(), what="report.json")
    stack.start()
    # the final outcome=success callback was lost while the API was down
    assert stack.get(rid)["state"] == "running"
    # reconciliation: the watchdog must eventually close the review without re-scanning from scratch
    stack.wait(lambda: not stack.worker_pids(rid), what="worker exit")
    act = stack.sweep(at=later(minutes=20))
    assert act == {rid: "relaunched"}
    r = stack.wait_state(rid, "completed")
    assert_clean_report(stack, rid, baseline, attempt=2)
    assert stack.calls("semgrep") == 1  # everything resumed from the checkpoint


@pytest.mark.xfail(strict=True, reason="LocalLauncher keeps worker processes in memory: after a review-api restart "
                                       "a live worker reads as 'missing' and the watchdog launches a duplicate attempt "
                                       "(the K8s launcher asks the API server, so prod is not affected)")
def test_restart_then_sweep_does_not_relaunch_live_worker(stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    stack.restart()
    assert stack.sweep() == {}


def test_restart_then_sweep_converges_without_duplicates(baseline, stack):
    """Even with the duplicate launch above, the stale attempt is superseded (409) and the result is clean."""
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    stack.restart()
    stack.sweep()
    stack.hang_trivy(False)
    r = stack.wait_state(rid, "completed")
    assert r["attempt"] == 2
    assert_clean_report(stack, rid, baseline, attempt=2)
    stack.wait(lambda: not stack.worker_pids(rid), what="stale worker exit")


# 3 ---------------------------------------------------------------------------------------------
def _age(rid, **delta):
    """Fake clock: move every timestamp of the review back by `delta` (as if that much time passed)."""
    from datetime import timedelta
    d = timedelta(**delta)
    with dbmod.session_factory()() as db:
        r = db.get(Review, rid)
        for k in ("created_at", "started_at", "paused_at", "last_heartbeat", "updated_at"):
            if getattr(r, k, None) is not None:
                setattr(r, k, getattr(r, k) - d)
        for a in db.query(ReviewAttempt).filter_by(review_id=rid):
            a.started_at = a.started_at - d if a.started_at else None
        db.commit()


def test_two_day_pause_then_resume_completes_from_checkpoint(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    p = stack.post(rid, "pause")
    assert p.status_code == 202 and p.json()["state"] == "paused"
    stack.wait(lambda: not stack.worker_pids(rid), what="worker stopped on pause (SIGTERM)")
    assert stack.work_empty()
    _age(rid, days=2)
    assert stack.sweep(at=later(days=2)) == {}  # the watchdog never touches a paused review
    assert stack.get(rid)["state"] == "paused"
    stack.hang_trivy(False)
    r = stack.post(rid, "resume")
    assert r.status_code == 202 and r.json()["state"] in ("queued", "running")
    assert stack.sweep() == {}  # resume refreshed last_heartbeat: a 2-day-old heartbeat must not count as stuck
    r = stack.wait_state(rid, "completed")
    assert (r["attempt"], r["budget_no"]) == (1, 1)  # pause keeps the same attempt
    assert_clean_report(stack, rid, baseline, attempt=1)
    assert stack.calls("semgrep") == 1


def test_two_day_pause_does_not_expire(stack):
    """No contract defines a pause expiry (ADR-0002: 'may be paused for days'): after 2 days still paused/resumable."""
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    stack.post(rid, "pause")
    _age(rid, days=2)
    for days in (2, 7, 30):
        assert stack.sweep(at=later(days=days)) == {}
    g = stack.get(rid)
    check("Review", g)
    assert g["state"] == "paused"


# 4 ---------------------------------------------------------------------------------------------
def test_five_consecutive_failures_then_retry_opens_new_budget(baseline, stack):
    origin = stack.repo
    hidden = origin.with_name("origin.hidden")
    origin.rename(hidden)  # every clone fails -> worker reports outcome=error
    rid = stack.create()
    acts = []
    for n in range(1, 6):
        stack.wait(lambda: not stack.worker_pids(rid), what=f"attempt {n} exit")
        stack.wait(lambda: stack.get(rid)["attempt"] == n, what=f"attempt {n}")
        acts.append(stack.sweep())
    assert acts[:4] == [{rid: "relaunched"}] * 4 and acts[4] == {rid: "failed"}
    r = stack.get(rid)
    check("Review", r)
    assert (r["state"], r["attempt"], r["budget_no"]) == ("failed", 5, 1)
    with dbmod.session_factory()() as db:
        assert db.query(ReviewAttempt).filter_by(review_id=rid, budget_no=1).count() == 5
    assert stack.post(rid, "resume").status_code == 409  # budget exhausted
    hidden.rename(origin)
    rr = stack.post(rid, "retry")
    assert rr.status_code == 202, rr.text
    assert (rr.json()["state"], rr.json()["attempt"], rr.json()["budget_no"]) == ("retrying", 1, 2)
    r = stack.wait_state(rid, "completed")
    assert (r["attempt"], r["budget_no"]) == (1, 2)
    assert_clean_report(stack, rid, baseline, attempt=1, budget_no=2)


# 5 ---------------------------------------------------------------------------------------------
def test_resumed_report_identical_to_uninterrupted(baseline, stack):
    stack.hang_trivy()
    rid = stack.create("brief")
    stack.wait_trivy_started()
    stack.sigkill_worker(rid)
    stack.post(rid, "pause")  # SIGKILLed job then paused/resumed: two kinds of recovery on one review
    stack.hang_trivy(False)
    assert stack.post(rid, "resume").status_code == 202
    r = stack.wait_state(rid, "completed")
    rep = assert_clean_report(stack, rid, baseline, attempt=r["attempt"], killed=1)
    assert sum(rep["summary"][k] for k in ("critical", "high", "medium", "low", "info")) == len(rep["findings"])


@pytest.mark.xfail(strict=True, reason="low/local-only: a relaunched worker does not remove stale src-<review> clone "
                                       "dirs left in a shared WORK_DIR by a SIGKILLed attempt (K8s uses a per-pod emptyDir)")
def test_relaunch_removes_stale_clone_dirs_of_killed_attempt(baseline, stack):
    stack.hang_trivy()
    rid = stack.create()
    stack.wait_trivy_started()
    stack.sigkill_worker(rid)
    stack.hang_trivy(False)
    stack.sweep()
    stack.wait_state(rid, "completed")
    assert stack.work_empty()
