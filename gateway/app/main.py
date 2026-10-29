"""gateway (contract: contracts/gateway.openapi.yaml).

Browser -> gateway: `aiscan_session` cookie (HttpOnly; Secure; SameSite=Lax) + double-submit CSRF on unsafe
/api/* and /auth/logout. Gateway -> review-api: X-Internal-Token + X-User-Id + X-User-Role, only for the
explicit routes in app/routes.py. GitHub webhooks: HMAC X-Hub-Signature-256 (401 INVALID_SIGNATURE),
deduped by X-GitHub-Delivery (409).
"""
from __future__ import annotations

import hashlib
import hmac
import time
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from . import errors
from .config import Settings, get_settings
from .db import Session, User, WebhookDelivery, make_sessionmaker
from .errors import ApiError, error_response
from .ratelimit import RateLimiter
from .routes import ROUTES, STREAMING
from .security import hash_token, new_token, verify_github_signature

log = logging.getLogger("gateway")
LOGINS = Counter("ai_scan_gateway_logins_total", "OAuth logins", ["result"])
WEBHOOKS = Counter("ai_scan_gateway_webhooks_total", "Webhook deliveries", ["result"])
REQUESTS = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route", "status"])
UPSTREAM = Histogram("gateway_upstream_duration_seconds", "review-api response time (to headers) seen by the proxy",
                     ["status"], buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30))
PROXIED = Counter("ai_scan_gateway_proxied_total", "Proxied API requests", ["status"])
# pre-initialise the bounded result labels to 0 so increase() sees the first event after a restart
for _r in ("ok", "bad_state", "token_error", "github_error", "denied"):
    LOGINS.labels(_r)
for _r in ("bad_signature", "duplicate", "accepted", "ignored", "ping", "unknown_sender", "review_api_unavailable",
           "review_created"):
    WEBHOOKS.labels(_r)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
FORWARD_REQ = {"accept", "content-type", "x-request-id", "last-event-id", "user-agent"}
FORWARD_RESP = {"content-type", "retry-after", "cache-control", "x-request-id"}
STATE_COOKIE = "aiscan_oauth_state"


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def _pushed_at_epoch(v) -> float | None:
    """repository.pushed_at: GitHub sends epoch seconds on push; accept numeric strings and ISO 8601 too."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = v.strip()
        if v.isdigit():
            return float(v)
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _aware(dt).timestamp()
    return None


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or get_settings()
    SessionLocal = make_sessionmaker(s.database_url)
    app = FastAPI(title="AI Scan Repos — gateway", version="0.2.0", openapi_url=None, docs_url=None, redoc_url=None)
    app.state.SessionLocal = SessionLocal
    allowed, admins = s.logins(s.allowed_github_logins), s.logins(s.admin_github_logins)
    auth_rl, api_rl = RateLimiter(s.rate_limit_auth_per_minute), RateLimiter(s.rate_limit_api_per_minute)

    def http(timeout=20.0):
        return httpx.AsyncClient(timeout=timeout, transport=transport)

    def cookie(resp: Response, name: str, value: str, max_age: int, httponly=True):
        resp.set_cookie(name, value, max_age=max_age, httponly=httponly, secure=s.cookie_secure,
                        samesite="lax", path="/")

    # ---------- middleware: rate limit + CSRF (registered before errors.install => runs inside request-id) ----------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        p = request.url.path
        if p.startswith("/auth/") or p.startswith("/webhooks/"):
            key = f"ip:{request.client.host if request.client else '-'}"
            wait = auth_rl.hit(key)
        elif p.startswith("/api/"):
            # per user only for a valid session; anything else (no/forged/expired cookie) is keyed by client IP
            # (uvicorn --proxy-headers + FORWARDED_ALLOW_IPS => request.client is the real client)
            uid = _session_user_id(request.cookies.get(s.session_cookie))
            wait = api_rl.hit(f"u:{uid}" if uid else f"ip:{request.client.host if request.client else '-'}")
        else:
            wait = None
        if wait:
            return error_response(request, 429, "RATE_LIMITED", "too many requests", headers={"Retry-After": str(wait)})
        if request.method in UNSAFE and (p.startswith("/api/") or p == "/auth/logout"):
            c, h = request.cookies.get(s.csrf_cookie), request.headers.get("x-csrf-token")
            if not c or not h or not hmac.compare_digest(c.encode(), h.encode()):
                return error_response(request, 403, "FORBIDDEN", "CSRF token missing or invalid")
        return await call_next(request)

    errors.install(app)

    purge_state = {"next": 0.0}

    def purge_webhook_rows() -> int:
        """Delete delivery-id and payload-hash rows older than the replay window. Returns rows deleted."""
        window = s.webhook_replay_window_seconds
        if window <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
        with SessionLocal() as db:
            n = db.query(WebhookDelivery).filter(WebhookDelivery.received_at < cutoff).delete(synchronize_session=False)
            db.commit()
        return n

    def _maybe_purge():
        """Periodic purge, piggybacked on webhook traffic: at most once per min(window, 300 s) per replica."""
        now = time.monotonic()
        if now < purge_state["next"]:
            return
        purge_state["next"] = now + max(1, min(s.webhook_replay_window_seconds, 300))
        try:
            purge_webhook_rows()
        except Exception:  # noqa: BLE001 - never fail a delivery because of housekeeping
            logging.getLogger("gateway").warning("webhook dedupe purge failed", exc_info=True)

    app.state.purge_webhook_rows = purge_webhook_rows

    @app.on_event("startup")
    async def _purge_loop():
        import asyncio

        async def loop():
            while True:
                await asyncio.sleep(max(1, min(s.webhook_replay_window_seconds, 3600)))
                try:
                    await asyncio.to_thread(purge_webhook_rows)
                except Exception:  # noqa: BLE001
                    logging.getLogger("gateway").warning("webhook dedupe purge failed", exc_info=True)
        if s.webhook_replay_window_seconds > 0:
            app.state.purge_task = asyncio.create_task(loop())

    def _session_user_id(tok: str | None) -> str | None:
        if not tok or len(tok) > 200:
            return None
        try:
            with SessionLocal() as db:
                sess = db.get(Session, hash_token(tok))
                if sess and not sess.revoked_at and _aware(sess.expires_at) >= datetime.now(timezone.utc):
                    return sess.user_id
        except Exception:  # noqa: BLE001 - DB trouble: fall back to the IP key, the route will report it
            return None
        return None

    def current_user(request: Request) -> User:
        tok = request.cookies.get(s.session_cookie)
        if not tok:
            raise ApiError(401, "UNAUTHORIZED", "not logged in")
        with SessionLocal() as db:
            sess = db.get(Session, hash_token(tok))
            if not sess or sess.revoked_at or _aware(sess.expires_at) < datetime.now(timezone.utc):
                raise ApiError(401, "UNAUTHORIZED", "session expired")
            u = db.get(User, sess.user_id)
            if not u or (allowed and u.login.lower() not in allowed):
                raise ApiError(403, "FORBIDDEN", "not allowed")
            return u

    @app.middleware("http")
    async def _timed(request, call_next):
        t0, status = time.perf_counter(), 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            route = request.scope.get("route")
            REQUESTS.labels(request.method, getattr(route, "path", "unmatched"), str(status)).observe(
                time.perf_counter() - t0)

    # ---------- probes ----------
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            raise ApiError(503, "INTERNAL", "database not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---------- OAuth ----------
    @app.get("/auth/github/login")
    def login():
        if not s.github_client_id:
            raise ApiError(503, "INTERNAL", "GitHub OAuth not configured")
        state = new_token()
        q = urlencode({"client_id": s.github_client_id, "redirect_uri": f"{s.public_url}/auth/github/callback",
                       "scope": "read:user repo", "state": state, "allow_signup": "false"})
        resp = RedirectResponse(f"{s.github_oauth_base}/login/oauth/authorize?{q}", status_code=302)
        cookie(resp, STATE_COOKIE, state, 600)
        return resp

    @app.get("/auth/github/callback")
    async def callback(request: Request, code: str = Query(..., min_length=1), state: str = Query(..., min_length=1)):
        expected = request.cookies.get(STATE_COOKIE)
        if not expected or not hmac.compare_digest(state.encode(), expected.encode()):
            LOGINS.labels("bad_state").inc()
            raise ApiError(400, "VALIDATION_ERROR", "invalid OAuth state")
        try:
            async with http() as c:
                tr = await c.post(f"{s.github_oauth_base}/login/oauth/access_token",
                                  headers={"Accept": "application/json"},
                                  data={"client_id": s.github_client_id, "client_secret": s.github_client_secret,
                                        "code": code, "redirect_uri": f"{s.public_url}/auth/github/callback"})
                gh_token = tr.json().get("access_token") if tr.status_code == 200 else None
                if not gh_token:
                    LOGINS.labels("token_error").inc()
                    raise ApiError(502, "INTERNAL", "GitHub token exchange failed")
                ur = await c.get(f"{s.github_api_base}/user", headers={"Authorization": f"Bearer {gh_token}",
                                                                        "Accept": "application/vnd.github+json"})
                ur.raise_for_status()
                gh = ur.json()
        except (httpx.HTTPError, ValueError):
            LOGINS.labels("github_error").inc()
            raise ApiError(502, "INTERNAL", "GitHub error")
        login_name = gh["login"]
        if allowed and login_name.lower() not in allowed:
            LOGINS.labels("denied").inc()
            raise ApiError(403, "FORBIDDEN", "GitHub user not in allow-list")
        tok = new_token()
        with SessionLocal() as db:
            u = db.scalars(select(User).where(User.github_id == gh["id"])).first()
            if not u:
                first = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
                u = User(github_id=gh["id"], login=login_name, role="admin" if first else "user")
                db.add(u)
            u.login, u.avatar_url, u.email = login_name, gh.get("avatar_url"), gh.get("email")
            if login_name.lower() in admins:
                u.role = "admin"
            db.flush()
            db.add(Session(id=hash_token(tok), user_id=u.id, user_agent=(request.headers.get("user-agent") or "")[:300],
                           ip=request.client.host if request.client else None,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours)))
            db.commit()
        # The GitHub token is not persisted (users.gh_token_enc stays NULL in Phase 1).
        LOGINS.labels("ok").inc()
        resp = RedirectResponse(f"{s.public_url}/", status_code=302)
        ttl = s.session_ttl_hours * 3600
        cookie(resp, s.session_cookie, tok, ttl)
        cookie(resp, s.csrf_cookie, new_token(), ttl, httponly=False)  # JS reads it, echoes in X-CSRF-Token
        resp.delete_cookie(STATE_COOKIE, path="/")
        return resp

    @app.get("/me")
    def me(request: Request, u: User = Depends(current_user)):
        resp = JSONResponse({"id": u.id, "github_login": u.login, "avatar_url": u.avatar_url, "role": u.role})
        if not request.cookies.get(s.csrf_cookie):
            cookie(resp, s.csrf_cookie, new_token(), s.session_ttl_hours * 3600, httponly=False)
        return resp

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request, u: User = Depends(current_user)):
        with SessionLocal() as db:
            sess = db.get(Session, hash_token(request.cookies[s.session_cookie]))
            if sess:
                sess.revoked_at = datetime.now(timezone.utc)
                db.commit()
        resp = Response(status_code=204)
        resp.delete_cookie(s.session_cookie, path="/")
        resp.delete_cookie(s.csrf_cookie, path="/")
        return resp

    # ---------- explicit proxy to review-api ----------
    async def forward(request: Request, u: User, stream: bool) -> Response:
        path = request.url.path[len("/api"):]
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQ}
        headers.update({"X-Internal-Token": s.internal_api_token, "X-User-Id": u.id, "X-User-Role": u.role})
        c = http(None if stream else 30.0)
        t0 = time.perf_counter()
        try:
            req = c.build_request(request.method, f"{s.review_api_url}{path}", params=request.query_params,
                                  content=await request.body(), headers=headers)
            r = await c.send(req, stream=True)
        except httpx.HTTPError:
            await c.aclose()
            PROXIED.labels("502").inc()
            UPSTREAM.labels("502").observe(time.perf_counter() - t0)
            raise ApiError(502, "INTERNAL", "review-api unavailable")
        PROXIED.labels(str(r.status_code)).inc()
        UPSTREAM.labels(str(r.status_code)).observe(time.perf_counter() - t0)
        out = {k: v for k, v in r.headers.items() if k.lower() in FORWARD_RESP}
        if "location" in r.headers:
            out["Location"] = "/api" + r.headers["location"]
        if stream and r.headers.get("content-type", "").startswith("text/event-stream"):
            async def relay():
                try:
                    async for chunk in r.aiter_raw():
                        yield chunk
                finally:
                    await r.aclose()
                    await c.aclose()
            out["X-Accel-Buffering"] = "no"
            return StreamingResponse(relay(), status_code=r.status_code, headers=out)
        body = await r.aread()
        await r.aclose()
        await c.aclose()
        return Response(body, status_code=r.status_code, headers=out)

    def make_proxy(stream: bool):
        async def proxy(request: Request, u: User = Depends(current_user)):
            return await forward(request, u, stream)
        return proxy

    for path, methods in ROUTES.items():
        app.add_api_route(path, make_proxy(path in STREAMING), methods=list(methods), include_in_schema=False,
                          name=f"proxy {path}")

    # ---------- GitHub webhooks ----------
    @app.post("/webhooks/github", status_code=202)
    async def webhook(request: Request):
        body = await request.body()
        if not verify_github_signature(s.github_webhook_secret, body, request.headers.get("x-hub-signature-256")):
            WEBHOOKS.labels("bad_signature").inc()
            raise ApiError(401, "INVALID_SIGNATURE", "invalid X-Hub-Signature-256")
        event, delivery = request.headers.get("x-github-event"), request.headers.get("x-github-delivery")
        if not event or not delivery:
            raise ApiError(400, "VALIDATION_ERROR", "X-GitHub-Event and X-GitHub-Delivery are required")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            raise ApiError(400, "VALIDATION_ERROR", "payload must be a JSON object")
        window = s.webhook_replay_window_seconds
        if event == "push" and "pushed_at" in (payload.get("repository") or {}):
            pushed = _pushed_at_epoch(payload["repository"]["pushed_at"])
            if pushed is None:
                raise ApiError(400, "VALIDATION_ERROR", "repository.pushed_at must be epoch seconds or ISO 8601")
            if window > 0 and time.time() - pushed > window:
                WEBHOOKS.labels("stale").inc()
                raise ApiError(409, "CONFLICT", "stale delivery (outside the replay window)", {"delivery_id": delivery})
        _maybe_purge()
        # replay dedupe on sha256(event + body): rows "sha256:<hex>" in webhook_deliveries (no schema change, shared by
        # replicas). Checked up front, stored only after the event was processed successfully, so a failed delivery
        # can be redelivered.
        hkey = "sha256:" + hashlib.sha256(event.encode() + b"\n" + body).hexdigest()
        with SessionLocal() as db:
            if window > 0:
                old = db.get(WebhookDelivery, hkey)
                if old and (datetime.now(timezone.utc) - _aware(old.received_at)).total_seconds() <= window:
                    WEBHOOKS.labels("replay").inc()
                    raise ApiError(409, "CONFLICT", "duplicate payload (replay)", {"delivery_id": delivery})
                if old:
                    db.delete(old)
                    db.commit()
            try:
                db.add(WebhookDelivery(delivery_id=delivery[:200], event=event[:100]))
                db.commit()
            except IntegrityError:
                db.rollback()
                WEBHOOKS.labels("duplicate").inc()
                raise ApiError(409, "CONFLICT", "duplicate delivery", {"delivery_id": delivery})
        status = await _handle_event(event, payload)
        failed = status == "review_api_unavailable" or status.startswith("review_rejected_5")
        with SessionLocal() as db:
            d = db.get(WebhookDelivery, delivery[:200])
            if failed:  # forget the delivery id too: GitHub "Redeliver" (same id) may retry
                db.delete(d)
            else:
                d.status = status
                if window > 0:
                    db.add(WebhookDelivery(delivery_id=hkey, event=event[:100], status="payload_hash"))
            try:
                db.commit()
            except IntegrityError:  # a concurrent identical delivery stored the hash first
                db.rollback()
        WEBHOOKS.labels(status).inc()
        if failed:
            raise ApiError(502, "INTERNAL", "review-api unavailable, delivery not recorded (redeliver to retry)")
        WEBHOOKS.labels(status).inc()
        return {"accepted": True}

    async def _handle_event(event: str, payload: dict) -> str:
        if event != "push" or not s.webhook_auto_review:
            return "accepted" if event == "push" else "ignored" if event != "ping" else "ping"
        sender = (payload.get("sender") or {}).get("id")
        repo_url = (payload.get("repository") or {}).get("html_url")
        sha = payload.get("after")
        with SessionLocal() as db:
            u = db.scalars(select(User).where(User.github_id == sender)).first() if sender else None
        if not u or not repo_url or (allowed and u.login.lower() not in allowed):
            return "unknown_sender"
        review = {"repo_url": repo_url, "depth": s.webhook_review_depth}
        if isinstance(sha, str) and len(sha) == 40:
            review["ref"] = sha
        try:
            async with http() as c:
                r = await c.post(f"{s.review_api_url}/reviews", json=review,
                                 headers={"X-Internal-Token": s.internal_api_token, "X-User-Id": u.id,
                                          "X-User-Role": u.role})
        except httpx.HTTPError:
            return "review_api_unavailable"
        return "review_created" if r.status_code == 201 else f"review_rejected_{r.status_code}"

    return app


def get_app():  # uvicorn --factory app.main:get_app
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper())
    bad = s.problems()
    if bad:  # fail fast: the pod crash-loops with a clear message instead of running with dev defaults
        for m in bad:
            logging.getLogger("gateway").critical("config: %s", m)
        raise SystemExit("invalid production config: " + "; ".join(bad))
    return create_app(s)

