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
- The reconciler keeps `stuck` visible for at least one cycle (`WATCHDOG_INTERVAL_SECONDS`, default 60) before it relaunches.
