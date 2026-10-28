# contracts/ (v0.1.0-rc)

Source of truth for APIs and data shapes in AI Scan Repos. Change only via PR; code follows these.

| File | What |
|---|---|
| `gateway.openapi.yaml` | Public edge (OpenAPI 3.1): GitHub OAuth, `/me`, webhooks, explicitly proxied `/api/*` routes (no catch-all). Session cookie auth. |
| `review-api.openapi.yaml` | Internal API (OpenAPI 3.1): reviews lifecycle, progress SSE (`progress`/`state`/`done`), files, attempts, retry, reports, repo check, providers, admin settings. `X-Internal-Token` (gateway only) + `X-User-Id`. |
| `common.schema.json` | Shared `$defs`: uuid, commit_sha, repo_url, state, depth, provider_kind, role, stage, severity, file_status, attempt_outcome, attempt, budget_no. |
| `finding.schema.json` | Normalized finding. `id = sha1(tool\nrule\nfile\nline)` hex; `line` nullable. |
| `report.schema.json` | `report.json` on the reports volume. |
| `checkpoint.schema.json` | `checkpoint.json` (resume cursor). |
| `progress.schema.json` | `progress.json` per-file live progress; also SSE `progress` payload. |

Conventions: ids are UUIDs; `commit_sha` = 40 hex; `attempt` 1–5 within a budget, `budget_no` ≥1 and +1 per
`POST /reviews/{id}/retry` (only from `failed`); errors are `{error:{code,message}, request_id}` with `code` ∈ INVALID_SIGNATURE, CLOUD_ACK_REQUIRED, UNAUTHORIZED, FORBIDDEN, NOT_FOUND, VALIDATION_ERROR, RATE_LIMITED, CONFLICT, INTERNAL; list endpoints use
`page`/`page_size` (max 100) and return `total`; `/reviews/{id}/attempts` is not paginated. `POST /reviews` with a
cloud provider requires `ack_cloud: true`, else 422 `CLOUD_ACK_REQUIRED`. User routes → 401 when unauthenticated;
`/admin/*` → 403 for non-admins. `users.role ∈ {user, admin}`; the first user to log in becomes admin.

**Deferred past v0.1:** per-depth time/cost estimates; server-side paging of findings inside reports.

Validate: `make contracts` (from `ai-scan-repos/`): openapi-spec-validator + JSON Schema 2020-12 meta-validation.
