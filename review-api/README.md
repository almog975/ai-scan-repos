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
