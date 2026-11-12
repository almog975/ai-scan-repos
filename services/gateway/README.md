# services/gateway/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../../README.md).

## Purpose
Public API edge: GitHub OAuth login, sessions + CSRF cookies, webhook HMAC verification, proxy to review-api with `X-Internal-Token` + `X-User-Id`.

## Contents
`app/{main,config,db,security}.py`, `tests/`, `Dockerfile`, `Makefile`.

## Build/Run
`pip install -r requirements.txt -r requirements-dev.txt && make test`; run via `docker compose up`.

## Config
Env: `DATABASE_URL`, `REVIEW_API_URL` (default `http://review-api:8001`), `INTERNAL_API_TOKEN`, `GITHUB_CLIENT_ID/SECRET`, `PUBLIC_URL` (OAuth callback = `PUBLIC_URL/auth/github/callback`), `GITHUB_CALLBACK_URL` (override), `GITHUB_OAUTH_SCOPES` (default `read:user`), `ALLOWED_GITHUB_LOGINS`, `ADMIN_GITHUB_LOGINS`, `GITHUB_WEBHOOK_SECRET`, `WEBHOOK_AUTO_REVIEW`, `WEBHOOK_REVIEW_DEPTH`, `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_API_PER_MINUTE`, `WEBHOOK_REPLAY_WINDOW_SECONDS` (default 86400), `WEBHOOK_PENDING_RECLAIM_SECONDS` (default 300, >= 3x `REVIEW_API_TIMEOUT_SECONDS`), `REVIEW_API_TIMEOUT_SECONDS` (default 20).

## Webhook replay protection

GitHub only signs the body, so the gateway keeps a short record of what it already processed.

- A repeated `X-GitHub-Delivery` id returns 409.
- The same event and body under a new delivery id within `WEBHOOK_REPLAY_WINDOW_SECONDS` returns 409.
- `push` events older than the window (by `repository.pushed_at`, inside the signed body) return 409.
- If review-api fails, the delivery returns 502 and nothing is kept, so GitHub's Redeliver works.
- Review requests carry an `Idempotency-Key`, so a retried delivery never creates a second review.

## Rate limits

`/auth/*` and `/webhooks/*` are limited per client IP, and `/api/*` per user with a valid session (otherwise per IP).

The limiter is in memory, so with N replicas the real limit is N times the setting.
