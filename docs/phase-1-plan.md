# Phase 1 – Backend implementation plan

Base: `feat/phase-0-foundation` @ `7b51cf4` (contracts v0.1.0-rc, not tagged).
`contracts/` is the source of truth. A step is finished only when its listed contract tests and unit tests pass, `make test` is green, and every kustomize overlay builds.
Out of scope: UI designs, tags, and per-depth estimates. Findings are not paged server-side; Brief = top 10 findings stays.

## DB migration direction (decision; not implemented yet)
- **Alembic** in `review-api/alembic/` is the only thing that runs migrations. Its runner is a k8s Job or init container; it must not be part of the app CMD.
- `infra/db/schema.sql` is the reference DDL, and a CI test checks that it matches the migrations. Run `alembic upgrade head` against an empty Postgres, then `pg_dump --schema-only`, then diff the dump against `schema.sql` with normalisation.
- Planned revisions:
  - `0002_contract_v1`: enums `review_state` (queued, running, paused, stuck, retrying, completed, failed, cancelled — exactly `common.schema.json#/$defs/state`; "done" = `completed`) and `user_role`; `reviews.budget_no`, `ref`, `commit_sha CHECK ^[a-f0-9]{40}$`, `provider_id`, `ack_cloud`, `depth`; table `review_attempts(review_id, budget_no, n 1..5, job_name, outcome, started_at, finished_at, error)` with UNIQUE(review_id, budget_no, n); `ai_providers(key_ciphertext, key_nonce, key_version)`; `settings` key/value jsonb; `sessions`; `webhook_deliveries`; a first-user-admin trigger.
  - `0003_*`: indexes and backfills only. Every revision must have a working downgrade.
- Test: `review-api/tests/test_migrations.py` runs upgrade, then downgrade, then upgrade (testcontainers Postgres; skipped when docker is absent).

## Steps (each one is one PR into `feat/phase-1-backend`)

### 1. Error envelope + auth/roles
- Files: `review-api/app/errors.py` (exception → `{error:{code,message,details},request_id}`, codes from the Error.code enum), a request-id middleware, and `app/auth.py` (session lookup, `require_user` → 401 UNAUTHORIZED, `require_admin` → 403 FORBIDDEN). Gateway: `gateway/app/errors.py`, same envelope.
- DB: migration 0002, covering `users.role` and the first-user-admin trigger.
- Acceptance: the `test_openapi_specs` Error/401/403 checks pass. New `tests/test_errors.py` checks that every error response validates against the Error schema. `tests/test_auth.py` checks that the first user becomes admin, the second becomes user, and `/admin/*` returns 403 for a user.

### 2. Review model + state machine
- Files: `app/models.py` (Review, ReviewAttempt), `app/state.py` (allowed transitions queued→running→{paused,completed,failed,stuck,cancelled}, paused→running, stuck→retrying→running, failed→retrying via retry; attempt 1..5 per budget, budget_no+1 on retry).
- Acceptance: a table-driven `tests/test_state.py` that rejects illegal transitions with 409 CONFLICT. The state enum is identical to `common.schema.json#/$defs/state` (asserted against the contract file).

### 3. POST/GET /reviews
- Body is `ReviewCreate{repo_url, ref, depth, provider_id, ack_cloud}` with closed schemas. A cloud provider without `ack_cloud=true` gets 422 CLOUD_ACK_REQUIRED. `ref` is resolved to a 40-char SHA. The list endpoint uses `page`/`page_size` (≤100) and returns `total`.
- Files: `app/routers/reviews.py`, `app/schemas.py`, generated from or checked against the OpenAPI.
- Acceptance: schemathesis `st run contracts/review-api.openapi.yaml` passes for /reviews against the test app. This enables the 4 or 5 tests that are skipped today, using an in-process ASGI base URL. `test_agreed_decisions` CLOUD_ACK cases pass.

### 4. AI providers + crypto
- `app/crypto.py`: AES-256-GCM with a 96-bit random nonce and AAD = provider id. The key is `AI_KEYS_ENCRYPTION_KEY` from Secret `ai-keys-master`; startup fails, and `/readyz` returns 503, if it is missing or not 32 bytes. `key_version` allows rotation later.
- Routes: `/admin/ai-providers` CRUD (AIProviderWrite/Patch), `/admin/ai-providers/{id}/test`, and `GET /ai-providers` (id, name, kind, cloud flag; never any key material).
- Adapters: `app/providers/base.py` (`Provider` protocol: `complete()`, `test()`, `is_cloud`) and 9 stubs in `app/providers/{openai,anthropic,gemini,azure_openai,bedrock,mistral,groq,ollama,openai_compatible}.py`, plus a registry mapping each `provider_kind` to its adapter.
- Acceptance:
  - The provider_kind enum equals the registry keys (test reads `common.schema.json`).
  - Round-trip encrypt/decrypt works, and a tampered ciphertext fails.
  - No response body contains `key`, `api_key` or `secret` fields (schema test plus a response scan).

### 5. /repos/check + /admin/settings
- `/repos/check` echoes `ref` and returns `size_mb`, the default branch and the resolved SHA. It goes through the GitHub API client `app/github.py`, which is mocked in tests. `/admin/settings` GET/PUT is a closed object stored in `settings`.
- Acceptance: the contract tests for `size_mb`/ref echo pass, and PUT with an unknown key returns 422 VALIDATION_ERROR.

### 6. Scanner Job launcher
- `app/launcher.py`: load the template from the ConfigMap `scanner-job-template` (mounted file `SCANNER_JOB_TEMPLATE`). Set:
  - name `scan-<id>-a<attempt>`;
  - labels `app=scanner`, `review-id`, `budget-no`, `attempt`;
  - `activeDeadlineSeconds: 21600` and `backoffLimit: 0`;
  - env `DEPTH`, `REVIEW_ID`, `ATTEMPT`, `BUDGET_NO` and `COMMIT_SHA`;
  - the `reports` PVC mounted at `/reports`;
  - a pinned image (`:latest` is rejected).
- RBAC lives in `deploy/review-api/base` (Role: jobs create/get/delete/list, pods/log get).
- Acceptance: the existing `test_watchdog_crypto_launcher` tests are extended. The rendered Job validates with kubeconform, and the name and labels are asserted.

### 7. Checkpoint, reports layout, scanner-worker
- Volume layout: `/reports/<review_id>/attempts/<budget_no>-<n>/{checkpoint.json, findings.partial.jsonl, logs/}`, plus `/reports/<review_id>/report.json|report.md` (final).
- `checkpoint/v1` keys (contract is closed): `schema, review_id, commit_sha, depth, attempt, budget_no, stage, cursor{scanners_done, files_total, files_done, findings_offset}, progress, updated_at`. Partial findings always live in the sibling `findings.partial.jsonl`; only the first `cursor.findings_offset` lines are trusted. The worker resumes from the newest earlier attempt (any budget) only when `commit_sha` and `depth` match.
- Files: `scanner-worker/scanner/{checkpoint.py,report.py,run.py}`. Finding id = sha1 recipe from `finding.schema.json`. `report.json` includes `attempt` and `budget_no`, and Brief = top 10 by severity.
- Acceptance: worker output validates against `checkpoint`, `report` and `finding` schemas (reuse `contracts/tests` registry). Kill-and-resume test: same findings and no duplicate ids.

### 8. Watchdog + /retry
- `app/watchdog.py` marks running reviews as `stuck` when the heartbeat is stale or the Job has failed. It deletes the old Job (propagationPolicy Background) and launches attempt n+1, up to 5. After 5 the review is `failed`. `POST /reviews/{id}/retry` is allowed only from `failed`; it returns 202 and moves the review to `retrying` with budget_no+1 and attempt 1. Each attempt writes a `review_attempts` row.
- `GET /reviews/{id}/attempts` is unpaginated, with optional `?budget_no=`.
- Acceptance: a watchdog test with a fake k8s client checks the delete and relaunch, and the cap of 5. A retry from a non-failed state returns 409 CONFLICT. The attempts response validates against the spec.

### 9. Progress, SSE, pause/resume/cancel, files
- `GET /reviews/{id}/progress` returns a progress.schema object. The SSE stream sends `event: progress|state|done`, where `done` is terminal and closes the stream.
- `pause`/`resume` return 202. Pause = checkpoint and delete the Job; resume = launch from the checkpoint. `cancel` moves the review to `cancelled`.
- `/reviews/{id}/files` uses `page`/`page_size` and returns `total`.
- Acceptance:
  - Every SSE payload validates against `progress.schema.json`.
  - pause/resume returns 202 with a Review body.
  - The files pagination contract tests pass.

### 10. Gateway
- Only the routes listed in `gateway.openapi.yaml` are proxied, and anything else returns 404 NOT_FOUND. The browser session cookie is `HttpOnly; Secure; SameSite=Lax`. Gateway→review-api calls use `X-Internal-Token` (Secret), and review-api rejects calls that lack it. GitHub webhooks use HMAC, failing with 401 INVALID_SIGNATURE. Rate limiting returns 429 RATE_LIMITED.
- Acceptance: a test checks that the gateway route table equals the spec paths, and an HMAC test runs. schemathesis on gateway with review-api mocked.

### 11. Health, readiness, metrics hardening
- `/healthz` (liveness, no dependencies), `/readyz` (DB, plus the crypto key for review-api, plus the k8s API for the launcher), and `/metrics` (`reviews_total{state}`, `scan_attempts_total{outcome}`, `job_launch_errors_total` and request histograms) on gateway :8000, review-api :8001 and scanner-worker :8002.
- Acceptance: probe tests per service. The probe paths in the overlays match, which is checked by a kustomize build grep test in CI.

### 12. Migration ↔ schema.sql parity + CI wiring
- Add the parity test, schemathesis stages and migrations Job to the Jenkinsfile.
- Acceptance: CI is green, and `make test` plus `make contracts` pass.

## Requirement → step map
| Contract requirement | Step |
|---|---|
| Error schema / codes, 401/403, first-user admin | 1 |
| states queued..done | 2 |
| POST /reviews ref, provider_id, ack_cloud → 422 CLOUD_ACK_REQUIRED; list paging | 3 |
| /admin/ai-providers AES-GCM (ai-keys-master), GET /ai-providers no keys, 9 adapters | 4 |
| /repos/check, /admin/settings | 5 |
| Job scan-<id>-a<n>, 21600s, DEPTH, reports PVC, template ConfigMap | 6 |
| checkpoint/v1 + commit SHA + findings.partial.jsonl, attempts/ layout, Brief top 10 | 7 |
| review_attempts, budget_no, 5/budget, watchdog deletes Job, POST /retry 202 | 8 |
| progress.schema + SSE progress/state/done, pause/resume 202, /files paged | 9 |
| gateway explicit routes, session cookie, internal token, HMAC | 10 |
| /healthz /readyz /metrics | 11 |
| migrations (Alembic) parity with infra/db/schema.sql | 0002 in 1, parity in 12 |
