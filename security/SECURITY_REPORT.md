# Security verification r4 — PR #13 (head 08590fc, base main 7eb75db)
Tester: Grok-Bot-AI-Scan-Tester · 2026-10-29 · local and fake targets only (fake review-api, local SQLite, local postgres 127.0.0.1:55432)

## Verdict: PASS (1 new open LOW, 1 INFO)

## 1. Diff review
- gateway/app/main.py: the hash is now claimed as a `pending` row (PK insert) **before** `_handle_event`. On IntegrityError the delivery gets 409 replay. On success the row becomes `payload_hash`. On failure or exception both the claim and the delivery id are released.
- Metric: one `WEBHOOKS.inc()` per path. The success path no longer double counts.
- test_phase9_r3: `race_identical_single_process` went from strict xfail to a plain assert. **Stricter, not weaker.** No other case was removed or relaxed, and the harness is byte-identical to r3.
- The future pushed_at is documented as accepted (it is GitHub-signed). This is accepted and was a LOW in r3.

## 2. Race (r4_harness)
| Target | N=2 ×20 | N=10 ×20 | N=50 ×20 |
|---|---|---|---|
| SQLite, 1 gateway | PASS | PASS | PASS |
| Postgres, 2 gateway replicas (round-robin) | PASS | PASS | PASS |

Every round of the 120 had exactly 1 review created, 1×202 and N−1×409.

## 3. Failure path
- With review-api 503 the response is 502, and the claim and delivery id are deleted (0 rows left).
- After recovery: redeliver with the same id returns 202, then a repeat returns 409, then a new id returns 409. Exactly 1 review is created and 0 pending rows remain.
- **NEW LOW: a crash mid-processing leaves the claim behind.** After SIGKILL of the gateway during `_handle_event`, both the `pending` claim and the `accepted` delivery-id row stay in the DB. After a restart, redeliver with the same id returns 409 and a new id with the same body returns 409. The claim is only freed by the replay-window expiry/purge (default 86400 s). That was verified with a 3 s window, where the pending row was purged.
  - By then the stale-pushed_at check rejects the redelivery anyway, so that push is never auto-reviewed. Manual review still works.
  - Suggestion: give pending claims a short TTL (e.g. reclaim if pending older than 5 min).

## 4. Metric
One successful delivery adds exactly `review_created` +1 (delta = {review_created: 1}).

## 5. Gitleaks 8.21.2
- `gitleaks dir .` from the root: rc 0.
- `gitleaks dir /abs/path`: rc 0, both from the root and from /tmp (.gitleaks.toml is auto-loaded from the target).
- Git history: 0 findings.
- Planted AKIA… AWS key: detected (aws-access-token), by both the relative and the absolute scan.
- Planted generic `sk-OTHER…` key: detected.
- **INFO: the allowlist is not exact.** It is the literal `sk-SECRETKEY123` with no regex metacharacters, but it is unanchored with regexTarget=match. So it is a substring match: a planted `api_key = "sk-SECRETKEY123zzzz…"` was suppressed. Suggest anchoring to the exact canary (e.g. `'''"sk-SECRETKEY123"'''`) or using `stopwords`.

## 6. Regression
| Suite | Result |
|---|---|
| contracts | 125 passed |
| gateway | 37 passed (+2 new) |
| review-api | 183 passed, 2 xfailed |
| scanner-worker | 135 passed |
| phase3 recovery | 12 passed, 2 xfailed |
| worker recovery | 7 passed |
| vitest | 23 passed |
| tsc | rc 0 |
| ci/tests | 235 passed, ×3 envs |
| pg parity | 32 passed |
| schemathesis | 1385 passed |
| kubeconform | valid |
| e2e mock | 32 passed |
| e2e full stack | 34 passed |

- **Pentest** (AISCAN_ROOT=pr13): 64 passed in 7 of 9 runs. 2 runs had a single flaky failure of `rate_limit_api_random_cookie_429`, a timing-dependent harness case in code this PR doesn't touch. It is not a product regression.
- **r4 + r3 wrappers:** 31 passed, 1 xfailed (the crash LOW, strict).
- **Trivy:** 0 vulnerable HIGH/CRITICAL in any component. The only fs findings are the standing false positives AWS-0104 and KSV-0109.
- **semgrep:** the baseline items, plus SHA1 in a test harness (non-security use).
