# Runbook — resuming a review after days

For a review that has sat `paused` or `failed` for days. Code: `routers/control.py` (`resume`),
`routers/reviews.py` (`retry`), `jobs.py`, `launcher.py`, `scanner-worker/scanner/checkpoint.py`, `run.py`.
See also [stuck-review](stuck-review.md) and the [state machine](../diagrams/review-state-machine.md).

| From | Command | Attempt |
|---|---|---|
| `paused` | `POST /api/reviews/{id}/resume` → `queued` | same attempt; continues its own checkpoint |
| `failed` | `POST /api/reviews/{id}/retry` → `retrying` | `budget_no+1`, attempt 1; adopts the newest earlier checkpoint |

## 1. commit_sha and repo changes
- `commit_sha` is resolved and **pinned at creation** (`POST /reviews`). Resume/retry scan the **same commit**,
  not the branch's new head. The worker fetches exactly that SHA and fails if it gets another.
- A checkpoint is adopted only if its `commit_sha` and `depth` match (`resume_from`).
- **Force-push / branch deleted:** if the commit is no longer fetchable from GitHub, clone fails → attempt
  `error` → the watchdog burns attempts until `failed`. Check first:
  `git ls-remote <repo_url> | grep <commit_sha>` or try `git fetch --depth 1 origin <sha>` locally.
  If gone, **cancel** and create a new review (new SHA).
- Want the latest code? Create a new review; there is no "rebase" of an existing one.
- Private repo access lost (token revoked / repo made private) → clone fails the same way.

## 2. PVC retention and cleanup
- Checkpoints live on the `reports` PVC: `/reports/<review_id>/attempts/<budget>-<n>/`. They survive Job
  deletion and pod restarts. The worker's `/work` clone is removed on every exit, so resume re-clones.
- Jobs: `ttlSecondsAfterFinished: 3600`; a paused review has no Job.
- **No automatic retention/cleanup** of `/reports` exists — TODO (Infra/Coder: retention policy and job). If
  someone deleted the review dir, resume still works but starts from scratch.
- Before resuming, check space: `kubectl -n ai-scan-repos exec deploy/review-api -- df -h /reports`.

## 3. AI provider changed in between
The per-review AI Secret is built **at each launch** from the current DB row (`jobs.py`):
- Key rotated with PATCH → new key used automatically.
- Key expired/revoked at the vendor → AI calls fail (worker retries 429/5xx ×3); scanners still produce a
  report, AI fields empty. Fix the key first, then `POST /api/admin/ai-providers/{id}/test`.
- **Provider deleted** → `reviews.provider_id` is set NULL (`ondelete=SET NULL`) → resumed scan runs **without AI**.
  There is no way to attach a different provider to an existing review — TODO; create a new review instead.
- Provider disabled → TODO: not checked at resume; the Secret is still built from the row.
- Master key (`ai-keys-master`) changed → "cannot decrypt AI key", no AI. See [ADR-0003](../adr/0003-ai-key-rotation.md).
- Cloud acknowledgement (`ack_cloud`) is not re-asked on resume/retry; if the provider's `kind` was changed
  to `cloud` in between, the report shows the cloud banner but nobody re-confirmed — TODO.

## 4. Migrations ran in between
- Migrations run in Job `review-api-migrate` (Argo CD PreSync) — `review-api/alembic/versions/`. Check:
  `kubectl -n ai-scan-repos logs job/review-api-migrate` and that review-api is Ready.
- Checkpoints are `checkpoint/v1`; a checkpoint with another schema or depth is ignored → full rescan (safe, slower).
- A newer worker image resumes old partial findings as-is; their `id` recipe or severity mapping may differ
  from new findings (e.g. pre-Phase-2 semgrep INFO=`info`). If the scanner version changed, prefer
  cancel + new review for a clean report.

## 5. After resuming — verify
1. `GET /api/reviews/{id}` → `queued`/`retrying` then `running` within a few minutes (first heartbeat).
   If it stays `queued` > 15 min: the watchdog does not sweep `queued` — see [stuck-review §3.3](stuck-review.md).
2. `GET /api/reviews/{id}/attempts` → new attempt row, no immediate `error`.
3. `kubectl -n ai-scan-repos logs -l app=scanner,review-id=<id>` → log line `review <id> b-n depth=… resumed=True stage=…` (resumed=False = started from scratch).
4. Progress starts near the checkpoint's stage, not 0 (otherwise the checkpoint was not adopted).
5. On `completed`: `report.json` `repo.commit_sha` = the pinned SHA; `provider` is what you expect (or `none`);
   cloud banner present if `kind=cloud`.
