from datetime import datetime, timedelta, timezone

import pytest

from app import db as dbmod
from app.models import Outcome, Review, ReviewAttempt, State
from app.watchdog import sweep
from contract import check

REPO = "https://github.com/a/b"


class FakeK8s:
    """Records Job create/delete like the batch API; status per job name."""

    def __init__(self):
        self.launched, self.deleted, self.cleaned, self.st = [], [], [], {}

    def launch(self, r):
        name = f"scan-{r.id}-a{r.attempt}"
        self.launched.append((r.budget_no, r.attempt))
        self.st[name] = "active"
        return name

    def delete(self, name):
        self.deleted.append(name)
        self.st.pop(name, None)

    def cleanup(self, rid):
        self.cleaned.append(rid)

    def status(self, name):
        return self.st.get(name, "missing")


@pytest.fixture
def k8s(app):
    f = FakeK8s()
    app.state.deps["launcher"] = f
    return f


def create(client, H):
    r = client.post("/reviews", json={"repo_url": REPO, "depth": "quick"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def db():
    return dbmod.session_factory()()


def later(minutes=20):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def test_stale_heartbeat_deletes_job_and_relaunches(client, H, k8s, settings):
    rid = create(client, H)
    with db() as s:
        assert sweep(s, k8s, settings) == {}  # fresh heartbeat, job active
        assert sweep(s, k8s, settings, at=later()) == {rid: "relaunched"}
        r = s.get(Review, rid)
        assert r.state == State.retrying and r.attempt == 2 and r.budget_no == 1
    assert k8s.deleted == [f"scan-{rid}-a1"] and k8s.launched[-1] == (1, 2)
    att = client.get(f"/reviews/{rid}/attempts", headers=H).json()
    assert [(a["n"], a["outcome"]) for a in att] == [(1, "stuck"), (2, None)]
    check({"type": "array", "items": {"$ref": "urn:review-api#/components/schemas/ReviewAttempt"}}, att)


def test_failed_job_is_stuck_immediately(client, H, k8s, settings):
    rid = create(client, H)
    k8s.st[f"scan-{rid}-a1"] = "failed"
    with db() as s:
        assert sweep(s, k8s, settings) == {rid: "relaunched"}


def test_worker_reported_error(client, H, k8s, settings):
    rid = create(client, H)
    with db() as s:
        a = s.query(ReviewAttempt).filter_by(review_id=rid).one()
        a.outcome, a.error, a.ended_at = Outcome.error, "boom", datetime.now(timezone.utc)
        s.commit()
        sweep(s, k8s, settings)
        a = s.query(ReviewAttempt).filter_by(review_id=rid, n=1).one()
        assert a.outcome == Outcome.error and a.error == "boom"  # worker's outcome kept


def test_cap_of_5_then_failed_then_retry_new_budget(client, H, k8s, settings):
    rid = create(client, H)
    with db() as s:
        for i in range(1, 6):
            res = sweep(s, k8s, settings, at=later(20 * i))
            assert res == {rid: "relaunched" if i < 5 else "failed"}
        r = s.get(Review, rid)
        assert r.state == State.failed and r.attempt == 5 and "exhausted" in r.failure_reason
        assert sweep(s, k8s, settings, at=later(200)) == {}
    assert [n for b, n in k8s.launched] == [1, 2, 3, 4, 5] and len(k8s.deleted) == 5 and k8s.cleaned == [rid]
    att = client.get(f"/reviews/{rid}/attempts", headers=H).json()
    assert len(att) == 5 and all(a["outcome"] == "stuck" for a in att)

    r = client.post(f"/reviews/{rid}/retry", headers=H)
    assert r.status_code == 202, r.text
    check("Review", r.json())
    assert r.json()["state"] == "retrying" and r.json()["budget_no"] == 2 and r.json()["attempt"] == 1
    assert k8s.launched[-1] == (2, 1)
    assert len(client.get(f"/reviews/{rid}/attempts?budget_no=2", headers=H).json()) == 1
    assert len(client.get(f"/reviews/{rid}/attempts?budget_no=1", headers=H).json()) == 5
    assert len(client.get(f"/reviews/{rid}/attempts", headers=H).json()) == 6


def test_retry_only_from_failed(client, H, A, k8s):
    rid = create(client, H)
    r = client.post(f"/reviews/{rid}/retry", headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    check("Error", r.json())
    other = client.post("/reviews/00000000-0000-0000-0000-000000000000/retry", headers=H)
    assert other.status_code == 404
    assert client.get(f"/reviews/{rid}/attempts?budget_no=0", headers=H).status_code == 400


def test_relaunch_launch_error_recorded(client, H, k8s, settings):
    rid = create(client, H)

    def boom(r):
        raise RuntimeError("api down")
    k8s.launch = boom
    with db() as s:
        sweep(s, k8s, settings, at=later())
        a2 = s.query(ReviewAttempt).filter_by(review_id=rid, n=2).one()
        assert a2.outcome == Outcome.error and "api down" in a2.error
