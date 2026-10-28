import hashlib
import hmac
import json

import httpx

from app.security import verify_github_signature
from tests.conftest import GH, RA, do_login, make_client


def csrf(c):
    return {"X-CSRF-Token": c.cookies.get("aisr_csrf")}


def test_health_metrics():
    c, _ = make_client()
    assert c.get("/healthz").json() == {"status": "ok"}
    assert b"ai_scan_gateway_logins_total" in c.get("/metrics").content


def test_login_redirect_and_state_check():
    c, router = make_client()
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{GH}/login/oauth/authorize?")
    assert "redirect_uri=http%3A%2F%2Fapp.test%2Fauth%2Fcallback" in r.headers["location"]
    assert c.get("/auth/callback?code=x&state=WRONG").status_code == 400
    assert c.get("/auth/callback?state=x").status_code == 400


def test_oauth_not_configured():
    c, _ = make_client(github_client_id="")
    assert c.get("/auth/login", follow_redirects=False).status_code == 503


def test_callback_token_failure():
    c, router = make_client()
    router.post(f"{GH}/login/oauth/access_token").respond(200, json={"error": "bad_verification_code"})
    c.get("/auth/login", follow_redirects=False)
    st = c.cookies.get("aisr_oauth_state")
    assert c.get(f"/auth/callback?code=x&state={st}").status_code == 400


def test_login_sets_cookies_and_me(logged_in):
    c, _ = logged_in
    me = c.get("/auth/me").json()
    assert me["login"] == "almog" and me["role"] == "user"
    assert c.cookies.get("aisr_session") and c.cookies.get("aisr_csrf")


def test_cookie_flags():
    c, router = make_client(base_url="https://app.test", cookie_secure=True)
    r = do_login(c, router)
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("aisr_session=")][0].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=lax" in sc
    csrf_c = [h for h in r.headers.get_list("set-cookie") if h.startswith("aisr_csrf=")][0].lower()
    assert "httponly" not in csrf_c


def test_relogin_upserts_and_admin():
    c, router = make_client(admin_github_logins="Almog")
    do_login(c, router)
    do_login(c, router, login="almog")
    assert c.get("/auth/me").json()["role"] == "admin"


def test_allowlist():
    c, router = make_client(allowed_github_logins="someone-else")
    assert do_login(c, router).status_code == 403


def test_unauthenticated():
    c, _ = make_client()
    assert c.get("/api/reviews").status_code == 401
    assert c.get("/auth/me").status_code == 401
    c.cookies.set("aisr_session", "bogus")
    assert c.get("/auth/me").status_code == 401


def test_proxy_injects_and_strips_headers(logged_in):
    c, router = logged_in
    uid = c.get("/auth/me").json()["id"]
    route = router.get(f"{RA}/reviews").respond(200, json=[])
    r = c.get("/api/reviews", headers={"X-User-Id": "attacker", "X-Internal-Token": "forged"})
    assert r.status_code == 200 and r.json() == []
    req = route.calls.last.request
    assert req.headers["x-user-id"] == uid and req.headers["x-internal-token"] == "itok"
    assert "cookie" not in req.headers


def test_proxy_post_requires_csrf(logged_in):
    c, router = logged_in
    route = router.post(f"{RA}/reviews").respond(201, json={"id": "r1"})
    body = {"repo_url": "https://github.com/a/b", "depth": "quick"}
    assert c.post("/api/reviews", json=body).status_code == 403
    assert c.post("/api/reviews", json=body, headers={"X-CSRF-Token": "nope"}).status_code == 403
    r = c.post("/api/reviews", json=body, headers=csrf(c))
    assert r.status_code == 201 and json.loads(route.calls.last.request.content) == body
    router.post(f"{RA}/reviews/r1/pause").respond(200, json={"status": "paused"})
    assert c.post("/api/reviews/r1/pause", headers=csrf(c)).json()["status"] == "paused"
    router.get(f"{RA}/reviews/r1/report.md").respond(200, text="# R", headers={"content-type": "text/plain"})
    assert c.get("/api/reviews/r1/report.md").text == "# R"
    router.put(f"{RA}/ai-keys").respond(204)
    assert c.put("/api/ai-keys", json={"provider": "openai", "api_key": "k"}, headers=csrf(c)).status_code == 204


def test_logout(logged_in):
    c, _ = logged_in
    assert c.post("/auth/logout").status_code == 403  # csrf
    assert c.post("/auth/logout", headers=csrf(c)).status_code == 200
    assert c.get("/auth/me").status_code == 401


def sign(body: bytes, secret="whsecret"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_helper():
    assert verify_github_signature("s", b"x", sign(b"x", "s"))
    assert not verify_github_signature("s", b"x", sign(b"y", "s"))
    assert not verify_github_signature("", b"x", sign(b"x", ""))
    assert not verify_github_signature("s", b"x", None)


def test_webhook_rejects_unsigned_and_bad():
    c, _ = make_client()
    assert c.post("/webhooks/github", content=b"{}").status_code == 401
    assert c.post("/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": sign(b"{}", "wrong")}).status_code == 401


def test_webhook_ping_and_push_noop():
    c, _ = make_client()
    b = b"{}"
    assert c.post("/webhooks/github", content=b, headers={"X-Hub-Signature-256": sign(b), "X-GitHub-Event": "ping"}).json()["event"] == "ping"
    assert c.post("/webhooks/github", content=b, headers={"X-Hub-Signature-256": sign(b), "X-GitHub-Event": "issues"}).json()["ignored"] == "issues"
    assert c.post("/webhooks/github", content=b, headers={"X-Hub-Signature-256": sign(b), "X-GitHub-Event": "push"}).json()["review"] is None


def test_webhook_push_creates_review():
    c, router = make_client(webhook_auto_review=True)
    do_login(c, router, uid=7)
    route = router.post(f"{RA}/reviews").respond(201, json={"id": "rv"})
    push = json.dumps({"sender": {"id": 7}, "repository": {"html_url": "https://github.com/a/b"}}).encode()
    h = {"X-Hub-Signature-256": sign(push), "X-GitHub-Event": "push"}
    assert c.post("/webhooks/github", content=push, headers=h).json()["review"] == "rv"
    assert route.calls.last.request.headers["x-internal-token"] == "itok"
    unknown = json.dumps({"sender": {"id": 999}, "repository": {"html_url": "https://github.com/a/b"}}).encode()
    assert c.post("/webhooks/github", content=unknown, headers={**h, "X-Hub-Signature-256": sign(unknown)}).json()["review"] is None
    router.post(f"{RA}/reviews").respond(422)
    assert c.post("/webhooks/github", content=push, headers=h).status_code == 502
