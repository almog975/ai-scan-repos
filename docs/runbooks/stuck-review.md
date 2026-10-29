# Runbook — stuck review

Based on `review-api/app/watchdog.py`, `state.py`, `routers/control.py`, `routers/reviews.py`, `launcher.py`,
`config.py` and `scanner-worker/scanner/checkpoint.py`. States and transitions:
[review state machine](../diagrams/review-state-machine.md). Namespace: `ai-scan-repos`.

```bash
NS=ai-scan-repos; R=<review-id>; API=https://<host>/api   # gateway; use your browser cookies + X-CSRF-Token for POSTs
```

## 1. Spot it
| Signal | Where | Looks stuck when |
|---|---|---|
| State | `GET $API/reviews/$R` | `running`/`retrying` and progress not moving; or `queued` for > 15 min |
| Attempts | `GET $API/reviews/$R/attempts` | many rows with outcome `stuck`/`error`; attempt near 5 |
| Progress | `GET $API/reviews/$R/progress` | same stage/percent for > 15 min |
| Metrics | review-api `/metrics` (Prometheus) | `scan_attempts_total{outcome="stuck"}` / `{outcome="error"}` rising; `job_launch_errors_total` > 0; `reviews_total{state="failed"}` |
| Logs | `kubectl -n $NS logs deploy/review-api` | `review <id> attempt b-n stuck: <reason>`, `relaunch of … failed`, `cannot decrypt AI key`, `could not delete job` |

```bash
kubectl -n $NS get jobs,pods -l app=scanner,review-id=$R -o wide
kubectl -n $NS describe job -l review-id=$R          # DeadlineExceeded / BackoffLimitExceeded
kubectl -n $NS logs -l app=scanner,review-id=$R --tail=200
kubectl -n $NS logs deploy/review-api | grep $R
kubectl -n $NS get events --sort-by=.lastTimestamp | grep scan-$R   # image pull, PVC, quota, scheduling
```
Job name: `scan-<review-id>-a<attempt> (attempt unique across budgets: (budget-1)*5+attempt)`; labels `app=scanner, review-id, attempt, budget-no`.

## 2. What the watchdog does by itself
Every `WATCHDOG_INTERVAL_SECONDS` (60) it checks reviews in **`running` or `retrying`**. A review is stuck when:
- no heartbeat for `HEARTBEAT_TIMEOUT_SECONDS` (900), or
- its Job is `failed` (backoffLimit 0; `activeDeadlineSeconds` 21600 = 6 h exceeded) or `missing`, or
- the worker reported `outcome=error`.

It then ends the attempt (`stuck`), deletes the Job (propagation Background), and launches attempt n+1 in
the same budget (review → `retrying` until the first heartbeat). The new attempt resumes from the newest
checkpoint of the same `commit_sha` and depth. After attempt **5** the review becomes `failed` with
`failure_reason` "5 attempts exhausted in budget N: …" and its per-review AI Secret is deleted.
**Not covered:** `queued` and `paused` reviews are never swept (see 3.5).

## 3. Manual steps
### 3.1 Inspect the checkpoint
```bash
POD=$(kubectl -n $NS get pod -l app=review-api -o name | head -1)
kubectl -n $NS exec $POD -- ls -la /reports/$R /reports/$R/attempts
kubectl -n $NS exec $POD -- cat /reports/$R/attempts/<budget>-<n>/checkpoint.json
kubectl -n $NS exec $POD -- wc -l /reports/$R/attempts/<budget>-<n>/findings.partial.jsonl
```
(The `reports` PVC is mounted at `/reports` in review-api.)
Check `stage`, `progress`, `commit_sha`, `cursor.findings_offset` (only that many partial lines are trusted).
If `report.json` exists the scan finished and only the success callback was lost.

### 3.2 Delete the Job
```bash
kubectl -n $NS delete job -l review-id=$R --cascade=background
```
Within ≤ 60 s the watchdog sees `missing` and relaunches attempt n+1 (if the review is `running`/`retrying`).

### 3.3 Force a retry / resume
| State | Action |
|---|---|
| `running`/`retrying` | delete the Job (3.2) — the watchdog relaunches |
| `paused` | `curl -X POST $API/reviews/$R/resume` (same attempt, own checkpoint) |
| `stuck` (rare, transient) | `POST $API/reviews/$R/resume` (attempt n+1; 409 if budget exhausted) |
| `failed` | `curl -X POST $API/reviews/$R/retry` (new budget, attempt 1; resumes from the last checkpoint of the same commit) |
| `queued` with no Job | the reconciler recreates the Job after 15 min; otherwise `POST …/cancel` and start a new review (pause is only allowed from `running`, `queued` returns 409) |
Resume/retry return 409 on capacity (`max_concurrent_reviews`) — wait or raise it in `PUT /api/admin/settings`.

### 3.4 Cancel
`curl -X POST $API/reviews/$R/cancel` — from any active state; deletes the Job and the AI Secret. Admins may
call it on any user's review.

### 3.5 Mark failed (no endpoint)
There is no API to force `failed`. Prefer **cancel**. If `failed` is required (e.g. to allow `retry`), as a last resort:
```bash
kubectl -n $NS delete job -l review-id=$R
kubectl -n $NS exec -it statefulset/postgres -- psql -U <db-user> <db> -c \
 "UPDATE reviews SET state='failed', failure_reason='manually failed: <ticket>', finished_at=now() WHERE id='$R' AND state IN ('queued','running','retrying','stuck','paused');"
```
(user/db from Secret `db-secrets`.) This bypasses metrics and attempt bookkeeping — note it in the incident.

## 4. Escalation
1. Same reason on several reviews (image pull, PVC full, GitHub/AI unreachable, `job_launch_errors_total`) →
   platform issue: **Infra** (cluster, PVC, egress NetworkPolicy, Tailscale).
2. Worker exceptions/stack traces in scanner logs, or checkpoint not advancing across attempts → **Coder** (scanner-worker).
3. Watchdog not acting (no "stuck" log lines while heartbeat is stale), state/API errors → **Coder** (review-api).
4. AI provider errors (401/429, `cannot decrypt AI key`) → admin: fix the provider ([admin-ai-providers](admin-ai-providers.md)).
Attach: review id, `GET …/attempts` output, Job describe, last 200 log lines, checkpoint.json.
