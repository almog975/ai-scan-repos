import os, subprocess, tempfile, pytest
from playwright.sync_api import sync_playwright
from mock_stack import serve

FRONTEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def dist():
    out = tempfile.mkdtemp(prefix="aiscan-e2e-dist-")
    subprocess.run(["npx", "vite", "build", "--outDir", out, "--emptyOutDir"], cwd=FRONTEND, check=True,
                   stdout=subprocess.DEVNULL)
    return out


@pytest.fixture
def stack(dist):
    srv, st = serve(dist)
    st.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield st
    srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, stack):
    ctx = browser.new_context(base_url=stack.base)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()
