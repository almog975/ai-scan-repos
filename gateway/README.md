# gateway/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../README.md) and [ADR-0001](../docs/adr/0001-repo-layout.md).

## Purpose
Public API edge: GitHub OAuth login, sessions + CSRF cookies, webhook HMAC verification, proxy to review-api with `X-Internal-Token` + `X-User-Id`.

## Contents
`app/{main,config,db,security}.py`, `tests/`, `Dockerfile`, `Makefile`.

## Build/Run
`pip install -r requirements.txt -r requirements-dev.txt && make test`; run via `docker compose up`.

## CI/CD
TODO: Jenkins stage with path filter `gateway/**` (Test → Build → Scan → Push); image tag bump in `deploy/gateway/overlays/<env>`; Argo CD syncs.

## Config
Env: `DATABASE_URL`, `REVIEW_API_URL` (default `http://review-api:8001`), `INTERNAL_API_TOKEN`, `GITHUB_CLIENT_ID/SECRET`, `PUBLIC_URL`, `ALLOWED_GITHUB_LOGINS`, `ADMIN_GITHUB_LOGINS`, `GITHUB_WEBHOOK_SECRET`, `WEBHOOK_AUTO_REVIEW`, `WEBHOOK_REVIEW_DEPTH`, `SESSION_TTL_HOURS`, `COOKIE_SECURE`, `RATE_LIMIT_AUTH_PER_MINUTE`, `RATE_LIMIT_API_PER_MINUTE`, `WEBHOOK_REPLAY_WINDOW_SECONDS` (default 86400).

## Owners
AI Scan Coder; Tester (HMAC negative tests).

## Webhook replay protection

GitHub signs only the request body (HMAC `X-Hub-Signature-256`); `X-GitHub-Delivery` and the headers are unsigned and there
is no signed timestamp header. So:

- **Delivery id**: a repeated `X-GitHub-Delivery` → 409 CONFLICT.
- **Payload hash**: the sha256 of every accepted body is stored (row `sha256:<hex>` in `webhook_deliveries`, shared by all
  replicas, no schema change) for `WEBHOOK_REPLAY_WINDOW_SECONDS`. The same body under a new delivery id → 409, so a
  captured delivery can't retrigger a review. Push bodies are unique per push (`after`, `pushed_at`), so genuine
  pushes are never dropped.
- **Timestamp window**: `push` payloads carry `repository.pushed_at` *inside the signed body*; one older than the window
  → 409. Together with the hash TTL this closes replays after the hash expires. Other events (ping, etc.) have no
  usable signed timestamp and rely on the hash dedupe alone, which is enough because they never trigger a review.
  Note: GitHub's manual "Redeliver" resends the same body and is therefore also rejected (409) within the window.

## Rate limits

`/auth/*` + `/webhooks/*`: per client IP. `/api/*`: per user **only for a valid session**; requests with no, forged,
revoked or expired cookies are keyed by client IP (`uvicorn --proxy-headers` + `FORWARDED_ALLOW_IPS`), so random
cookies can't bypass the limiter.
