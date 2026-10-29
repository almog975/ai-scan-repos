# Known limitations / backlog

## Local mode (`JOB_LAUNCHER=local`) only

These are tracked as `xfail(strict=True)` in `review-api/tests/test_phase3_recovery.py`; production (Kubernetes) is not affected.

1. **Duplicate attempt after a review-api restart.** `LocalLauncher` keeps worker processes in memory, so after
   review-api restarts a still-running worker reads as `missing`; the reconciler marks the review `stuck` and
   relaunches attempt n+1. The stale worker gets 409 (superseded) on its next heartbeat and stops, and the result is
   still clean and deduplicated (`test_restart_then_sweep_converges_without_duplicates`), but one attempt is used up.
   *Kubernetes:* `status()` asks the API server about the Job `scan-<id>-b<budget>-a<attempt>`, so a restart has no effect.
2. **Stale clone dirs in a shared `WORK_DIR`.** A SIGKILLed worker cannot run its cleanup, so its `src-<review>`
   clone dir can stay in a shared local `WORK_DIR` after the relaunch.
   *Kubernetes:* `/work` is a per-pod `emptyDir` and goes away with the pod.

## Recovery semantics (Phase 3)

- A pause has no expiry: a paused review stays paused until it is resumed or cancelled, and the reconciler never touches it.
- A review is marked `stuck` only after 2 consecutive reconciler passes over `HEARTBEAT_TIMEOUT_SECONDS`, and `stuck` stays visible for at least one cycle (`WATCHDOG_INTERVAL_SECONDS`, default 60) before the relaunch.
- A queued or retrying review whose Job has been missing for more than 15 min (`QUEUED_JOB_TIMEOUT_SECONDS`) gets its Job created again for the same attempt, and no attempt is counted.

## Infra / security (Phase 9)

- Postgres backups live on the same single K3s node as the database; no off-node copy (docs/postgres-backup-restore.md).
- Jenkins egress allows 443 to any public IP, and monitoring/argocd use PSA warn/audit only (docs/security-accepted-risks.md).
- In-cluster registry pushes must use `registry.registry:5000`; the nip.io name only works from outside pods (docs/registry-auth-runbook.md).
