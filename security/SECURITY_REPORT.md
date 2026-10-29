# Phase 9 r3: PR #12 head 82db36a (base main 809d325)

**Verdict: PASS.** 0 open CRITICAL/HIGH. All the Phase 9 LOW fixes work except one: concurrent identical deliveries are processed twice, a regression from r2. It is LOW and still open.

## Diff
The xfail was removed from `test_expired_hash_rows_purged`. Its assertion is unchanged and is now required to pass, so the change is stricter. The pentest and r2 harnesses are otherwise unchanged.

## Findings

| Sev | Area | File:line | Status |
|---|---|---|---|
| LOW (regression) | Race: 2 identical deliveries at once (new ids) → both 202, **2 reviews created** | gateway/app/main.py (hash checked up front, inserted after `_handle_event`) | OPEN, Coder. Fix: insert a pending hash row before processing (unique PK) and delete it on failure. Exposure: needs a captured signed body, replayed within the processing time. xfail(strict) test. |
| LOW | `WEBHOOKS.labels(status).inc()` runs twice on success | gateway/app/main.py, end of webhook() | OPEN, Coder. Metric double-count. |
| LOW | A future-skewed `pushed_at` (+10 y) is accepted. Once its hash is purged after 24 h, it could be replayed. | gateway/app/main.py `_pushed_at_epoch` use | Note. The value is inside the GitHub-signed body, so an attacker can't forge it. Optional skew bound. |
| INFO | A numeric float string such as "1.7e9" gets 400 | same | Acceptable: GitHub sends ints. |
| FP | Trivy AWS-0104 CRIT, KSV-0109 HIGH ×4 | unchanged | Same justification as r2. |
| FP | Gitleaks tree rc 1 only when scanned by absolute path | security/pentest/pentest_harness.py:130 | rc 0 from the repo root, where the new path-only fingerprint matches. |

## Checks (security/pentest/test_phase9_r3.py: 19 pass + 1 strict xfail, run ×2)
- **Purge:** 81 delivery-id+hash rows dropped to 0 after the 2 s test window with no traffic (background loop), and 0 rows remained older than the window afterwards. Growth is bounded.
- **Redeliver (same id):** 409 within the window; 202 after the window.
- **Replay (new id) after success:** 409.
- **Event+body hash:** same body as ping → 202, as issues → 202, then ping again → 409.
- **review-api down (503):** 502 and 0 rows stored. After recovery, the same delivery id gets 202 and creates exactly 1 review.
- **pushed_at:**

  | Value | Result |
  |---|---|
  | numeric string | 202 |
  | ISO Z | 202 |
  | ISO +00:00 | 202 |
  | stale ISO | 409 |
  | negative number | 409 |
  | garbage | 400 |
  | empty | 400 |
  | "-5" | 400 |
  | null | 400 |
  | bool | 400 |
  | float string | 400 |
  | future | 202 (see finding) |

- **Race:** FAIL. See the finding.
- **Node 22.12:**
  - ci/agent `node:22.12-bookworm-slim`
  - frontend/Dockerfile `node:22.12-alpine`
  - .nvmrc 22.12
  - engines `>=22.12` in package.json and the lock
  - on real node 22.12.0: `npm ci --engine-strict`, build and 23 vitest tests all OK
- **Docs:** accurate.
  - The base deployment runs 1 replica with no HPA, so the per-replica note is correct.
  - gateway-ingress allows only Traefik (kube-system), frontend and Prometheus.
  - uvicorn 0.34 takes the rightmost untrusted XFF entry: "6.6.6.6, 1.2.3.4, 10.42.0.9" from 10.42.0.5 → 1.2.3.4. A peer outside FORWARDED_ALLOW_IPS has its XFF ignored.

## Regression (clean env -i)

| Suite | Result |
|---|---|
| contracts | 125 passed |
| gateway | 35 passed (also 35 with env vars set) |
| review-api | 183 passed |
| scanner-worker | 135 passed |
| Phase 3 recovery | 12 passed, 2 xfail |
| worker recovery | 7 passed |
| vitest / tsc | 23 passed / 0 |
| ci/tests clean ×2 | 235 passed each |
| ci/tests with env vars | 235 passed |
| pg parity | 32 passed |
| schemathesis | rc 0 |
| kubeconform | rc 0 |
| shellcheck | product scripts unchanged; the 4 extra hits are in the PR's security/*.sh helpers |
| e2e mock | 32 passed |
| e2e full stack | 34 passed |
| pentest (PR copy) | 44 passed ×2 (31 + 13 r2) |

## Scanners (C/H/M)

| Tool / target | C | H | M |
|---|---|---|---|
| Trivy fs, vulns + misconfig | 1 (FP) | 1 (FP) | 10 |
| Trivy config, built manifests | 0 | 3 (FP) | 18 |
| Trivy components, frontend incl. dev deps / gateway / review-api / ci-agent | 0 | 0 | 0 |
| Trivy scanner-worker | 0 | 0 | 1 (setuptools) |
| Gitleaks history | 0 leaks | | |
| Gitleaks tree (from the repo root) | 0 leaks | | |

## Unverified
- concurrency on real Postgres with 2 replicas
- image builds and scans (no docker)
- the k3s demo
