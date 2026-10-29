# Runbook — AI Scan Repos

## 1. Deploy

### Local (dev)
See README Quickstart. Defaults: SQLite `./review-api.db`, `JOB_LAUNCHER=local`, reports in `./reports`.
Set `LOCAL_WORKER_CWD` to `scanner-worker/` so the subprocess finds the `scanner` package.

### Docker images
Images are always `<host>/ai-scan/<svc>:<sha>` (git SHA, never `latest`); registry hosts per target are in
[deploy/README.md](../../deploy/README.md).
```bash
SHA=$(git rev-parse --short HEAD)
docker build -t <host>/ai-scan/review-api:$SHA review-api
docker build -t <host>/ai-scan/scanner-worker:$SHA scanner-worker
docker build -t <host>/ai-scan/gateway:$SHA gateway
docker build -t <host>/ai-scan/frontend:$SHA frontend
docker push <host>/ai-scan/<svc>:$SHA
```

### K3s / EKS (supported path)
Use the CLI installer `infra/installer/aiscan-install.sh` (no web UI; the old web wizard was removed in Phase 9):
it bootstraps the cluster, creates secrets, installs Argo CD and the app-of-apps. See [installer.md](../installer.md).
The manual per-component steps below are for debugging only.

### Kubernetes (Kustomize, per component)
Manifests live in `deploy/<component>/{base,overlays/{local,k3s,eks}}`; deploy each with
`kubectl apply -k deploy/<component>/overlays/$OVERLAY`. Apply order (from [infra-README.md](../infra-README.md)):

1. **platform** – `kubectl apply -k deploy/platform/overlays/$OVERLAY` (on a fresh cluster the ServiceMonitor
   CRD is missing: re-apply after step 3 or ignore that error).
2. **secrets** – create them from the CLI per [secrets-runbook.md](../secrets-runbook.md) (never in git):
   `gateway-secrets`, `db-secrets`, `ai-keys-master`, `scanner-secrets` (ns `ai-scan-repos`),
   `grafana-admin` (ns `monitoring`), `registry-htpasswd`, `registry-tls` (ns `registry`).
3. **kube-prometheus-stack** – `infra/helm/kube-prometheus-stack/install.sh`.
4. **registry** – `kubectl apply -f infra/registry/registry.yaml`; on K3s nodes install
   `infra/registry/registries.yaml.example` as the registries config.
5. **postgres** – `kubectl apply -k deploy/postgres/overlays/$OVERLAY`.
6. **scanner-worker** – `kubectl apply -k deploy/scanner-worker/overlays/$OVERLAY` (RBAC + Job template).
7. **review-api, gateway, frontend** – `kubectl apply -k deploy/<svc>/overlays/$OVERLAY`.

**Image tags:** overlays ship with `GIT_SHA_PLACEHOLDER`. For a manual deploy set a real tag first:
```bash
cd deploy/review-api/overlays/$OVERLAY
kustomize edit set image ai-scan/review-api=<host>/ai-scan/review-api:$SHA
# scanner-worker: the image is passed to review-api via ConfigMap scanner-image
cd ../../../scanner-worker/overlays/$OVERLAY
kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=<host>/ai-scan/scanner-worker:$SHA
```
Don't commit these manual edits on `main` — in normal operation Jenkins sets the tags.

What the code expects at runtime (defaults in `review-api/app/config.py`; check against the manifests):
- Namespace `ai-scan-repos`; review-api `JOB_LAUNCHER=kubernetes` with RBAC to create `batch/jobs` there.
- Postgres reachable via `DATABASE_URL` (from `db-secrets`); migrations run in Job `review-api-migrate` (Argo CD PreSync hook) before review-api rolls.
- Reports PVC mounted at `REPORTS_DIR` in review-api **and** at `/reports` in every scanner Job → must be
  shared (`ReadWriteMany`, or co-located pods with RWO); see [storage-plan.md](../storage-plan.md).
- ServiceAccount `scanner-worker` (token not mounted); scanner Jobs get env via `envFrom` Secret
  `scanner-secrets` (`SCANNER_SECRET_NAME`, set in the review-api deployment).
- `INTERNAL_API_TOKEN` must be identical in review-api, gateway and scanner Jobs.
- Service `review-api:8001` (ClusterIP, not exposed) — matches `REVIEW_API_INTERNAL_URL`.
- `SCANNER_IMAGE` from ConfigMap `scanner-image`.
- Probes: liveness `/healthz`, readiness `/readyz`.
- Egress for Jobs: github.com, the AI endpoint, trivy/semgrep rule/DB downloads.

### CI/CD and GitOps
Jenkins (smee relay → test → build → Trivy → push → tag bump) and the Argo CD Applications that watch
`deploy/<component>/overlays/<target>` are described in [docs/ci.md](../ci.md) and
[deploy/README.md](../../deploy/README.md).

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

Finished Jobs are deleted by K8s after 1 h (`ttlSecondsAfterFinished`). Report directories older than
`REPORT_RETENTION_DAYS` (ConfigMap `reports-retention`, default 30) are deleted by CronJob `reports-cleanup`
(daily 03:00 Asia/Jerusalem); the API then returns 410 `REPORT_EXPIRED`. See [admin-guide.md](../admin-guide.md#7-reports-retention-backups-and-cleanup).

## 3. Troubleshoot

| Symptom | Likely cause / fix |
|---|---|
| `401/403` on any endpoint | Missing/wrong `X-Internal-Token`, or missing `X-User-Id` (see `app/auth.py`) |
| `422` on POST /reviews | URL not `https://github.com/<owner>/<repo>` |
| `/readyz` 503 | DB unreachable / bad `DATABASE_URL` |
| Sync stuck at PreSync | Job `review-api-migrate` failed — `kubectl -n ai-scan-repos logs job/review-api-migrate`; check DB connectivity |
| Review stuck `running`, attempts climbing | Worker crashing or no heartbeat; check job logs; watchdog relaunches every 15 min, fails after 5 attempts |
| Review `failed` "no progress for 900s after N attempts" | Max attempts reached; fix cause then create a new review |
| Job never appears (k8s) | review-api RBAC to create Jobs, `SCANNER_IMAGE` pull errors, Secret `scanner-secrets` missing |
| Local mode: nothing happens | `local worker command not found` warning → set `LOCAL_WORKER_CWD`/`LOCAL_WORKER_CMD`, activate venv |
| Scanner shows `skipped` | Tool not on PATH (normal locally for gitleaks/trivy; image ships all three) |
| Scanner shows `error ... timed out` | Raise `SCANNER_TIMEOUT_SECONDS` |
| Report "AI step skipped or unavailable" | `AI_PROVIDER=none`, invalid provider, missing `AI_BASE_URL` for `openai_compatible`, or API error (see `ai.error` in report.json) |
| `/report` 404 "report not ready" | Review not `completed` or file missing on the reports volume (PVC not shared?) |
| Pause has no effect | API and worker don't share the same reports volume |
| POST /admin/ai-providers 500, /readyz 503 | `AI_KEYS_ENCRYPTION_KEY` empty or not 32 bytes (hex/base64) |

### GitHub webhooks: "Redeliver" returns 409

Two different webhooks exist. Don't confuse them:

| Webhook | Receiver | Dedupe |
|---|---|---|
| CI (push → Jenkins build) | smee-client → Jenkins `/github-webhook/` | none in our code. Jenkins also polls (`pollSCM H/5`) |
| Auto-review (push → review, only with `WEBHOOK_AUTO_REVIEW=true`) | gateway `POST /webhooks/github` | replay protection (below) |

The gateway (`gateway/app/main.py`, `WEBHOOK_REPLAY_WINDOW_SECONDS`, default 86400 = 24 h) checks each signed delivery like this. Every rejection is **409 CONFLICT**.

1. **Same `X-GitHub-Delivery` already stored** → `"duplicate delivery"`. GitHub's **Redeliver** keeps the original delivery GUID. Delivery-id rows are never expired, so this 409 is **permanent, not 24 h**. It applies whenever the first attempt reached the gateway with a valid signature, even when the review itself could not be created: the response is still 202, with the status `review_api_unavailable`, `unknown_sender` or `review_rejected_<code>`.
2. **Same body under a new delivery id within the window** → `"duplicate payload (replay)"`. This check is a sha256 of the body, kept for 24 h.
3. **`push` whose `repository.pushed_at` is older than the window** → `"stale delivery (outside the replay window)"`. Even an unseen delivery of a push older than 24 h is refused.

Redeliver therefore only works if the original never got past signature or header checks (for example the gateway was down, or returned 401/400/5xx before storing anything) **and** the push is less than 24 h old. Check the gateway metric `ai_scan_gateway_webhooks_total{result="duplicate"|"replay"|"stale"}` or the GitHub delivery's response body to see which case hit.

**What to do instead of Redeliver:**
- **Start the review by hand:** in the UI use **New review** with the repo URL, and put the pushed commit SHA in *Branch, tag or commit*. Or call `POST /api/reviews` with `ref` = that SHA.
- **Or push a new commit** (an empty commit works: `git commit --allow-empty -m "rescan"`). It produces a new delivery id, body and `pushed_at`.
- **CI didn't build:** wait for `pollSCM` (≤ 5 min) or use **Build Now** in Jenkins. Redelivering to smee/Jenkins is not deduped by our code.
- Don't lower `WEBHOOK_REPLAY_WINDOW_SECONDS` or delete `webhook_deliveries` rows just to redeliver, because that reopens replay attacks.
