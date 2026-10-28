# Runbook — AI Scan Repos

## 1. Deploy

### Local (dev)
See README Quickstart. Defaults: SQLite `./review-api.db`, `JOB_LAUNCHER=local`, reports in `./reports`.
Set `LOCAL_WORKER_CWD` to `scanner-worker/` so the subprocess finds the `scanner` package.

### Docker images
```bash
docker build -t <registry>/ai-scan-repos/review-api:<tag> review-api
docker build -t <registry>/ai-scan-repos/scanner-worker:<tag> scanner-worker
```

### Kubernetes — TODO (no manifests/Helm chart in repo yet)
What the code expects to exist (derive manifests from this, defaults from `review-api/app/config.py`):
- Namespace `ai-scan-repos`.
- Postgres + `DATABASE_URL` (e.g. `postgresql+psycopg://...`); review-api runs `alembic upgrade head` on start.
- PVC `ai-scan-reports` mounted at `REPORTS_DIR` in review-api **and** in every Job at `/reports`
  → needs `ReadWriteMany` (or co-located pods with RWO). TODO: confirm storage class.
- ServiceAccount for review-api with RBAC `create` on `batch/jobs` in the namespace; env `JOB_LAUNCHER=kubernetes`.
- ServiceAccount `scanner-worker` (token not mounted).
- Secret `scanner-worker-env` with `INTERNAL_API_TOKEN`, `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_API_KEY`.
- Secret for review-api: `INTERNAL_API_TOKEN` (same value), `AI_KEYS_ENCRYPTION_KEY`, `DATABASE_URL`.
- Service `review-api:8000` (ClusterIP, not exposed) — matches `REVIEW_API_INTERNAL_URL`.
- `SCANNER_IMAGE` pointing at the pushed worker image.
- Probes: liveness `/healthz`, readiness `/readyz`.
- Egress for Jobs: github.com, AI endpoint, and trivy/semgrep rule/DB downloads.

CI/CD (Jenkins) and GitOps (Argo CD) for this project: **TODO** — existing ones cover only the shop
app (`devsecops-shop-devops/jenkins/Jenkinsfile`, `devsecops-shop-devops/argocd/application-shop.yaml`).

## 2. Operate

| Task | How |
|---|---|
| List reviews | `GET /reviews` (headers `X-Internal-Token`, `X-User-Id`) |
| Progress | `GET /reviews/{id}` → `status`, `progress`, `attempts`, `message`, `job_name`; or `<reports>/<id>/progress.json` |
| Pause / resume | `POST /reviews/{id}/pause` / `/resume` |
| Read report | `GET /reviews/{id}/report` or `/report.md` |
| Store an AI key | `PUT /ai-keys {"provider","api_key"}` (encrypted; see gap below) |
| Metrics | `GET /metrics` |
| Jobs (k8s) | `kubectl -n ai-scan-repos get jobs -l app=scanner-worker`; logs: `kubectl logs job/<job_name>` |
| Rotate encryption key | TODO — no re-encryption tool; changing `AI_KEYS_ENCRYPTION_KEY` makes stored keys unreadable |

Tunables: `HEARTBEAT_TIMEOUT_SECONDS` (900), `MAX_ATTEMPTS` (5), `WATCHDOG_INTERVAL_SECONDS` (60),
`SCANNER_TIMEOUT_SECONDS` (900 per tool), `CLONE_TIMEOUT_SECONDS` (300).

Finished Jobs are deleted by K8s after 1 h (`ttlSecondsAfterFinished`). Report files on the PVC are not
cleaned up — TODO retention policy.

## 3. Troubleshoot

| Symptom | Likely cause / fix |
|---|---|
| `401/403` on any endpoint | Missing/wrong `X-Internal-Token`, or missing `X-User-Id` (see `app/auth.py`) |
| `422` on POST /reviews | URL not `https://github.com/<owner>/<repo>` |
| `/readyz` 503 | DB unreachable / bad `DATABASE_URL` |
| Startup crash in container | `alembic upgrade head` failed — check DB connectivity |
| Review stuck `running`, attempts climbing | Worker crashing or no heartbeat; check job logs; watchdog relaunches every 15 min, fails after 5 attempts |
| Review `failed` "no progress for 900s after N attempts" | Max attempts reached; fix cause then create a new review |
| Job never appears (k8s) | review-api RBAC to create Jobs, `SCANNER_IMAGE` pull errors, Secret `scanner-worker-env` missing |
| Local mode: nothing happens | `local worker command not found` warning → set `LOCAL_WORKER_CWD`/`LOCAL_WORKER_CMD`, activate venv |
| Scanner shows `skipped` | Tool not on PATH (normal locally for gitleaks/trivy; image ships all three) |
| Scanner shows `error ... timed out` | Raise `SCANNER_TIMEOUT_SECONDS` |
| Report "AI step skipped or unavailable" | `AI_PROVIDER=none`, invalid provider, missing `AI_BASE_URL` for `openai_compatible`, or API error (see `ai.error` in report.json) |
| `/report` 404 "report not ready" | Review not `completed` or file missing on the reports volume (PVC not shared?) |
| Pause has no effect | API and worker don't share the same reports volume |
| PUT /ai-keys 500 | `AI_KEYS_ENCRYPTION_KEY` empty/invalid Fernet key |
