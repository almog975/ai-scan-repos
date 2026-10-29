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
- **Payload hash**: sha256 of `event + "\n" + body` (row `sha256:<hex>` in `webhook_deliveries`, shared by all replicas,
  no schema change). It is checked before processing and **stored only after the event was processed successfully**.
  The same event+body under a new delivery id within `WEBHOOK_REPLAY_WINDOW_SECONDS` (default 86400) → 409, so a
  captured delivery can't retrigger a review.
- **Failed deliveries** (review-api unreachable or 5xx) → 502, and neither the delivery id nor the hash is kept, so
  GitHub's "Redeliver" (same delivery id) or a resend works. A successful delivery redelivered within the window is a 409.
- **Timestamp window**: `push` payloads carry `repository.pushed_at` *inside the signed body* (epoch seconds; numeric
  strings and ISO 8601 are also parsed, anything else → 400). Older than the window → 409. Together with the hash TTL
  this closes replays after the hash expires. Other events have no usable signed timestamp and rely on the hash
  alone, which is enough because only `push` can trigger a review.
- **Purge**: rows (delivery ids and hashes) older than the window are deleted periodically: a background task every
  min(window, 1 h) and, piggybacked on webhook traffic, at most every min(window, 300 s) per replica. Delivery-id
  dedupe therefore also lasts for the window only.

## Rate limits

`/auth/*` + `/webhooks/*`: per client IP. `/api/*`: per user **only for a valid session**; requests with no, forged,
revoked or expired cookies are keyed by client IP, so random cookies can't bypass the limiter.

- **Per replica**: the limiter is in-process (fixed 1-minute window). With N gateway replicas the effective limit is up
  to N × the configured value. A shared limiter (Redis/DB) is out of scope; the base deployment runs 1 replica.
- **Client IP**: `uvicorn --proxy-headers` trusts `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS`. That setting
  takes IP/CIDR literals only, and ingress pod IPs are dynamic, so it can't name "the Traefik pods". It stays the pod
  CIDR (k3s `10.42.0.0/16`, eks VPC `10.40.0.0/16`). The `gateway-ingress` NetworkPolicy is the actual control: only
  Traefik (kube-system), the frontend nginx and the Prometheus scrape can open a connection to the gateway at all,
  so no other pod can spoof `X-Forwarded-For`.
