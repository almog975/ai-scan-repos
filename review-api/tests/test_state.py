import pytest

from app.errors import ApiError
from app.models import Depth, Review, State
from app.state import MAX_ATTEMPTS, TRANSITIONS, can, next_attempt, open_new_budget, transition
from contract import common_enum


def rv(state=State.queued, **kw):
    return Review(user_id="u", repo_url="https://github.com/a/b", depth=Depth.quick, state=state,
                  attempt=kw.pop("attempt", 1), budget_no=kw.pop("budget_no", 1), **kw)


def test_states_equal_contract():
    assert [s.value for s in State] == common_enum("state")
    assert set(TRANSITIONS) == set(State)


@pytest.mark.parametrize("src,dst,ok", [
    ("queued", "running", True), ("running", "paused", True), ("paused", "running", True),
    ("running", "stuck", True), ("stuck", "retrying", True), ("retrying", "running", True),
    ("stuck", "failed", True), ("failed", "retrying", True), ("running", "completed", True),
    ("running", "cancelled", True),
    ("completed", "running", False), ("cancelled", "running", False), ("failed", "running", False),
    ("queued", "completed", False), ("paused", "completed", False), ("completed", "retrying", False),
])
def test_transitions(src, dst, ok):
    r = rv(State(src))
    if ok:
        transition(r, State(dst))
        assert r.state == State(dst)
    else:
        with pytest.raises(ApiError) as e:
            transition(r, State(dst))
        assert e.value.status == 409 and e.value.code == "CONFLICT"


def test_timestamps_and_budget():
    r = rv()
    transition(r, State.running)
    assert r.started_at and not r.finished_at
    transition(r, State.failed, reason="5 attempts exhausted")
    assert r.finished_at and r.failure_reason
    r.attempt = MAX_ATTEMPTS
    assert next_attempt(r) == 0
    open_new_budget(r)
    assert (r.state, r.budget_no, r.attempt, r.finished_at, r.failure_reason) == (State.retrying, 2, 1, None, None)
    assert next_attempt(r) == 2
    assert not can(State.running, State.retrying)  # retry only via failed/stuck
