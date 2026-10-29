"""Phase 9: default provider kind in the start form, report header without AI."""
from playwright.sync_api import expect
from test_e2e_flow import REPO, login, mset, row

CLOUD = {"id": "11111111-1111-4111-8111-111111111111", "name": "xAI Grok", "kind": "cloud", "cloud_warning": True, "is_default": True}
ONPREM = {"id": "22222222-2222-4222-8222-222222222222", "name": "vLLM", "kind": "onprem", "cloud_warning": False, "is_default": False}


def test_default_cloud_provider_needs_ack(page, stack):
    stack.providers[:] = [CLOUD, ONPREM]
    stack.created.clear()
    try:
        login(page)
        sel = page.locator("#provider")
        expect(sel.locator("option").first).to_have_text("Default provider: xAI Grok (cloud)")
        expect(page.locator(".warn")).to_be_visible()   # default is cloud -> warning without picking it
        page.get_by_placeholder("https://github.com/owner/repo").fill(REPO)
        start = page.get_by_role("button", name="Start review")
        expect(start).to_be_disabled()
        page.locator(".warn input[type=checkbox]").check()
        start.click()
        expect(page.get_by_role("cell", name="stamparm/DSVW")).to_be_visible()
        assert stack.created[-1].get("provider_id") in (None, "") and stack.created[-1]["ack_cloud"] is True
        sel.select_option(ONPREM["id"])
        expect(page.locator(".warn")).to_have_count(0)
    finally:
        stack.providers[:] = []
    assert not page.errors


def test_report_header_without_ai(page, stack):
    login(page)
    page.get_by_placeholder("https://github.com/owner/repo").fill(REPO)
    page.get_by_role("button", name="Start review").click()
    rid = next(r for r in stack.reviews)
    mset(stack, rid, state="completed", files_done=1, files_total=1)
    page.goto(f"/reviews/{rid}/report")
    expect(page.locator(".report")).to_be_visible()
    head = page.locator(".report").first.inner_text()
    assert "none · none" not in head and "no AI" in head, head
    assert not page.errors
