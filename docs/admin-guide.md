# AI Scan Repos – Admin Guide

For admins and operators. It summarises each area and links to the detailed runbook. End users: [user-guide.md](user-guide.md).

## 1. Admin role

- The first user to log in becomes `admin`. Others get `user`, except GitHub logins listed in `ADMIN_GITHUB_LOGINS`.
- `ALLOWED_GITHUB_LOGINS` (optional) limits who may log in at all.
- Admins see **AI providers** in the sidebar. `/api/admin/*` returns 403 for non-admins.

## 2. AI providers

Full reference, including adapters, the API, Tailscale on-prem setup and the status table: **[runbooks/admin-ai-providers.md](runbooks/admin-ai-providers.md)**.

UI: **AI providers** (`/admin/ai-providers`) lets you add, edit and delete providers, run **Test connection** (*"Connected: <model> answered in N ms"*, or an error), and enable or disable them. API: `/api/admin/ai-providers` (CRUD, plus `/{id}/test`). Defaults are set with `PUT /api/admin/settings`: `default_provider_id`, `max_concurrent_reviews`, `max_repo_size_mb`.

**Cloud vs on-prem.** The provider row's `kind` decides it (`onprem` | `cloud`), never the adapter or the URL.
- For `kind: cloud`, saving requires the admin acknowledgement (*"I acknowledge that repository code will be sent to this cloud provider"*), which is recorded as `cloud_ack_by`/`cloud_ack_at`. Switching a provider to cloud clears any earlier acknowledgement.
- Users must also send `ack_cloud: true` when they start or resume a review with a cloud provider. Without it the API returns 422 `CLOUD_ACK_REQUIRED`.
- Reports made with a cloud provider show a cloud banner.
- Prefer on-prem for private code.

**Grok (x.ai).** There is no dedicated adapter. It runs as `openai_compatible`:

| Field | Value |
|---|---|
| name | `Grok` |
| kind | **`cloud`** (never `onprem`, because that would hide the warning) |
| base_url | `https://api.x.ai/v1` |
| model | e.g. a current Grok model id |
| api_key | your x.ai key (stored encrypted) |

```bash
curl -s -b "$COOKIES" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -X POST https://<host>/api/admin/ai-providers \
  -d '{"name":"Grok","kind":"cloud","base_url":"https://api.x.ai/v1","model":"<grok-model>","api_key":"<key>"}'
```

**Keys.** Provider keys are encrypted at rest with AES-256-GCM, using `AI_KEYS_ENCRYPTION_KEY` from the `ai-keys-master` secret. The UI shows only the last 4 characters. Rotating the master key requires re-encrypting all stored keys, and there is no tool for that yet ([ADR-0003](adr/0003-ai-key-rotation.md), deferred).

## 3. Secrets

Names and keys only (never commit values). Full table: **[secrets-runbook.md](secrets-runbook.md)**. Created by the installer's `secrets` step ([installer.md](installer.md#secrets)).

| Secret (ns `ai-scan-repos`) | Keys | Used by |
|---|---|---|
| `gateway-secrets` | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_WEBHOOK_SECRET` | gateway |
| `db-secrets` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL` | postgres, review-api, gateway (`DATABASE_URL`) |
| `ai-keys-master` | `AI_KEYS_ENCRYPTION_KEY` (32 bytes, `openssl rand -hex 32`) | review-api |
| `scanner-secrets` | `INTERNAL_API_TOKEN` (≥ 32 chars), `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_API_KEY` | scanner Jobs; review-api and gateway read only `INTERNAL_API_TOKEN` |

- With `APP_ENV=production`, review-api and gateway refuse to start while a dev default or a missing key remains. `kubectl -n ai-scan-repos logs deploy/<name>` shows the `config:` lines ([k3s.md](k3s.md)).
- Rotation: re-create the secret, then `kubectl -n ai-scan-repos rollout restart deploy/<svc>`. The installer never replaces `ai-keys-master`, not even with `--rotate-secrets`.

## 4. Deploy and Argo CD

- **Install** (CLI only; the former web wizard was removed): `infra/installer/aiscan-install.sh --config infra/installer/installer.env install` (try `--dry-run` first) runs preflight → bootstrap → registry → secrets → images → deploy → verify. See [installer.md](installer.md) and [k3s.md](k3s.md).
- **GitOps:** Argo CD app-of-apps `ai-scan-root`, with one Application `ai-scan-<component>-k3s` per component in ns `ai-scan-repos`, auto-sync with prune and selfHeal. See [ci-cd-flow.md](ci-cd-flow.md) and [ADR-0004](adr/0004-gitops.md).
- **CI:** Jenkins test → build (kaniko) → Trivy → push → tag bump. See [ci.md](ci.md) and [ci-cd.md](ci-cd.md).
- **Deploy PRs:** each main build opens a PR `ci(k3s): <svcs> -> <sha> [skip ci]` from `deploy/<svc>-<sha>`; merge it to deploy (Argo CD syncs). PAT setup: [deploy/jenkins/README.md](../deploy/jenkins/README.md#github-bot-pat-deploy-pr-flow).

```bash
argocd app list | grep ai-scan
argocd app get ai-scan-review-api-k3s
kubectl -n ai-scan-repos get pods,jobs
```

## 5. Monitoring and stuck reviews

- Metrics, alerts and the Grafana dashboard "AI Scan Repos" (uid `ai-scan-repos`) are described in **[monitoring.md](monitoring.md)**.
- Alerts that point to stuck work are `AIScanReviewsStuck` (stuck > 0 for 15 min), `AIScanNoWorkerHeartbeat` and `AIScanReconcilerNotRunning`.
- The reconciler marks a review `stuck` after 2 consecutive passes over the heartbeat threshold. It then retries, up to 5 attempts per budget, and finally sets `failed`. A queued or retrying review whose Job is missing for more than 15 min gets its Job recreated.
- Manual steps (inspect the checkpoint, delete the Job, resume, retry, cancel): **[runbooks/stuck-review.md](runbooks/stuck-review.md)**. Paused for days: [runbooks/resume-after-days.md](runbooks/resume-after-days.md). State diagram: [diagrams/review-state-machine.md](diagrams/review-state-machine.md).

- **Webhook redelivery:** the gateway answers 409 to GitHub **Redeliver** within 24 h of the original delivery. The stored delivery id is purged after 24 h (a failed delivery, answered 502, is not recorded and can be redelivered), but a push older than 24 h is then rejected as stale, so in practice redelivering a push still fails. Start the review by hand or push a new commit instead: [runbook.md](runbooks/runbook.md#github-webhooks-redeliver-returns-409).

## 6. Rollback

Revert the tag-bump commit in a PR (no force-push, no direct push to `main`), let Argo CD resync, then verify: **[runbooks/rollback.md](runbooks/rollback.md)**.

## 7. Reports: retention, backups and cleanup

- Reports live on the shared `reports` PVC (`/reports/<review-id>/…`). K3s uses the static local PV `ai-scan-reports-pv`; EKS uses EFS. See [storage-plan.md](storage-plan.md) and [reports-pvc-recovery.md](reports-pvc-recovery.md).
- **Cleanup:** CronJob `reports-cleanup` (platform component) runs daily at 03:00 Asia/Jerusalem. It deletes report directories older than `REPORT_RETENTION_DAYS` (ConfigMap `reports-retention`, default **30**). After that the API answers **410 REPORT_EXPIRED** and the UI shows "Report expired".

  ```bash
  kubectl -n ai-scan-repos get cronjob reports-cleanup
  kubectl -n ai-scan-repos create job --from=cronjob/reports-cleanup reports-cleanup-manual   # run now
  ```
- **Backups:** nothing in the repo backs up the reports or the database automatically.
  - K3s: copy `/srv/ai-scan/reports` off the node every night (for example with `restic` or `rsync`), and back up the Postgres volume or take a `pg_dump`. See [reports-pvc-recovery.md](reports-pvc-recovery.md).
  - EKS: AWS Backup for EFS, with `Retain` on the PV.
- `uninstall` keeps Secrets, PVCs and namespaces. Only `--purge` deletes data ([installer.md](installer.md)).
