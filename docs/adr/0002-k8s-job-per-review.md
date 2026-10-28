# ADR-0002: One Kubernetes Job per review, with checkpoint recovery

- **Status:** Accepted for the prototype (implemented in `review-api` + `scanner-worker`); target design in plan Phases 2–3
- **Date:** 2026-10-28
- **Deciders:** Planner (A), Coder, Infra (R)
- **Related:** Source: AI Scan Planner plan canvas (store-3f3d29fa…/09f1217b…), read 2026-10-28. Sections "חוזה worker ↔ volume", "State machine". Code: `review-api/app/launcher.py`,
  `review-api/app/watchdog.py`, `review-api/app/main.py`, `scanner-worker/scanner/run.py`, `scanner-worker/scanner/checkpoint.py`.

## Contracts
`contracts/` is the source of truth ([ADR-0001](0001-repo-layout.md)): the checkpoint, report, finding and
review-state shapes in `contracts/*.schema.json` / `review-api.openapi.yaml` are the target, and the code
must be changed to match them. Exception adopted by the plan: **Brief depth = top 10 findings to the AI**.

## Context
A review clones untrusted code and runs Semgrep, Gitleaks, Trivy and LLM calls; it can take minutes to
hours (deep mode, slow on-prem AI) and may be paused for days. It must be isolated, resource-bounded,
must not leave source code behind, and must survive pod/node/API crashes without duplicating findings.

## Decision
**Every review attempt runs as its own `batch/v1` Job** created by review-api (the only component with
RBAC to create Jobs in namespace `ai-scan-repos`). Progress is durable on a shared reports PVC; retries
are owned by review-api, not Kubernetes.

As implemented today:
- **Job spec** (`build_job_manifest`): name `scan-<id12>-a<attempt>-<rand5>`, `backoffLimit: 0`,
  `ttlSecondsAfterFinished: 3600`, `restartPolicy: Never`, SA `scanner-worker` with
  `automountServiceAccountToken: false`, UID/GID 10001, `runAsNonRoot`, seccomp RuntimeDefault,
  `readOnlyRootFilesystem`, drop ALL caps, no privilege escalation; requests 250m/512Mi, limits 2/2Gi.
  Volumes: `/work` emptyDir 2Gi, `/tmp` emptyDir 1Gi, `/reports` PVC `ai-scan-reports`.
  Secrets only via `envFrom` Secret `scanner-worker-env` — never in args.
- **Checkpoint**: `<reports>/<review_id>/checkpoint.json`, written atomically (tmp file → fsync →
  `os.replace`) after each scanner and, in `deep` mode, after each AI-reviewed file (`done_files`).
  `progress.json` alongside. On start the worker loads the checkpoint and skips completed scanners/files.
- **Heartbeat**: each step POSTs `/internal/reviews/{id}/progress` (X-Internal-Token) → `last_heartbeat`.
- **Watchdog** (in review-api, every `WATCHDOG_INTERVAL_SECONDS`=60): a `running` review with no
  heartbeat for `HEARTBEAT_TIMEOUT_SECONDS`=**900 s** is relaunched with `attempts+1`; once
  `attempts >= MAX_ATTEMPTS`=**5** it is marked `failed` with message
  "no progress for 900s after N attempts".
- **Pause/resume**: `POST /reviews/{id}/pause` writes a `PAUSE` file in the review's reports dir; the worker
  checks it on every heartbeat, saves the checkpoint and exits. `resume` deletes `PAUSE`, increments
  attempts and launches a new Job, which resumes from the checkpoint.
- **Code hygiene**: clone dir under `/work` is removed in `finally`; Job TTL deletes the pod.
- Dev mode: `JOB_LAUNCHER=local` runs the same worker as a subprocess.

## Consequences
- + Strong isolation per review; crashes cost at most one step (one scanner or one file).
- + Pause for days costs no compute; API can restart without losing state (state in DB + PVC).
- − Requires a PVC shared by review-api and all Jobs (ReadWriteMany, or single node on K3s) — Infra.
- − Job start latency (image pull, scheduling) per attempt.
- − Re-running a scanner after a crash inside it repeats that scanner (checkpoint granularity).
- Gaps vs. contracts — code must follow the contracts (TODO): no `stuck`/`retrying`/`cancelled` states or cancel endpoint; no Job deletion on
  retry; no `activeDeadlineSeconds`; no `review_attempts` table; checkpoint lacks `commit_sha`
  (resume re-clones HEAD, which may differ); no `findings.partial.jsonl`; reports path is
  `<reports>/<id>/` not `/reports/reviews/<id>/`; Job spec built in code (`build_job_manifest`), not read from `deploy/base/scanner-job-template.yaml`;
  no startup reconciliation of Jobs vs. DB.

## Alternatives
| Option | Why not chosen |
|---|---|
| Long-lived worker Deployment + queue (Celery/RQ) | Shared process across untrusted repos; harder per-review limits/cleanup; extra broker |
| K8s Job retries (`backoffLimit>0`) | K8s doesn't know about heartbeats/pause; double retry logic |
| Scan inside review-api | No isolation; API needs scanner tools + big resources |
| Argo Workflows / Tekton | Extra controller to operate for a single-step job |
