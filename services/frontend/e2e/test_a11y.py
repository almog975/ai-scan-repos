"""Phase 4 DoD: axe-core (WCAG 2.1 A/AA) on every screen of the login -> scan -> progress -> report flow,
in English (LTR) and Hebrew (RTL). Uses the axe-core build from node_modules (installed by npm ci)."""
import os
import pytest
from playwright.sync_api import expect
from test_e2e_flow import login, mset, row, start

AXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_modules", "axe-core", "axe.min.js")


def axe(page, where):
    page.add_script_tag(path=AXE)
    res = page.evaluate("async () => (await axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']})).violations")
    bad = [(v["id"], v["impact"], [n["target"] for n in v["nodes"]][:3]) for v in res]
    assert bad == [], f"{where}: {bad}"


@pytest.mark.parametrize("lang,dirn", [("en", "ltr"), ("he", "rtl")])
def test_axe_flow(page, stack, lang, dirn):
    page.goto(f"/login?lang={lang}")
    expect(page.locator("html")).to_have_attribute("dir", dirn)
    axe(page, "login")
    page.goto("/")
    page.locator('a[href="/auth/github/login"]').click()
    expect(page.get_by_text("@almog975")).to_be_visible()
    axe(page, "home (empty)")
    page.locator("#repo-url, input[type=url], input[placeholder='https://github.com/owner/repo']").first.fill("https://github.com/stamparm/DSVW")
    page.locator("form button[type=submit]").click()
    expect(page.get_by_role("cell", name="stamparm/DSVW")).to_be_visible()
    rid = next(iter(stack.reviews))
    mset(stack, rid, state="running", files_done=3, files_total=10)
    expect(row(page)).to_contain_text("30%")
    axe(page, "home (list)")
    page.goto(f"/reviews/{rid}")
    expect(page.locator("h1")).to_be_visible()
    axe(page, "progress")
    mset(stack, rid, state="completed", files_done=10)
    page.goto(f"/reviews/{rid}/report")
    expect(page.locator(".report")).to_be_visible()
    axe(page, "report")
    assert not page.errors
