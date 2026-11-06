# AI Scan Repos

AI-assisted security review of GitHub repositories. A user logs in with GitHub, pastes a repo URL, and picks a depth and an AI provider. **review-api** then launches a short-lived **scanner-worker** Kubernetes Job (one per review). The Job clones the repo, runs **Semgrep, Gitleaks and Trivy**, optionally asks an AI model for explanations and fixes, and writes `report.json` / `report.md` to a shared reports volume. Reviews can be paused and resumed from a per-file checkpoint and recover automatically when a Job gets stuck.

Technion DevOps final project. Standalone repo `almog975/ai-scan-repos`; all paths are relative to the repo root ([ADR-0001](docs/adr/0001-repo-layout.md)). `contracts/` is the source of truth for APIs and schemas.

## Architecture

![Architecture](docs/diagrams/architecture-v1.svg)

[architecture-v1.md](docs/diagrams/architecture-v1.md) (target, mermaid) · [architecture.md](docs/diagrams/architecture.md) (backend as built) · [review state machine](docs/diagrams/review-state-machine.md)

| Component | Path | Tech | Port | Role |
|---|---|---|---|---|
| frontend | `frontend/` | React + Vite → nginx-unprivileged | 8080 | SPA (English/Hebrew): new review, live progress, report, admin AI providers |
| gateway | `gateway/` | FastAPI | 8000 | GitHub OAuth, sessions + CSRF, `/me`, proxies `/api/*` to review-api with the internal token |
| review-api | `review-api/` | FastAPI, SQLAlchemy 2, Alembic, Postgres (SQLite in dev) | 8001 | Reviews, state machine, Job launcher, reconciler, reports, encrypted AI keys, `/metrics` |
| scanner-worker | `scanner-worker/` | Python (`python -m scanner.run --review-id X`) + semgrep, gitleaks, trivy | 8002 (probes) | Clone → scan → AI → report; checkpoint after each file |
| postgres | `deploy/postgres/` | PostgreSQL 16 | 5432 | Database |
| platform | `deploy/platform/` | Kustomize | – | Reports PVC, ingress, NetworkPolicies, ServiceMonitors, alerts, `reports-cleanup` CronJob |

**Depths:** `quick` = scanners + a short AI summary · `brief` = AI on the **top 10 findings** with code context · `deep` = AI on every code file (up to `DEEP_MAX_FILES`, 40).
**AI providers** are managed by admins. The DB `kind` (`onprem` | `cloud`) decides cloud vs on-prem. Cloud providers need `ack_cloud`, and their reports carry a cloud banner. Grok runs as `openai_compatible` with `https://api.x.ai/v1`, `kind: cloud`.
**Review states:** queued, running, paused, stuck, retrying, completed, failed, cancelled. Pause is allowed only from `running`; other states return 409.

## Quickstart

### 1. Local (Python only, no OAuth, curl against review-api)

```bash
git clone https://github.com/almog975/ai-scan-repos.git && cd ai-scan-repos
python3 -m venv .venv && . .venv/bin/activate
pip install -r review-api/requirements.txt -r scanner-worker/requirements.txt   # includes semgrep
# gitleaks / trivy are used if on PATH, otherwise reported as "skipped"
export INTERNAL_API_TOKEN=dev-token AI_PROVIDER=none \
       REPORTS_DIR=$PWD/.local/reports WORK_DIR=$PWD/.local/work LOCAL_WORKER_CWD=$PWD/scanner-worker \
       DATABASE_URL=sqlite:///$PWD/.local/aiscan.db AI_KEYS_ENCRYPTION_KEY=$(openssl rand -hex 32)
mkdir -p .local
(cd review-api && uvicorn --factory app.main:get_app --port 8001) &   # port 8001: the local worker reports back here
```

review-api is internal: it expects `X-Internal-Token` and an existing user id in `X-User-Id` (the gateway normally
sets both). `app.devseed` creates a dev user (idempotent, SQLite only by default) and prints the curl headers:

```bash
H=$(cd review-api && python -m app.devseed alice)
ID=$(curl -s $H -X POST localhost:8001/reviews -d '{"repo_url":"https://github.com/OWASP/NodeGoat","depth":"quick"}' \
     | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s $H localhost:8001/reviews/$ID            # queued → running → completed
curl -s $H localhost:8001/reviews/$ID/report.md
```

(Verified 2026-10-28 with NodeGoat, `quick`: completed with a report.)

### 2. Docker Compose (full stack with UI)

Needs a GitHub OAuth App with callback `http://localhost:8080/auth/github/callback`.

```bash
cp .env.example .env     # fill GITHUB_CLIENT_ID/SECRET, AI_KEYS_ENCRYPTION_KEY=$(openssl rand -hex 32), INTERNAL_API_TOKEN
docker compose up --build   # AISCAN_HTTP_PORT (default 8080) and WORK_DIR are in .env
# open http://localhost:8080 → Log in with GitHub (the first user becomes admin)
```

Services: postgres → `migrate` (alembic) → review-api (local launcher, :8001 on localhost) → gateway (:8000) → frontend (:8080).

### 3. Local Kubernetes (lightweight, Docker Desktop or k3d)

No registry, no manual secrets, no NetworkPolicies. PowerShell-friendly (no WSL required for this path).

```powershell
# from the repo root — build local tags (contexts = service dirs, same as Makefiles)
docker build -t ai-scan-repos-review-api:latest review-api
docker build -t ai-scan-repos-gateway:latest gateway
docker build -t ai-scan-repos-frontend:latest frontend
docker build -t ai-scan-repos-scanner-worker:latest scanner-worker
# k3d only (skip on Docker Desktop Kubernetes):
#   k3d image import ai-scan-repos-review-api:latest ai-scan-repos-gateway:latest ai-scan-repos-frontend:latest ai-scan-repos-scanner-worker:latest -c <cluster>
kubectl apply -k deploy/overlays/local
kubectl get pods -n ai-scan-repos
kubectl -n ai-scan-repos port-forward svc/frontend 8080:8080
# open http://localhost:8080
# GitHub OAuth App: Authorization callback URL = http://localhost:8080/auth/github/callback (exactly)
# migrate logs: kubectl -n ai-scan-repos logs job/review-api-migrate
# teardown:     kubectl delete -k deploy/overlays/local
```

Or skip the local builds and pull from GHCR (after the Images workflow has pushed and the
packages are Public): `kubectl apply -k deploy/overlays/local-registry`. Details:
[docs/local-k3d.md](docs/local-k3d.md#github-container-registry).

Full notes (placeholders are NOT FOR PRODUCTION): [docs/local-k3d.md](docs/local-k3d.md#lightweight-overlay).

Auto-heal end to end on this stack (WSL / Git Bash): `ci/heal/e2e-local.sh`. One heal run: `kubectl -n ai-scan-repos create job --from=cronjob/ai-scan-autoheal heal-now` then `kubectl -n ai-scan-repos logs job/heal-now` (same commands in PowerShell). See [docs/local-k3d.md](docs/local-k3d.md#auto-heal-on-the-local-stack-end-to-end).

### 3b. Local Kubernetes via `make local-k8s` (k3d + registry + Argo CD)

```bash
cp .env.example .env     # INTERNAL_API_TOKEN >= 32 chars here
make local-k8s           # k3d cluster + registry, images, secrets from .env, deploy, Argo CD
# open https://ai-scan.localhost  (OAuth App callback: https://ai-scan.localhost/auth/github/callback)
make local-k8s-down
```

Uses `deploy/*/overlays/local` (k3d registry `:dev`), not `deploy/overlays/local`. Windows: run inside WSL2. Details: [docs/local-k3d.md](docs/local-k3d.md).

### 4. K3s (GitOps)

```bash
cp infra/installer/installer.env.example infra/installer/installer.env   # fill REPLACE_ME (never committed)
infra/installer/aiscan-install.sh --config infra/installer/installer.env --dry-run install
infra/installer/aiscan-install.sh --config infra/installer/installer.env install
```

`aiscan-install.sh` is a CLI (there is no web installer; the old web wizard was removed in Phase 9). It runs preflight → bootstrap → registry → secrets → images → deploy (Argo CD + app-of-apps) → verify. Details: [installer.md](docs/installer.md), [k3s.md](docs/k3s.md), [runbook](docs/runbooks/runbook.md).

### Tests

```bash
make contracts          # validate contracts/
make test               # every component (pytest / vitest), JUnit in <component>/reports/
cd frontend/e2e && python -m pytest -q   # Playwright e2e (mock stack); AISCAN_FULLSTACK=1 for the real stack
```

## CI/CD

GitHub Actions (`.github/workflows/images.yml`) builds and pushes `ghcr.io/almog975/ai-scan-repos-<svc>:sha-<short>` (+ `:latest` on main) for the local-registry overlay. Jenkins (k8s agent, kaniko, no Docker daemon): **Test** (contracts, unit, schemathesis, DB parity) → **Build** → **Trivy** (fail on HIGH/CRITICAL) → **Push** `<registry>/ai-scan/<svc>:<sha>` (main) → **tag bump** in `deploy/<component>/overlays/k3s` (`[skip ci]`). Argo CD (app-of-apps `ai-scan-root`, apps `ai-scan-<component>-k3s`, ns `ai-scan-repos`) syncs `main` with prune + selfHeal. Rollback = `git revert` of the tag bump via PR.

**Auto-heal:** Jenkins job `ai-scan-autoheal` (every 10 min + Build Now, `DRY_RUN` parameter) deletes crashed/evicted pods, re-runs a failed migrate Job, restarts deployments failing `/readyz`, checks postgres and syncs OutOfSync Argo CD apps, with namespaced RBAC only ([docs/autoheal.md](docs/autoheal.md)).

**Deploy:** Jenkins pushes the tag bump to `deploy/<svc>-<sha>` and opens a PR (`[skip ci]`); Almog merges; Argo CD syncs ([docs/ci-cd-flow.md](docs/ci-cd-flow.md)). Needs the `github-bot` PAT ([deploy/jenkins/README.md](deploy/jenkins/README.md#github-bot-pat-deploy-pr-flow)).

[ci-cd-flow.md](docs/ci-cd-flow.md) (diagram) · [ci.md](docs/ci.md) · [ci-cd.md](docs/ci-cd.md) · [ADR-0004 GitOps](docs/adr/0004-gitops.md) · [rollback](docs/runbooks/rollback.md)

## Documentation

| For | Docs |
|---|---|
| Users | [User guide](docs/user-guide.md) |
| Admins / operators | [Admin guide](docs/admin-guide.md) · [AI providers](docs/runbooks/admin-ai-providers.md) · [secrets](docs/secrets-runbook.md) · [monitoring](docs/monitoring.md) · [stuck review](docs/runbooks/stuck-review.md) · [rollback](docs/runbooks/rollback.md) · [storage](docs/storage-plan.md) |
| Demo | [Live demo script](docs/runbooks/demo-script.md) · [Demo video script](docs/demo-video-script.md) |
| Design | [ADRs](docs/adr/) · [report.md spec](docs/report-md-spec.md) · [severity mapping](docs/severity-mapping.md) · [contracts](contracts/README.md) |
| Everything | [docs/README.md](docs/README.md) |

Folder READMEs: [contracts](contracts/README.md) · [frontend](frontend/README.md) · [gateway](gateway/README.md) · [review-api](review-api/README.md) · [scanner-worker](scanner-worker/README.md) · [deploy](deploy/README.md) · [infra](infra/README.md)

## Status

**Done:**
- Phases 0–8: contracts, backend, scanner with AI, recovery and reconciler, frontend (English/Hebrew, accessibility checked), K3s overlays, CI build interface, monitoring, installer.

**Open:**
- `github-bot` PAT not created yet: until then the Deploy stage fails at the PR step (images are already pushed).
- No tool to re-encrypt AI keys after rotating the master key ([ADR-0003](docs/adr/0003-ai-key-rotation.md), deferred).
- No automatic backups of reports or the database.

More: [known-limitations.md](docs/known-limitations.md).
