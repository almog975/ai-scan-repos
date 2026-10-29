# review-api/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../README.md) and [ADR-0001](../docs/adr/0001-repo-layout.md).

## Purpose
Internal REST API: reviews CRUD, pause/resume, reports, AI keys; watchdog (900 s heartbeat, 5 attempts); launches one scanner Job per attempt. See [ADR-0002](../docs/adr/0002-k8s-job-per-review.md).

## Contents
`app/` (main, models, schemas, auth, launcher, watchdog, crypto, metrics), `alembic/`, `tests/`, `Dockerfile`, `Dockerfile.local`, `Makefile`.

## Build/Run
`uvicorn --factory app.main:get_app --port 8001`; `make test`; details in [top README](../README.md#quickstart-local-no-kubernetes).

## CI/CD
TODO: Jenkins stage with path filter `review-api/**` (Test → Build → Scan → Push); image tag bump in `deploy/review-api/overlays/<env>`; Argo CD syncs.

## Config
See [architecture.md › Configuration](../docs/diagrams/architecture.md#configuration).

## Owners
AI Scan Coder; Infra (Job/RBAC) consulted.

## Idempotent review creation

`POST /reviews` accepts an optional `Idempotency-Key` header (1-200 chars, stored in the unique nullable
`reviews.idempotency_key`, Alembic 0005). A repeated key from the same user returns the existing review with 200 and
launches nothing; the same key from another user is 409. The gateway sends the webhook's event+body hash, so a
delivery retried after a crash or the processing deadline never creates a second review.
