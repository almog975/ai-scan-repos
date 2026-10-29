import hashlib
import hmac
import json
from pathlib import Path

import httpx
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.routes import ROUTES
from app.security import verify_github_signature
from tests.conftest import GH, RA, do_login, make_client

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
RID = "0b8f3a52-2c1e-4c55-9a77-3f4e0c1d2a10"


def _validator(name):
    reg = Registry()
    for p in CONTRACTS.glob("*.schema.json"):
        reg = reg.with_resource(p.name, Resource.from_contents(json.loads(p.read_text())))
    spec = yaml.safe_load((CONTRACTS / "gateway.openapi.yaml").read_text())
    reg = reg.with_resource("urn:gw", Resource.from_contents(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:gw", **spec}))
    return Draft202012Validator({"$ref": f"urn:gw#/components/schemas/{name}"}, registry=reg,
                                format_checker=FormatChecker())


def check(name, payload):
    errs = list(_validator(name).iter_errors(payload))
    assert not errs, [e.message for e in errs]


def csrf(c):
    return {"X-CSRF-Token": c.cookies.get("aiscan_csrf")}


def err(r, status, code):
    assert r.status_code == status, r.text
    check("Error", r.json())
    assert r.json()["error"]["code"] == code
    return r


def test_route_table_equals_spec():
    gw = yaml.safe_load((CONTRACTS / "gateway.openapi.yaml").read_text())
    ra = yaml.safe_load((CONTRACTS / "review-api.openapi.yaml").read_text())
    want = {}
    for p, item in gw["paths"].items():
        if not p.startswith("/api/"):
            continue
        ref = item["$ref"].split("#/paths/")[1].replace("~1", "/")
        want[p] = sorted(m.upper() for m in ra["paths"][ref] if m in ("get", "post", "put", "patch", "delete"))
    assert {p: sorted(m) for p, m in ROUTES.items()} == want


def test_app_routes_match_spec_exactly():
    c, _ = make_client()
    gw = yaml.safe_load((CONTRACTS / "gateway.openapi.yaml").read_text())
    served = {r.path for r in c.app.routes if hasattr(r, "methods")}
    assert served == set(gw["paths"])


def test_health_metrics():
    c, _ = make_client()
    assert c.get("/healthz").json() == {"status": "ok"}
    assert c.get("/readyz").json() == {"status": "ready"}
    assert b"ai_scan_gateway_logins_total" in c.get("/metrics").content


def test_login_redirect_and_state_check():
    c, router = make_client()
    r = c.get("/auth/github/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{GH}/login/oauth/authorize?")
    assert "redirect_uri=http%3A%2F%2Fapp.test%2Fauth%2Fgithub%2Fcallback" in r.headers["location"]
    assert "scope=read%3Auser+repo" in r.headers["location"]
    err(c.get("/auth/github/callback?code=x&state=WRONG"), 400, "VALIDATION_ERROR")
    err(c.get("/auth/github/callback?state=x"), 400, "VALIDATION_ERROR")


def test_callback_github_errors():
    c, router = make_client()
    router.post(f"{GH}/login/oauth/access_token").respond(200, json={"error": "bad_verification_code"})
    c.get("/auth/github/login", follow_redirects=False)
    st = c.cookies.get("aiscan_oauth_state")
    err(c.get(f"/auth/github/callback?code=x&state={st}"), 502, "INTERNAL")
    router.post(f"{GH}/login/oauth/access_token").mock(side_effect=httpx.ConnectError("down"))
    err(c.get(f"/auth/github/callback?code=x&state={st}"), 502, "INTERNAL")


def test_first_user_admin_and_me_contract():
    c, router = make_client()
    do_login(c, router, login="first", uid=1)
    me = c.get("/me").json()
    check("Me", me)
    assert me["role"] == "admin" and me["github_login"] == "first" and me["avatar_url"].endswith("/1")
    c2, r2 = make_client()  # separate DB per app; same app second user:
    c.cookies.clear()
    do_login(c, router, login="second", uid=2)
    assert c.get("/me").json()["role"] == "user"
    do_login(c, router, login="first", uid=1)
    assert c.get("/me").json()["role"] == "admin"  # re-login keeps role


def test_cookie_flags():
    c, router = make_client(base_url="https://app.test", cookie_secure=True)
    r = do_login(c, router)
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("aiscan_session=")][0].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=lax" in sc
    csrf_c = [h for h in r.headers.get_list("set-cookie") if h.startswith("aiscan_csrf=")][0].lower()
    assert "httponly" not in csrf_c


def test_admin_logins_and_allowlist():
    c, router = make_client(admin_github_logins="Almog")
    do_login(c, router, login="other", uid=1)
    c.cookies.clear()
    do_login(c, router, login="almog", uid=2)
    assert c.get("/me").json()["role"] == "admin"
    c, router = make_client(allowed_github_logins="someone-else")
    err(do_login(c, router), 403, "FORBIDDEN")


def test_unauthenticated():
    c, _ = make_client()
    err(c.get("/api/reviews"), 401, "UNAUTHORIZED")
    err(c.get("/me"), 401, "UNAUTHORIZED")
    c.cookies.set("aiscan_session", "bogus")
    err(c.get("/me"), 401, "UNAUTHORIZED")


def test_unlisted_routes_404(logged_in):
    c, router = logged_in
    catch = router.route(url__startswith=RA).respond(200, json={})
    for m, p in [("GET", "/api/internal/reviews/x/progress"), ("GET", "/api/ai-keys"), ("GET", "/api/healthz"),
                 ("DELETE", "/api/reviews"), ("GET", "/api/reviews/x/y/z"), ("GET", "/nope")]:
        err(c.request(m, p, headers=csrf(c)), 404, "NOT_FOUND")
    assert not catch.called


def test_proxy_injects_and_strips_headers(logged_in):
    c, router = logged_in
    me = c.get("/me").json()
    route = router.get(f"{RA}/reviews").respond(200, json={"items": []})
    r = c.get("/api/reviews?page=2", headers={"X-User-Id": "attacker", "X-Internal-Token": "forged",
                                              "X-User-Role": "admin", "Authorization": "x"})
    assert r.status_code == 200 and r.json() == {"items": []}
    req = route.calls.last.request
    assert req.headers["x-user-id"] == me["id"] and req.headers["x-internal-token"] == "itok"
    assert req.headers["x-user-role"] == me["role"] and req.url.params["page"] == "2"
    assert "cookie" not in req.headers and "authorization" not in req.headers


def test_proxy_post_requires_csrf_and_rewrites_location(logged_in):
    c, router = logged_in
    route = router.post(f"{RA}/reviews").respond(201, json={"id": RID}, headers={"Location": f"/reviews/{RID}"})
    body = {"repo_url": "https://github.com/a/b", "depth": "quick"}
    err(c.post("/api/reviews", json=body), 403, "FORBIDDEN")
    err(c.post("/api/reviews", json=body, headers={"X-CSRF-Token": "nope"}), 403, "FORBIDDEN")
    r = c.post("/api/reviews", json=body, headers=csrf(c))
    assert r.status_code == 201 and json.loads(route.calls.last.request.content) == body
    assert r.headers["location"] == f"/api/reviews/{RID}"
    router.post(f"{RA}/reviews/{RID}/pause").respond(202, json={"state": "paused"})
    assert c.post(f"/api/reviews/{RID}/pause", headers=csrf(c)).status_code == 202
    router.get(f"{RA}/reviews/{RID}/report.md").respond(200, text="# R", headers={"content-type": "text/markdown"})
    assert c.get(f"/api/reviews/{RID}/report.md").text == "# R"
    router.put(f"{RA}/admin/settings").respond(403, json={"error": {"code": "FORBIDDEN", "message": "x"}, "request_id": "r"})
    assert c.put("/api/admin/settings", json={}, headers=csrf(c)).status_code == 403
    router.get(f"{RA}/reviews").respond(429, json={}, headers={"Retry-After": "60"})
    assert c.get("/api/reviews").headers["retry-after"] == "60"


def test_sse_relayed(logged_in):
    c, router = logged_in
    sse = b'event: state\ndata: {"state":"running"}\n\nevent: done\ndata: {"state":"completed"}\n\n'

    async def chunks():
        yield sse[:30]
        yield sse[30:]
    router.get(f"{RA}/reviews/{RID}/progress").mock(side_effect=lambda req: httpx.Response(
        200, content=chunks(), headers={"content-type": "text/event-stream"}))
    with c.stream("GET", f"/api/reviews/{RID}/progress") as r:
        assert r.headers["content-type"].startswith("text/event-stream") and r.read() == sse


def test_review_api_down(logged_in):
    c, router = logged_in
    router.get(f"{RA}/reviews").mock(side_effect=httpx.ConnectError("down"))
    err(c.get("/api/reviews"), 502, "INTERNAL")


def test_logout(logged_in):
    c, _ = logged_in
    err(c.post("/auth/logout"), 403, "FORBIDDEN")  # csrf
    assert c.post("/auth/logout", headers=csrf(c)).status_code == 204
    c.cookies.set("aiscan_csrf", "t")
    err(c.get("/me"), 401, "UNAUTHORIZED")


def test_rate_limited():
    c, router = make_client(rate_limit_auth_per_minute=2)
    for _ in range(2):
        c.get("/auth/github/login", follow_redirects=False)
    r = err(c.get("/auth/github/login", follow_redirects=False), 429, "RATE_LIMITED")
    assert int(r.headers["retry-after"]) >= 1


def sign(body: bytes, secret="whsecret"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def wh(c, body: bytes, event="push", delivery="d1", sig=None):
    return c.post("/webhooks/github", content=body, headers={
        "X-Hub-Signature-256": sig or sign(body), "X-GitHub-Event": event, "X-GitHub-Delivery": delivery})


def test_signature_helper():
    assert verify_github_signature("s", b"x", sign(b"x", "s"))
    assert not verify_github_signature("s", b"x", sign(b"y", "s"))
    assert not verify_github_signature("", b"x", sign(b"x", ""))
    assert not verify_github_signature("s", b"x", None)


def test_webhook_hmac():
    c, _ = make_client()
    err(c.post("/webhooks/github", content=b"{}"), 401, "INVALID_SIGNATURE")
    err(wh(c, b"{}", sig=sign(b"{}", "wrong")), 401, "INVALID_SIGNATURE")
    err(wh(c, b'{"a":1}', sig=sign(b'{"a":2}')), 401, "INVALID_SIGNATURE")  # body tampered
    err(make_client(github_webhook_secret="")[0].post("/webhooks/github", content=b"{}",
        headers={"X-Hub-Signature-256": sign(b"{}", "")}), 401, "INVALID_SIGNATURE")


def test_webhook_accept_dedupe_malformed():
    c, _ = make_client()
    r = wh(c, b"{}", event="ping")
    assert r.status_code == 202 and r.json() == {"accepted": True}
    err(wh(c, b"{}", event="ping"), 409, "CONFLICT")  # same X-GitHub-Delivery
    err(wh(c, b"not json", delivery="d2"), 400, "VALIDATION_ERROR")
    err(c.post("/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": sign(b"{}")}), 400, "VALIDATION_ERROR")


def test_webhook_push_creates_review():
    c, router = make_client(webhook_auto_review=True)
    do_login(c, router, uid=7)
    route = router.post(f"{RA}/reviews").respond(201, json={"id": RID})
    push = json.dumps({"sender": {"id": 7}, "after": "a" * 40,
                       "repository": {"html_url": "https://github.com/a/b"}}).encode()
    assert wh(c, push).status_code == 202
    req = route.calls.last.request
    assert req.headers["x-internal-token"] == "itok" and json.loads(req.content)["ref"] == "a" * 40
    unknown = json.dumps({"sender": {"id": 999}, "repository": {"html_url": "https://github.com/a/b"}}).encode()
    assert wh(c, unknown, delivery="d2").status_code == 202 and route.call_count == 1


def test_request_histogram():
    c, _ = make_client()
    c.get("/healthz")
    assert 'http_request_duration_seconds_count{method="GET",route="/healthz",status="200"}' in c.get("/metrics").text


def test_proxy_metrics_low_cardinality(logged_in):
    """Phase 7: proxy counters/latency are labelled by status only; routes use templates, never ids."""
    from prometheus_client import REGISTRY
    c, router = logged_in
    router.get(f"{RA}/reviews/{RID}").respond(200, json={"id": RID})
    n0 = REGISTRY.get_sample_value("gateway_upstream_duration_seconds_count", {"status": "200"}) or 0
    assert c.get(f"/api/reviews/{RID}").status_code == 200
    assert REGISTRY.get_sample_value("gateway_upstream_duration_seconds_count", {"status": "200"}) == n0 + 1
    text = c.get("/metrics").text
    assert "ai_scan_gateway_proxied_total" in text and RID not in text


def test_webhook_replay_new_delivery_id_rejected():
    """Phase 9: same signed body under a new X-GitHub-Delivery is a replay -> 409, no second review."""
    c, router = make_client(webhook_auto_review=True)
    do_login(c, router, uid=7)
    route = router.post(f"{RA}/reviews").respond(201, json={"id": RID})
    import time as _t
    push = json.dumps({"sender": {"id": 7}, "after": "b" * 40, "repository": {
        "html_url": "https://github.com/a/b", "pushed_at": int(_t.time())}}).encode()
    assert wh(c, push, delivery="r1").status_code == 202
    err(wh(c, push, delivery="r2"), 409, "CONFLICT")
    assert route.call_count == 1
    # a different (genuine) push body is still accepted
    push2 = push.replace(b"b" * 40, b"c" * 40)
    assert wh(c, push2, delivery="r3").status_code == 202 and route.call_count == 2


def test_webhook_stale_push_rejected_and_hash_ttl():
    import time as _t
    from datetime import datetime, timedelta, timezone
    c, router = make_client(webhook_auto_review=True, webhook_replay_window_seconds=60)
    old = json.dumps({"repository": {"html_url": "https://github.com/a/b", "pushed_at": int(_t.time()) - 3600}}).encode()
    err(wh(c, old, delivery="s1"), 409, "CONFLICT")
    # hash entries expire after the window
    assert wh(c, b'{"zen":"x"}', event="ping", delivery="p1").status_code == 202
    err(wh(c, b'{"zen":"x"}', event="ping", delivery="p2"), 409, "CONFLICT")
    from app.db import WebhookDelivery
    SL = c.app.state.SessionLocal
    with SL() as db:
        row = db.get(WebhookDelivery, "sha256:" + hashlib.sha256(b'{"zen":"x"}').hexdigest())
        row.received_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.commit()
    assert wh(c, b'{"zen":"x"}', event="ping", delivery="p3").status_code == 202


def test_api_rate_limit_random_cookies_keyed_by_ip():
    import secrets
    c, _ = make_client(rate_limit_api_per_minute=3)
    codes = [c.get("/api/reviews", cookies={"aiscan_session": secrets.token_hex(8)}).status_code for _ in range(5)]
    assert codes[:3] == [401] * 3 and codes[-1] == 429


def test_api_rate_limit_valid_session_per_user():
    c, router = make_client(rate_limit_api_per_minute=3)
    assert do_login(c, router).status_code == 302
    router.get(f"{RA}/reviews").respond(200, json={"items": []})
    tok = c.cookies.get("aiscan_session")
    for _ in range(4):  # same client IP, forged cookies: IP bucket exhausted
        c.get("/api/reviews", cookies={"aiscan_session": "forged"})
    c.cookies.set("aiscan_session", tok)
    assert c.get("/api/reviews").status_code == 200  # user bucket is separate
    codes = [c.get("/api/reviews").status_code for _ in range(3)]
    assert codes[-1] == 429
