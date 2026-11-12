"""Phase 4 e2e: login -> new review -> progress -> pause/resume -> report (+ partial).
Runs the real built SPA against mock_stack (contract-shaped gateway). The strict xfails landed in Phase 4 and are now plain tests."""
import json, urllib.request
import pytest
from playwright.sync_api import expect

REPO = "https://github.com/stamparm/DSVW"
POLL = 8000  # ReviewList polls every 3 s


def mset(stack, rid, **kw):
    req = urllib.request.Request(f"{stack.base}/__mock/set/{rid}", json.dumps(kw).encode(), method="POST")
    urllib.request.urlopen(req).read()


def login(page):
    page.goto("/")
    page.get_by_role("link", name="Log in with GitHub").click()
    expect(page.get_by_text("@almog975")).to_be_visible()


def start(page, stack, url=REPO):
    page.get_by_placeholder("https://github.com/owner/repo").fill(url)
    page.get_by_role("button", name="Start review").click()
    expect(page.get_by_role("cell", name="stamparm/DSVW")).to_be_visible()
    return next(iter(stack.reviews))


def row(page):
    return page.get_by_role("row").filter(has_text="stamparm/DSVW")


def test_login_required_then_github_login(page, stack):
    page.goto("/")
    expect(page.get_by_role("link", name="Log in with GitHub")).to_have_attribute("href", "/auth/github/login")
    login(page)
    expect(page.get_by_text("No reviews yet.")).to_be_visible()


def test_invalid_url_blocks_submit(page, stack):
    login(page)
    page.get_by_placeholder("https://github.com/owner/repo").fill("https://gitlab.com/a/b")
    expect(page.get_by_text("Must be https://github.com/")).to_be_visible()
    expect(page.get_by_role("button", name="Start review")).to_be_disabled()


def test_full_flow_progress_to_report(page, stack):
    login(page)
    rid = start(page, stack)
    expect(row(page).locator(".badge")).to_have_text("queued")
    mset(stack, rid, state="running", files_done=3, files_total=10)
    expect(row(page)).to_contain_text("30%", timeout=POLL)
    mset(stack, rid, state="running", files_done=10, files_total=10)
    expect(row(page)).to_contain_text("100%", timeout=POLL)
    mset(stack, rid, state="completed", findings_count=3)
    row(page).get_by_role("button", name="Report").click(timeout=POLL)
    expect(page.get_by_label("severity counts")).to_contain_text("HIGH: 1")
    expect(page.get_by_label("severity counts")).to_contain_text("MEDIUM: 2")
    expect(page.get_by_role("heading", name="Security report")).to_be_visible()
    # raw HTML in a finding must not execute or render as an element
    assert page.evaluate("window.__xss") is None
    assert page.locator(".report script").count() == 0
    page.get_by_role("button", name="← Back").click()
    expect(row(page)).to_be_visible()
    assert not page.errors


def test_pause_resume_with_csrf(page, stack):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state="running", files_done=2, files_total=10)
    expect(row(page)).to_contain_text("20%", timeout=POLL)
    row(page).get_by_role("button", name="Pause").click()
    expect(row(page).locator(".badge")).to_have_text("paused")
    expect(row(page).get_by_role("button", name="Pause")).to_have_count(0)
    row(page).get_by_role("button", name="Resume").click()
    expect(row(page).locator(".badge")).to_have_text("queued")
    posts = [c for c in stack.calls if c[0] == "POST"]
    assert [p[1].rsplit("/", 1)[1] for p in posts] == ["reviews", "pause", "resume"]
    assert all(ok for _, _, ok in posts), "every mutating call must carry X-CSRF-Token"
    # progress kept across pause/resume (checkpoint)
    expect(row(page)).to_contain_text("20%")


def test_paused_review_stops_polling(page, stack):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state="running")  # Pause is offered only for running
    expect(row(page).locator(".badge")).to_have_text("running")
    row(page).get_by_role("button", name="Pause").click()
    expect(row(page).locator(".badge")).to_have_text("paused")
    n = len(stack.calls)
    page.wait_for_timeout(4000)
    assert [c for c in stack.calls[n:] if c[1] == "/api/reviews"] == []


def test_logout(page, stack):
    login(page)
    page.get_by_role("button", name="Log out").click()
    expect(page.get_by_role("link", name="Log in with GitHub")).to_be_visible()
    page.reload()
    expect(page.get_by_role("link", name="Log in with GitHub")).to_be_visible()


# ---------------- landed in Phase 4 (were strict xfail) ----------------

def test_progress_uses_sse(page, stack):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state="running", files_done=1, files_total=4)
    page.wait_for_timeout(4000)
    assert stack.sse_subscribers > 0


@pytest.mark.parametrize("final", ["failed", "cancelled"])
def test_partial_report_reachable(page, stack, final):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state=final, files_done=4, files_total=10, partial=True, failure_reason="budget exhausted")
    row(page).get_by_role("button", name="Report").click(timeout=POLL)
    expect(page.get_by_text("Partial report")).to_be_visible()


def test_cancel_button(page, stack):
    login(page)
    rid = start(page, stack)
    row(page).get_by_role("button", name="Cancel").click(timeout=3000)
    expect(row(page).locator(".badge")).to_have_text("cancelled")


def test_retry_button(page, stack):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state="failed", attempt=5, failure_reason="budget exhausted")
    row(page).get_by_role("button", name="Retry").click(timeout=POLL)
    expect(row(page).locator(".badge")).to_have_text("queued")
    assert stack.reviews[rid]["budget_no"] == 2


def test_pause_conflict_shows_error(page, stack):
    login(page)
    rid = start(page, stack)
    mset(stack, rid, state="running")
    expect(row(page).get_by_role("button", name="Pause")).to_be_visible()
    # Race: the server moves on before the click lands (the UI follows SSE, so simulate the 409 at the network).
    page.route("**/api/reviews/*/pause", lambda route: route.fulfill(
        status=409, content_type="application/json",
        body='{"error": {"code": "CONFLICT", "message": "cannot pause from completed"}, "request_id": "req-test"}'))
    row(page).get_by_role("button", name="Pause").click()
    expect(page.locator(".error")).to_be_visible(timeout=2000)
