# Security verification r5 — PR #14 (head 503296d, base main 44c4270)
Tester: Grok-Bot-AI-Scan-Tester · 2026-10-29 · local/fake targets only · AISCAN_ROOT=/workspace/aiscan-pr14

## Verdict: PASS (1 open LOW, test-only flakes identified)

## 1. Diff review
- **Gateway: `_claim()`.**
  - It inserts a pending row for both the payload hash and the delivery id.
  - On conflict it runs one conditional `UPDATE ... WHERE status='pending' AND received_at < now-reclaim` and checks rowcount == 1, so takeover is atomic.
  - Rows that are done are never reclaimed.
- **Config.** New setting `WEBHOOK_PENDING_RECLAIM_SECONDS=300`.
- **Purge.** It now also deletes stale pending rows.
- **xfail removal.** In test_phase9_r4, `crash_retry_not_blocked` went from a strict xfail to an assert, which is stricter. The harness change only sets reclaim=1 s for that case, and no case was dropped.
- **Pentest harness.** The rate-limit loop now sends 2×300+1 requests, which fixes the fixed-window boundary flake from r4.
- **New gateway unit tests.** 2 added (39 total).

## 2. Stale-claim reclaim (r5_harness)
| Case | Result |
|---|---|
| kill -9 mid-processing: hash and id rows left pending | PASS |
| Redeliver inside the reclaim window (3 s): same id and new id | 409 / 409, PASS |
| After the window: same id 202, then 409; new id 409 | PASS |
| Atomic reclaim race, SQLite: 2/10/50 concurrent ×20 rounds against a planted stale claim, half same-id and half new-id | 60/60 rounds: exactly 1 review, one 202, rest 409 |
| Atomic reclaim race, Postgres with 2 replicas: 2/10/50 ×20 | 60/60 rounds: exactly 1 review |
| Default 300 s reclaim: in-flight redeliver | 409, PASS |
| Purge clears stale pending (age above reclaim, below window) and keeps fresh pending | PASS |
| **Long but alive processing (review-api 3 s, reclaim 1 s)** | **FAIL → LOW**: a same-id redeliver gets 202 and review-api receives 2 requests. Same on PG across replicas. |

**Findings**
- **LOW: no heartbeat or fencing.** Processing that takes longer than the reclaim window gets double-processed. The default 300 s is far above the gateway's 20 s httpx timeout, so this is safe as shipped. But `problems()` doesn't reject a misconfigured small value, and the httpx timeout is per phase, not total. Suggestions:
  - Validate reclaim ≥ e.g. 3× the review-api timeout.
  - Use a total-deadline timeout.
  - Optionally fence completion on received_at.
- **INFO: at-least-once delivery.** If the gateway is killed after review-api accepted the request but before the row is marked done, a reclaim creates a second review. The test saw 2 reviews for that push. This is inherent to the design; the fix is idempotency on the review-api side (e.g. keyed by delivery hash).
- **INFO: orphan delivery-id row.** When a new-id delivery wins the reclaim, the crashed run's pending delivery-id row stays until the purge removes it. This is harmless.

## 3. Pentest flake hunt (76 cases)
- **Idle:** 20 runs with `-rA` and junit. 18 runs fully passed (76/76).
  - Run 4 failed `r2::random_cookies_429` and `r2::no_cookie_same_ip_429`.
  - Run 15 failed `r2::valid_user_own_bucket_429`.
- **Under CPU load:** 8 busy loops on 8 cores, load average about 10. I stopped this phase at the time box after 4 complete runs, all 76/76.
  - A 5th run was aborted by my own process kill (KeyError in the r3 harness). It is excluded as an artifact, not a flake.
- **Root cause, all 3: test timing, not a product bug.**
  - The gateway RateLimiter is a fixed wall-clock minute window (`now // 60`), which is the documented behaviour.
  - `r2_harness` loops 330 random-cookie requests and 305 valid-user requests against a limit of 300/min. When a minute boundary falls inside the loop, the counter resets and no 429 is reached. `no_cookie_same_ip_429` then fails as a cascade, because the IP bucket was never filled.
  - The `valid_user_own_bucket_429` check also uses `n=0` both as "never got a 429" and as "got one at request 0", which is ambiguous.
  - **Fix (test only):** use 2×limit+1 requests, as PR #14 already does in pentest_harness, and a None sentinel.
- **pentest_harness `rate_limit_api_random_cookie_429`:** the r4 flake, now 0/24 runs.

## 4. Gitleaks anchored allowlist (`^sk-SECRETKEY123$`, regexTarget=secret)
- **Clean scans:** relative (root) rc 0, absolute (from /tmp) rc 0, git history 0 findings.
- **Planted secrets:** relative and absolute scans both report the same 5 findings:
  - AKIA… key: detected
  - `sk-SECRETKEY123zzzz`: detected
  - `sk-SECRETKEY123zzzz9999abcd`: detected
  - `xsk-SECRETKEY123`: detected
  - ghp_ PAT: detected
  - Exact `sk-SECRETKEY123`: allowed, as intended
- The allowlist now matches the exact literal only.

## 5. Regression
| Suite | Result |
|---|---|
| contracts | 125 passed |
| gateway | 39 passed |
| review-api | 183 passed, 2 xfailed |
| scanner-worker | 135 passed |
| phase3 recovery | 12 passed, 2 xfailed |
| worker recovery | 7 passed |
| vitest | 23 passed |
| tsc | rc 0 |
| ci/tests | 235 passed, ×3 envs |
| pg parity | 32 passed |
| schemathesis | 1338 passed |
| kubeconform | valid |
| e2e mock | 32 passed |
| e2e full stack | 34 passed |
| r5 wrapper | 11 passed, 2 xfailed (strict) |

- **Trivy per component** (frontend, gateway, review-api, scanner-worker, ci-agent): 0 HIGH/CRITICAL.
- **Trivy fs:** only the standing false positives AWS-0104 (CRIT) and KSV-0109 (HIGH).
- **Semgrep:** 2 ERROR and 7 WARNING, all baseline items (alembic SQL, e2e urllib, EFS CMK, launcher logger) plus SHA1 used in test harnesses for non-security purposes.
