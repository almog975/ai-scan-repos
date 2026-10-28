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
Env: `DATABASE_URL`, `REVIEW_API_URL` (default `http://review-api:8001`), `INTERNAL_API_TOKEN`, `GITHUB_CLIENT_ID/SECRET`, `PUBLIC_URL`, `ALLOWED_GITHUB_LOGINS`, `ADMIN_GITHUB_LOGINS`, `GITHUB_WEBHOOK_SECRET`, `WEBHOOK_AUTO_REVIEW`, `WEBHOOK_REVIEW_DEPTH`, `SESSION_TTL_HOURS`, `COOKIE_SECURE`.

## Owners
AI Scan Coder; Tester (HMAC negative tests).
