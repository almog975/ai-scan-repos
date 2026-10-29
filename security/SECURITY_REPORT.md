# Phase 9 FINAL GATE r2: PR #10 head 3efcb02 (was 777ceed)

**Verdict: PASS.** There are 0 open CRITICAL/HIGH findings. Every CRITICAL/HIGH the scanners still report was triaged as a false positive with a reason. Both full-stack demo runs were clean. 3 LOW findings are open (below). Only local and fake targets were used. Nothing was pushed, and no product code was changed.

## 1. Diff 777ceed..3efcb02 (31 files)
- The xfail removals made the tests stricter, not weaker. `KNOWN` / `test_known_finding` (xfail strict) was replaced by `CASES += [webhook_replay_new_delivery_rejected, rate_limit_api_random_cookie_429]`, which uses the normal `assert == "PASS"`. pentest_harness.py is byte-identical to ef519a9, so no case or threshold changed.
- New tests: gateway/tests/test_gateway.py (+54), review-api test_phase5_k3s autouse env cleanup, and ci/tests/test_phase9_installer.py with 7 CIDR overlap cases.
- Product changes:
  - cryptography 50.0.2
  - wizard deleted
  - gateway body-hash dedupe, pushed_at window and API limiter key
  - cidr_overlap
  - vite 6.4.5 / vitest 5.0.5 / @vitest/coverage-v8 5.0.5 (pinned)
  - node 22 in ci/agent (22.12-bookworm-slim) and frontend Dockerfile (22-alpine)
  - .gitleaksignore entries

## 2. Findings

| # | Severity | Tool/area | File:line | Status |
|---|---|---|---|---|
| r1-1 | HIGH ×4 | cryptography 44.0.0 | review-api/requirements.txt:7 | **FIXED** (50.0.2; no Trivy hit) |
| r1-2 | HIGH ×4 | python-multipart (wizard) | infra/installer/* | **FIXED** (wizard removed) |
| r1-3 | CRIT ×2 / HIGH | tinypool, vitest, vite (dev) | frontend/package-lock.json | **FIXED** (0 vulns incl. dev deps) |
| r1-8 | MED | webhook replay with new delivery id | gateway/app/main.py:301-323 | **FIXED** (409, also on the other replica and after restart) |
| r1-9 | MED | API limiter random-cookie bypass | gateway/app/main.py:80-84 | **FIXED** (IP bucket 429 at request 300; valid user unaffected) |
| r1-10 | LOW | cidr_ok /8 overlap | aiscan-install.sh:341-368 | **FIXED** (24 installer tests) |
| r1-12 | LOW | test env leak (INTERNAL_API_TOKEN) | review-api/tests/test_phase5_k3s.py:14 | **FIXED** (ci/tests with env: 235 passed) |
| FP | CRIT | Trivy AWS-0104 | upstream terraform-aws-modules/eks node_groups.tf | False positive / third-party (unchanged from r1) |
| FP | HIGH ×4 | Trivy KSV-0109 | scanner-job-template.yaml (+3 overlays) | False positive: matches the key name `automountServiceAccountToken` |
| FP | HIGH ×6 | kubescape C-0012 | deploy/review-api/base/deployment.yaml:24 | False positive: `SCANNER_SECRET_NAME` holds a Secret name |
| FP | ERROR | Semgrep raw SQL | review-api/alembic/versions/0002_contract_v1.py:46 | False positive: constant table names |
| FP | (leak) | Gitleaks dir (tree) | security/pentest/pentest_harness.py:130 | False positive: pentest canary `sk-SECRETKEY123`. The new .gitleaksignore entries are commit-prefixed, so they only match `gitleaks git`. Coder: add the path-only fingerprint `security/pentest/pentest_harness.py:generic-api-key:130` (done in the tests-repo copy). |
| N1 | LOW | webhook dedupe store not bounded | gateway/app/main.py:303-308 | OPEN, owner Coder. An expired `sha256:*` row is deleted only when the same body arrives again, and the delivery-id rows are never purged, so webhook_deliveries grows by 2 rows per delivery. In the test, 33 hash rows older than the 2 s window were still stored. Fix: a periodic `DELETE ... WHERE received_at < now()-window`. Only validly signed deliveries create rows. xfail(strict) test. |
| N2 | LOW | GitHub "Redeliver" / same body | gateway/app/main.py:301 | OPEN, note. The hash covers only the body, so GitHub's Redeliver (same body, new id) now gets 409. That includes a delivery whose processing failed (status review_api_unavailable): the hash is already committed, so it can't be retried. A different event with a byte-identical body is also 409, but in practice GitHub bodies differ per event. Suggest hashing event+body and storing the hash only after a successful _handle_event. |
| N3 | LOW | limits | gateway rate limiter / FORWARDED_ALLOW_IPS | Note. The limiter is in-process per replica, so N replicas allow N×300/min. The key needs `--proxy-headers` with FORWARDED_ALLOW_IPS = pod CIDR (base 10.42.0.0/16, eks 10.40.0.0/16), so any pod in that CIDR can set X-Forwarded-For. The limiter also does one DB lookup per /api request. A non-numeric `pushed_at` skips the window and falls back to hash dedupe only. |
| N4 | INFO | ci/agent vs frontend engines | frontend/package.json | No `engines` field. vitest 5.0.5 requires node ^22.12. ci/agent node:22.12 meets it. frontend/Dockerfile `node:22-alpine` floats (currently ≥22.12). Suggest `"engines": {"node": ">=22.12"}` and pinning 22.12 in the frontend Dockerfile. |

## 3. Scanner counts (CRITICAL / HIGH / MEDIUM)

| Tool / target | C | H | M |
|---|---|---|---|
| Trivy fs repo: vulns | 0 | 0 | 1 (setuptools 80.9.0, scanner-worker) |
| Trivy fs repo: misconfig | 1 (FP) | 1 (FP) | 9 |
| Trivy config, built manifests k3s/local/eks | 0 | 3 (FP) | 18 |
| Trivy comp frontend incl. dev deps | 0 | 0 | 0 |
| Trivy comp review-api / gateway / ci-agent | 0 | 0 | 0 |
| Trivy comp scanner-worker | 0 | 0 | 1 |
| Gitleaks history | 0 leaks | | |
| Gitleaks tree | 1 (FP canary, above) | | |
| Semgrep (p/default, python, security-audit, secrets, typescript, react) | 1 ERROR (FP), 5 WARNING | | |
| kubescape NSA ×3 overlays (99.2) | 0 | 1 (FP) | 1 |
| kubescape MITRE ×3 overlays (87.7) | 0 | 1 (FP) | 2 |

Trivy ran per-component `fs` instead of image scans, because there is no docker on the box.

## 4. Fix checks
- **cryptography 50.0.2:** a key encrypted under 44.0.0 decrypts under 50.0.2, a new encrypt/decrypt round-trips, and a wrong AAD is rejected (security/crypto_compat.py).
- **Wizard:** Dockerfile, app/, requirements.txt and the templates are gone. `git grep 8088|wizard|python-multipart|installer/app` finds only the removal note in docs/infra-README.md:14.
- **Webhook** (security/pentest/test_phase9_r2.py, 12 pass + 1 xfail, run twice):
  - same body with a new delivery id → 409, on the same replica, on a second replica sharing the DB, and after a gateway restart
  - a distinct body → 202
  - push with fresh pushed_at → 202; pushed_at older than 24 h → 409
  - after the TTL (2 s test window) the same body is accepted again
- **Rate limit:** random cookies from one IP get 429 at request 300. A valid session on the same IP is still served, and a cookieless request is 429. A valid user hits their own bucket at 299.
- **cidr_ok overlap:** test_phase9_installer 24 passed (10.0.0.0/8, 10.42.5.0/24, 10.43.0.1/32, 10.32.0.0/11, eks 10.40.7.7/32 and 172.16.0.0/12 rejected; custom POD_CIDR honoured).
- **Clean test env with INTERNAL_API_TOKEN set:** review-api 183 passed, gateway 30 passed, ci/tests 235 passed.
- **vite/vitest:** vitest 5.0.5 and vite 6.4.5 on node 22.12.0, using the official tarball from nodejs.org: `npm ci --engine-strict` OK, `vite build` OK, 23 vitest tests passed, tsc 0. Same result on node 24.

## 5. Regression (clean `env -i`)

| Suite | Result |
|---|---|
| contracts | 125 passed, 5 skipped |
| gateway | 30 passed |
| review-api | 183 passed, 2 skipped, 2 xfailed |
| scanner-worker | 135 passed, 11 skipped |
| Phase 3 recovery | 12 passed, 2 xfailed |
| worker recovery | 7 passed |
| frontend vitest / tsc | 23 passed / rc 0 |
| ci/tests clean ×2 | 235 passed, 3 skipped (each run) |
| ci/tests with typical env | 235 passed, 3 skipped |
| gateway with env | pass |
| pg parity tests | 32 passed |
| db-parity.sh, clean | rc 0 |
| db-parity.sh, injected drift | rc 1 |
| schemathesis | rc 0 (1258 generated, 0 failed) |
| promtool | 12 passed |
| kubeconform | rc 0 |
| shellcheck | 0 errors in product scripts (the same 3 warnings as r1); 4 new warnings were in the test scripts under security/, cleaned up in the tests repo |
| e2e mock | 32 passed, 2 skipped |
| e2e full stack | 34 passed |

## 6. Pentest
- security/pentest/test_phase9_pentest.py: **31/31 pass**, run twice. That is the 29 r1 cases plus the 2 former findings.
- security/pentest/test_phase9_r2.py: **12 pass + 1 strict xfail (N1)**, run twice.

## 7. Demo (local full stack; k3s not feasible: no docker for app images, and the runbook uses external NodeGoat/AI/Jenkins/Argo CD)

| Run | Result | Time |
|---|---|---|
| 1 | 34 passed | 39.2 s (wall 40 s) |
| 2 | 34 passed | 38.4 s (wall 40 s) |

The two runs were back to back. Manual steps: none.

## 8. Unverified
- Image-level scans (no docker)
- the k3s/EKS demo with real images
- the Jenkins/Argo CD stages
- the node:22-alpine frontend image build
- the dedupe behaviour on real Postgres with concurrent replicas (tested on shared SQLite)
- the real Traefik X-Forwarded-For chain
- live browser XSS in report markdown (still a static check only)

Side effect: the shared venv /workspace/aiscan-venv-r2 now has cryptography 50.0.2 (was 44.0.0).
