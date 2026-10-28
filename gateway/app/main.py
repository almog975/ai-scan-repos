"""gateway: GitHub OAuth, sessions, CSRF, permissions, webhook verification, review-api proxy."""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select

from .config import Settings, get_settings
from .db import GithubAccount, Session, make_sessionmaker
from .security import hash_token, new_token, verify_github_signature

log = logging.getLogger("gateway")
LOGINS = Counter("ai_scan_gateway_logins_total", "OAuth logins", ["result"])
WEBHOOKS = Counter("ai_scan_gateway_webhooks_total", "Webhook deliveries", ["result"])
PROXIED = Counter("ai_scan_gateway_proxied_total", "Proxied API requests", ["status"])

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
HOP_HEADERS = {"x-internal-token", "x-user-id", "cookie", "host", "content-length", "connection",
               "authorization", "x-csrf-token", "transfer-encoding"}
STATE_COOKIE = "aisr_oauth_state"


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or get_settings()
    SessionLocal = make_sessionmaker(s.database_url)
    app = FastAPI(title="AI Scan Repos — gateway", version="0.2.0")
    allowed, admins = s.logins(s.allowed_github_logins), s.logins(s.admin_github_logins)

    def http():
        return httpx.AsyncClient(timeout=20, transport=transport)

    def cookie(resp: Response, name: str, value: str, max_age: int, httponly=True):
        resp.set_cookie(name, value, max_age=max_age, httponly=httponly, secure=s.cookie_secure,
                        samesite="lax", path="/")

    # ---------- CSRF (double-submit cookie) ----------
    @app.middleware("http")
    async def csrf(request: Request, call_next):
        p = request.url.path
        if request.method in UNSAFE and (p.startswith("/api/") or p == "/auth/logout"):
            c, h = request.cookies.get(s.csrf_cookie), request.headers.get("x-csrf-token")
            if not c or not h or not hmac.compare_digest(c.encode(), h.encode()):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)

    def current_account(request: Request) -> GithubAccount:
        tok = request.cookies.get(s.session_cookie)
        if not tok:
            raise HTTPException(401, "not logged in")
        with SessionLocal() as db:
            sess = db.get(Session, hash_token(tok))
            if not sess or _aware(sess.expires_at) < datetime.now(timezone.utc):
                raise HTTPException(401, "session expired")
            acc = db.get(GithubAccount, sess.account_id)
            if not acc or (allowed and acc.login.lower() not in allowed):
                raise HTTPException(403, "not allowed")
            return acc

    # ---------- probes ----------
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        from sqlalchemy import text
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            raise HTTPException(503, "db not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---------- OAuth ----------
    @app.get("/auth/login")
    def login():
        if not s.github_client_id:
            raise HTTPException(503, "GitHub OAuth not configured")
        state = new_token()
        q = urlencode({"client_id": s.github_client_id, "redirect_uri": f"{s.public_url}/auth/callback",
                       "scope": "read:user", "state": state, "allow_signup": "false"})
        resp = RedirectResponse(f"{s.github_oauth_base}/login/oauth/authorize?{q}", status_code=302)
        cookie(resp, STATE_COOKIE, state, 600)
        return resp

    @app.get("/auth/callback")
    async def callback(request: Request, code: str = "", state: str = ""):
        expected = request.cookies.get(STATE_COOKIE)
        if not code or not state or not expected or not hmac.compare_digest(state.encode(), expected.encode()):
            LOGINS.labels("bad_state").inc()
            raise HTTPException(400, "invalid OAuth state")
        async with http() as c:
            tr = await c.post(f"{s.github_oauth_base}/login/oauth/access_token", headers={"Accept": "application/json"},
                              data={"client_id": s.github_client_id, "client_secret": s.github_client_secret,
                                    "code": code, "redirect_uri": f"{s.public_url}/auth/callback"})
            gh_token = tr.json().get("access_token") if tr.status_code == 200 else None
            if not gh_token:
                LOGINS.labels("token_error").inc()
                raise HTTPException(400, "GitHub token exchange failed")
            ur = await c.get(f"{s.github_api_base}/user",
                             headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"})
            ur.raise_for_status()
            user = ur.json()
        login_name = user["login"]
        if allowed and login_name.lower() not in allowed:
            LOGINS.labels("denied").inc()
            raise HTTPException(403, "GitHub user not in allowlist")
        tok = new_token()
        with SessionLocal() as db:
            acc = db.scalars(select(GithubAccount).where(GithubAccount.github_user_id == user["id"])).first()
            if not acc:
                acc = GithubAccount(github_user_id=user["id"], login=login_name)
                db.add(acc)
            acc.login = login_name
            acc.role = "admin" if login_name.lower() in admins else "user"
            db.flush()
            db.add(Session(id=hash_token(tok), account_id=acc.id,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours)))
            db.commit()
        # GitHub token is not stored: we only need identity.
        LOGINS.labels("ok").inc()
        resp = RedirectResponse(f"{s.public_url}/", status_code=302)
        ttl = s.session_ttl_hours * 3600
        cookie(resp, s.session_cookie, tok, ttl)
        cookie(resp, s.csrf_cookie, new_token(), ttl, httponly=False)  # JS reads it, echoes in X-CSRF-Token
        resp.delete_cookie(STATE_COOKIE, path="/")
        return resp

    @app.get("/auth/me")
    def me(request: Request, acc: GithubAccount = Depends(current_account)):
        resp = JSONResponse({"id": acc.id, "login": acc.login, "role": acc.role})
        if not request.cookies.get(s.csrf_cookie):
            cookie(resp, s.csrf_cookie, new_token(), s.session_ttl_hours * 3600, httponly=False)
        return resp

    @app.post("/auth/logout")
    def logout(request: Request):
        tok = request.cookies.get(s.session_cookie)
        if tok:
            with SessionLocal() as db:
                sess = db.get(Session, hash_token(tok))
                if sess:
                    db.delete(sess)
                    db.commit()
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(s.session_cookie, path="/")
        resp.delete_cookie(s.csrf_cookie, path="/")
        return resp

    # ---------- proxy to review-api ----------
    async def forward(request: Request, path: str, user_id: str) -> Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        headers["X-Internal-Token"] = s.internal_api_token
        headers["X-User-Id"] = user_id
        async with http() as c:
            r = await c.request(request.method, f"{s.review_api_url}{path}", params=request.query_params,
                                content=await request.body(), headers=headers)
        PROXIED.labels(str(r.status_code)).inc()
        out = {k: v for k, v in r.headers.items() if k.lower() in ("content-type",)}
        return Response(r.content, status_code=r.status_code, headers=out)

    @app.api_route("/api/reviews", methods=["GET", "POST"])
    @app.api_route("/api/reviews/{rest:path}", methods=["GET", "POST"])
    async def proxy_reviews(request: Request, rest: str = "", acc: GithubAccount = Depends(current_account)):
        return await forward(request, "/reviews" + (f"/{rest}" if rest else ""), acc.id)

    @app.api_route("/api/ai-keys", methods=["GET", "PUT"])
    async def proxy_keys(request: Request, acc: GithubAccount = Depends(current_account)):
        return await forward(request, "/ai-keys", acc.id)

    # ---------- GitHub webhooks ----------
    @app.post("/webhooks/github")
    async def webhook(request: Request):
        body = await request.body()
        if not verify_github_signature(s.github_webhook_secret, body, request.headers.get("x-hub-signature-256")):
            WEBHOOKS.labels("bad_signature").inc()
            raise HTTPException(401, "invalid signature")
        event = request.headers.get("x-github-event", "")
        if event == "ping":
            WEBHOOKS.labels("ping").inc()
            return {"ok": True, "event": "ping"}
        if event != "push":
            WEBHOOKS.labels("ignored").inc()
            return {"ok": True, "ignored": event}
        payload = json.loads(body or b"{}")
        if not s.webhook_auto_review:
            WEBHOOKS.labels("push_noop").inc()
            return {"ok": True, "review": None}
        sender_id = (payload.get("sender") or {}).get("id")
        repo_url = (payload.get("repository") or {}).get("html_url")
        with SessionLocal() as db:
            acc = db.scalars(select(GithubAccount).where(GithubAccount.github_user_id == sender_id)).first()
        if not acc or not repo_url or (allowed and acc.login.lower() not in allowed):
            WEBHOOKS.labels("push_unknown_sender").inc()
            return {"ok": True, "review": None, "reason": "sender has no account"}
        async with http() as c:
            r = await c.post(f"{s.review_api_url}/reviews", json={"repo_url": repo_url, "depth": s.webhook_review_depth},
                             headers={"X-Internal-Token": s.internal_api_token, "X-User-Id": acc.id})
        WEBHOOKS.labels(f"push_{r.status_code}").inc()
        if r.status_code != 201:
            raise HTTPException(502, "review-api rejected review")
        return {"ok": True, "review": r.json().get("id")}

    return app


def get_app():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    return create_app()
