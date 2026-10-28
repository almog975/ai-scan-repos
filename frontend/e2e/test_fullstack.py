"""Opt-in (AISCAN_FULLSTACK=1 + AISCAN_WORKER_PYTHON): the built SPA against the REAL stack:
front proxy (static dist + the exact add_header set of frontend/nginx.conf, incl. CSP) -> gateway (uvicorn,
real GitHub OAuth flow against a fake GitHub) -> review-api (uvicorn, sqlite, LocalLauncher) -> scanner-worker
subprocesses with review-api/tests/phase3_shims. Verifies the flow end to end and that the SPA runs under the CSP."""
import http.client, json, os, re, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (os.environ.get("AISCAN_FULLSTACK") and os.environ.get("AISCAN_WORKER_PYTHON")),
                                reason="set AISCAN_FULLSTACK=1 and AISCAN_WORKER_PYTHON")
sys.path[:0] = [str(ROOT / "review-api"), str(ROOT / "review-api" / "tests")]
NGINX = (ROOT / "frontend" / "nginx.conf").read_text()
HEADERS = [(m.group(1), m.group(3) or m.group(4)) for m in
           re.finditer(r'add_header\s+([\w-]+)\s+("([^"]*)"|(\S+))\s*(always)?\s*;', NGINX)]
PROXIED = re.compile(r"^/(api|auth|webhooks|me)(/|$)")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def fake_github(port, front):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def reply(self, code, body=b"", hdr=()):
            self.send_response(code)
            for k, v in hdr: self.send_header(k, v)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/login/oauth/authorize":
                st = parse_qs(u.query)["state"][0]
                return self.reply(302, hdr=[("Location", f"{front}/auth/github/callback?" + urlencode({"code": "c1", "state": st}))])
            if u.path == "/user":
                return self.reply(200, json.dumps({"id": 975, "login": "almog975", "avatar_url": None, "email": None}).encode(),
                                  [("Content-Type", "application/json")])
            self.reply(404)
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.reply(200, b'{"access_token":"gho_fake","token_type":"bearer"}', [("Content-Type", "application/json")])
    s = ThreadingHTTPServer(("127.0.0.1", port), H); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def front_proxy(port, dist, gw_port):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def sec(self):
            for k, v in HEADERS: self.send_header(k, v)
        def handle_any(self):
            path = urlparse(self.path).path
            if PROXIED.match(path):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                c = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=120)
                hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
                c.request(self.command, self.path, body=body or None, headers=hdr)
                r = c.getresponse()
                self.send_response(r.status)
                sse = (r.getheader("Content-Type") or "").startswith("text/event-stream")
                for k, v in r.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"): self.send_header(k, v)
                self.sec()
                if sse:
                    self.send_header("Connection", "close"); self.end_headers()
                    try:
                        while True:
                            ch = r.read1(4096) if hasattr(r, "read1") else r.read(1)
                            if not ch: break
                            self.wfile.write(ch); self.wfile.flush()
                    except OSError: pass
                    self.close_connection = True; return
                data = r.read(); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            f = os.path.normpath(os.path.join(dist, path.lstrip("/")))
            if not f.startswith(dist) or not os.path.isfile(f): f = os.path.join(dist, "index.html")
            ct = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}.get(os.path.splitext(f)[1], "application/octet-stream")
            data = open(f, "rb").read()
            self.send_response(200); self.send_header("Content-Type", ct); self.sec()
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        do_GET = do_POST = do_PATCH = do_DELETE = handle_any
    s = ThreadingHTTPServer(("127.0.0.1", port), H); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start(); return s


@pytest.fixture
def full(tmp_path, monkeypatch, dist):
    from phase3_harness import Stack, TOKEN
    st = Stack(tmp_path, monkeypatch)
    fport, gport, ghport = free_port(), free_port(), free_port()
    front = f"http://127.0.0.1:{fport}"
    gh = fake_github(ghport, front)
    env = dict(os.environ, DATABASE_URL=st.db_url, REVIEW_API_URL=f"http://127.0.0.1:{st.port}", INTERNAL_API_TOKEN=TOKEN,
               GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="csec", GITHUB_OAUTH_BASE=f"http://127.0.0.1:{ghport}",
               GITHUB_API_BASE=f"http://127.0.0.1:{ghport}", PUBLIC_URL=front, COOKIE_SECURE="false")
    gw = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "app.main:get_app", "--port", str(gport),
                           "--log-level", "warning"], cwd=ROOT / "gateway", env=env)
    for _ in range(100):
        try: socket.create_connection(("127.0.0.1", gport), 0.2).close(); break
        except OSError: time.sleep(0.1)
    fp = front_proxy(fport, os.path.abspath(dist), gport)
    st.front = front
    yield st
    fp.shutdown(); gw.terminate(); gw.wait(10); gh.shutdown(); st.close()


@pytest.fixture
def fpage(browser, full):
    ctx = browser.new_context(base_url=full.front)
    pg = ctx.new_page()
    pg.csp = []; pg.errors = []
    pg.on("console", lambda m: "Content Security Policy" in m.text and pg.csp.append(m.text))
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


ROW = "aiscan-fixture/vuln-app"


def go_login_and_start(page, full):
    page.goto("/")
    page.get_by_role("link", name="Log in with GitHub").click()
    expect(page.get_by_text("@almog975")).to_be_visible(timeout=15000)
    # gateway's first user is a new users row; review-api shares the sqlite DB
    page.get_by_placeholder("https://github.com/owner/repo").fill(f"https://github.com/{ROW}")
    page.get_by_role("button", name="Start review").click()
    expect(page.get_by_role("cell", name=ROW)).to_be_visible(timeout=15000)
    return page.get_by_role("row").filter(has_text=ROW)


def latest(full):
    from app import db as dbmod
    from app.models import Review
    with dbmod.session_factory()() as db:
        return db.query(Review).order_by(Review.created_at.desc()).first().id


def test_fullstack_scan_to_report(fpage, full):
    row = go_login_and_start(fpage, full)
    full.track()
    expect(row.locator(".badge")).to_have_text(re.compile("completed"), timeout=90000)
    row.get_by_role("button", name="Report").click()
    expect(fpage.locator(".report")).to_be_visible(timeout=15000)
    expect(fpage.locator(".report")).to_contain_text(re.compile("HIGH", re.I))
    assert fpage.csp == [], fpage.csp
    assert not fpage.errors, fpage.errors
    # really went through the worker + shims, and the page really ran under the nginx CSP
    assert full.calls("semgrep") and full.calls("trivy")
    r = fpage.request.get("/")
    assert "style-src 'self'" in r.headers["content-security-policy"]
    assert fpage.evaluate("document.cookie").find("aiscan_csrf=") >= 0


def test_fullstack_pause_resume_live(fpage, full):
    full.hang_trivy(True)
    row = go_login_and_start(fpage, full)
    full.track()
    full.wait_trivy_started()
    expect(row.locator(".badge")).to_have_text(re.compile("running"), timeout=30000)
    row.get_by_role("button", name="Pause").click()
    expect(row.locator(".badge")).to_have_text(re.compile("paused"), timeout=15000)
    full.hang_trivy(False)
    row.get_by_role("button", name="Resume").click()
    full.track()
    expect(row.locator(".badge")).to_have_text(re.compile("completed"), timeout=90000)
    assert fpage.csp == [], fpage.csp
