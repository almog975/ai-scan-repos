# Phase 9 FINAL GATE: PR #10 (head 777ceed, main 36c2953)

**Verdict: FAIL.** 2 real HIGH dependency issues are still open: cryptography 44.0.0 in review-api, and python-multipart 0.0.17 in the installer wizard. The functional regression, the pentest and the demo-equivalent runs are all clean. Only local and fake targets were used. Nothing was pushed, and no product code was changed.

## Findings (CRITICAL/HIGH triaged, plus the pentest and regression findings)

| # | Severity | Tool/area | File:line | Status |
|---|---|---|---|---|
| 1 | HIGH ×4 | Trivy fs / review-api comp | review-api/requirements.txt:7. cryptography 44.0.0 (CVE-2026-26007 fix 46.0.5, CVE-2026-69247 fix 50.0.0, CVE-2026-69249 fix 49.0.0, GHSA-537c-gmf6-5ccf fix 48.0.1) | **OPEN, real, owner Coder.** Bump to >=50.0.0. Reachability is low because only AESGCM is used, but the runtime image ships it, and the Jenkins Trivy HIGH gate would fail the image. |
| 2 | HIGH ×4 | Trivy fs | infra/installer/requirements.txt. python-multipart 0.0.17 (CVE-2024-53981, CVE-2026-24486/42561/53539, fix 0.0.30) | **OPEN, real, owner Coder.** The legacy installer wizard (infra/installer/Dockerfile + app/main.py, uvicorn on 0.0.0.0:8088, no auth, parses Form data) is still documented in docs/infra-README.md:14. Bump fastapi/python-multipart or delete the wizard. |
| 3 | CRITICAL ×2, HIGH ×1 | Trivy comp frontend | frontend/package-lock.json. tinypool 1.1.1 (CVE-2026-104848/104849), vitest 2.1.9 (CVE-2026-47429 CRIT), vite 5.4.21 (CVE-2026-112942) | Justified: dev/test-only. devDependencies are not in the nginx runtime image, and the root `trivy fs` (prod deps) does not report them. Recommended Coder upgrade: vitest 3.2.6+, vite 6.4.4+. If the gate counts dev deps, this becomes OPEN too. |
| 4 | CRITICAL | Trivy fs (terraform) | terraform-aws-modules/eks/aws/node_groups.tf (remote module from infra/installer/terraform/eks/main.tf:31) | False positive / third-party: AWS-0104 is the upstream EKS module's default node egress 0.0.0.0/0, which EKS nodes need (ECR/STS). It is not repo code. |
| 5 | HIGH ×4 | Trivy config | deploy/scanner-worker/base/scanner-job-template.yaml (+3 built overlays) | False positive: KSV-0109 "ConfigMap with secrets" matches the key name `automountServiceAccountToken`. Secrets come via secretRef. |
| 6 | HIGH (×6 runs) | kubescape C-0012 | deploy/review-api/base/deployment.yaml:24 | False positive: `SCANNER_SECRET_NAME=scanner-secrets` is a Secret *name*. All credentials use secretKeyRef/envFrom secretRef. |
| 7 | ERROR (HIGH impact) | Semgrep sqlalchemy-execute-raw-query / formatted-sql-query | review-api/alembic/versions/0002_contract_v1.py:46 | False positive: f-string over constant table/type names in the migration downgrade. No user input. |
| 8 | MEDIUM | Pentest webhook | gateway/app/security.py:14 | OPEN, owner Coder. The HMAC doesn't cover X-GitHub-Delivery, so a captured delivery replayed with a new delivery id is accepted (202). Effect: it can re-trigger auto-reviews. xfail(strict) test. |
| 9 | MEDIUM | Pentest rate limit | gateway/app/ratelimit (api keyed by cookie value) | OPEN, owner Coder. 320 requests with random aiscan_session cookies never got a 429. The requests are cheap 401s, but the limiter can be bypassed. xfail(strict) test. |
| 10 | LOW | Installer egress | infra/installer/aiscan-install.sh cidr_ok | /8 accepted, so 10.0.0.0/8 overlaps pod/service CIDRs. Note only. |
| 11 | LOW | Semgrep | launcher.py:258 (log text "secret", no value), tools.py:20 (sha1 = contract id), e2e test urllib, installer template no CSRF (plan-only), EFS CMK | False positive or accepted. |
| 12 | LOW (test) | Regression ci-env | review-api/tests/test_phase5_k3s.py::test_production_rejects_dev_defaults | Fails when INTERNAL_API_TOKEN is set in the shell (env leak, same class as the gateway fix in #10). Owner Coder: autouse env cleanup. |

## Scanner counts (CRITICAL / HIGH / MEDIUM)

| Tool / target | C | H | M |
|---|---|---|---|
| Trivy fs repo: vulns | 0 | 8 | 5 |
| Trivy fs repo: misconfig (incl. Dockerfiles, terraform) | 1 | 1 | 9 |
| Trivy config, built manifests k3s/local/eks | 0 | 3 | 18 |
| Trivy comp frontend (incl. dev) | 3 | 1 | 10 |
| Trivy comp review-api | 0 | 4 | 0 |
| Trivy comp scanner-worker | 0 | 0 | 1 |
| Trivy comp gateway | 0 | 0 | 0 |
| Trivy comp ci-agent | 0 | 0 | 0 (1 LOW) |
| Gitleaks working tree / full history | 0 leaks | | |
| Semgrep p/default+python+security-audit+secrets+typescript+react (663 rules, 303 files) | 1 ERROR, 6 WARNING (impact: 2 HIGH, 4 MED, 1 LOW) | | |
| kubescape NSA (k3s/local/eks): score 99.2 each | 0 | 1 | 1 |
| kubescape MITRE (k3s/local/eks): score 87.7 each | 0 | 1 | 2 (C-0007, C-0053 on the review-api job-launcher RoleBinding: by design) |

Trivy ran per-component `fs` instead of image scans because there is no docker on the box. The images were not built or scanned.

## Pentest matrix (local gateway + review-api + fake GitHub; security/pentest, 29 pass + 2 xfail, run twice)

| Case | Result |
|---|---|
| Session cookie HttpOnly + SameSite=Lax; CSRF cookie readable | PASS |
| CSRF missing / wrong token on POST → 403 | PASS |
| Unauthenticated /api → 401; SSE /progress without a session → 401 | PASS |
| IDOR: other user's review, report.json, retry, pause → 404 | PASS |
| Admin route as non-admin → 403 (admin 200) | PASS |
| Path traversal / non-UUID ids in report routes → 404 | PASS |
| SSRF repo_url: file://, 169.254.169.254, localhost, gitlab, `--upload-pack`, user:pass@, ssh:// → rejected | PASS |
| XSS in repo name → 400 | PASS |
| 2 MB payload / malformed JSON → 400 | PASS |
| Webhook: good sig 202, missing 401, bad 401, same-delivery replay 409 | PASS |
| Webhook replay with new delivery id | **FAIL (finding 8)** |
| OAuth login redirect fixed to the configured GitHub; forged state → 400 | PASS |
| Provider API key absent from API responses and service logs | PASS |
| Logout revokes the session | PASS |
| Auth rate limit → 429 after ~20 | PASS |
| API rate limit with random cookies | **FAIL (finding 9)** |
| Security headers/CSP/HSTS (frontend/e2e/test_nginx_headers.py, 12 tests incl. HSTS at Traefik) | PASS |
| XSS in report markdown | Static only: ReactMarkdown without rehype-raw, CSP without unsafe-inline, report.md served text/markdown + nosniff. Not exercised live in a browser. |

## Regression (clean `env -i`)

contracts 125p/5s · gateway 26p · review-api 149p/38s · scanner-worker 135p/11s · Phase 3 recovery 12p/2xf · worker recovery 7p · frontend vitest 23p · tsc 0 · ci/tests clean ×2: 228p/3s each · ci/tests with typical env: 227p **1 failed** (finding 12) · gateway with env: pass · pg parity tests 32p · db-parity.sh on postgres: clean rc 0, injected drift (extra column in schema.sql) **rc 1** · schemathesis rc 0 (1346 generated, 0 failed) · promtool (test_phase7_monitoring) 12p · kubeconform rc 0 · shellcheck: 3 warnings/info (SC2155 ×2, SC2015), no errors · e2e mock + full stack 34p.

PR #10 items verified: egress vars and CIDR rejection, REGISTRY_HOST default registry.registry:5000 (aiscan-install.sh:84), purge message, strict `(NotFound)` match (test_phase9_installer), gateway GITHUB_CLIENT_ID autouse cleanup, postgres-backups and postgres-data `Delete=false,Prune=false` in all 3 built overlays (eks gp3).

## Demo

docs/runbooks/demo-script.md cannot run on this box as written. It needs app images in k3s (no docker, so ErrImagePull), a live GitHub clone of OWASP/NodeGoat (external), Ollama/Grok, and Jenkins/Argo CD. Instead I ran the local full stack: the e2e suite with AISCAN_FULLSTACK=1, which covers the GitHub login, the start of a review, live SSE progress, pause/resume, the report and its download, the cloud-ack gate, the provider test, Hebrew RTL and axe. Both runs went back to back with no intervention: run 1 had 34 passed in 39.0 s, run 2 had 34 passed in 39.4 s. Manual steps: none.

## Unverified

Image scans, the demo on k3s/EKS with real images, the Jenkins/Argo CD stages, the live browser XSS check of report markdown, and the registries.yaml mirror on a real node (a documented manual step).

One review-api request in an early pentest attempt went to the public api.github.com (an unauthenticated repo lookup for a nonexistent fixture repo) before I injected a fake GitHub. It was not an attack. All later runs were fully local.
