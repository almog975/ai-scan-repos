# AI Scan Repos

AI-assisted security review of public GitHub repositories. A user submits a repo URL and a scan
depth; the **review-api** launches a short-lived **scanner-worker** run that clones the repo, runs
**Semgrep, Gitleaks and Trivy**, optionally asks an AI model for a risk explanation + fixes, and
writes `report.json` / `report.md` to a shared reports volume.

Technion DevOps final project. Standalone repo `almog975/ai-scan-repos`; all paths are relative to the repo root
([ADR-0001](docs/adr/0001-repo-layout.md)). `contracts/` is the source of truth — code follows it.

## Layout (Phase 0 / Foundation)

```
./  (repo root)
  contracts/       OpenAPI 3.1 (gateway, review-api) + JSON Schema 2020-12 (common, finding, report, checkpoint, progress)
  frontend/        React/Vite -> nginx-unprivileged, :8080
  gateway/         FastAPI, :8000  (/healthz /readyz /metrics)
  review-api/      FastAPI, :8001  (/healthz /readyz /metrics) – Alembic migrations in review-api/alembic/
  scanner-worker/  Python Job image; probe server :8002 (/healthz /readyz /metrics)
  deploy/<c>/       per-component Kustomize base + overlays/{local,k3s,eks} (see docs/kustomize-layout.md)
  infra/db/        target PostgreSQL schema v1 (schema.sql, migrations/001_init.sql)
  infra/           other infra-level assets
  deploy/argocd/          Argo CD app-of-apps: root-app.yaml + apps/<component>.yaml (see deploy/README.md)
  docs/            adr/, diagrams/, runbooks/, ci.md
  Jenkinsfile      CI: detect changes → test → build → Trivy → push → deploy (tag bump)
  Makefile         `make test` runs every service's `make test` (passes when a suite is empty); `make contracts` validates contracts
```

Fixed ports: frontend 8080, gateway 8000, review-api 8001, scanner-worker probes 8002.
Deferred past v0.1: per-depth estimates and server-side findings paging (see contracts/README.md).


## Components

| Component | Path | Tech | Role |
|---|---|---|---|
| review-api | `review-api/` | FastAPI, SQLAlchemy 2, Alembic, Postgres/SQLite | Review CRUD, pause/resume, launches scanner jobs, watchdog, report serving, encrypted AI-key storage, Prometheus `/metrics` |
| scanner-worker | `scanner-worker/` | Python CLI (`python -m scanner.run --review-id X`) + semgrep, gitleaks 8.21.2, trivy 0.76.1 | Clone → scan → AI → report, with checkpoint/resume and pause support |

Scan depths (`quick` / `brief` / `deep`):
- **quick** – scanners + short AI bullet summary of the top 30 findings.
- **brief** – scanners + AI explanation/fix for the **top 10 findings** (`BRIEF_TOP_FINDINGS`) with ±5 lines of code context (adopted into the plan/contracts).
- **deep** – additionally an AI review of up to `DEEP_MAX_FILES` (40) code files (≤60 KB each), checkpointed per file.

AI providers: `openai_compatible` (on-prem LM Studio/Ollama/vLLM via `AI_BASE_URL`), `openai`, `grok`,
`anthropic`, `gemini`, or `none`. Reports flag when code left the network (cloud provider).

## Quickstart (local, no Kubernetes)

```bash
git clone https://github.com/almog975/ai-scan-repos.git && cd ai-scan-repos
python3 -m venv .venv && . .venv/bin/activate
pip install -r review-api/requirements.txt -r review-api/requirements-dev.txt \
            -r scanner-worker/requirements.txt -r scanner-worker/requirements-dev.txt

# review-api (SQLite + local subprocess launcher are the defaults)
export INTERNAL_API_TOKEN=dev-token
export AI_KEYS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export REPORTS_DIR=$PWD/reports
export LOCAL_WORKER_CWD=$PWD/scanner-worker
export AI_PROVIDER=none          # or openai_compatible + AI_BASE_URL/AI_MODEL
cd review-api && uvicorn --factory app.main:get_app --port 8001
```

In another shell (gitleaks/trivy are skipped with status `skipped` if not on PATH):

```bash
H='-H X-Internal-Token:dev-token -H X-User-Id:alice -H Content-Type:application/json'
curl -s $H -X POST localhost:8001/reviews -d '{"repo_url":"https://github.com/OWASP/NodeGoat","depth":"quick"}'
curl -s $H localhost:8001/reviews/<id>          # status/progress
curl -s $H localhost:8001/reviews/<id>/report.md
```

### Tests

```bash
cd review-api && pytest -q        # API, validation, watchdog, crypto, launcher
cd scanner-worker && pytest -q    # parsers/report (fixtures), AI client (mock transport), worker resume/pause
```

### Container images

```bash
docker build -t ai-scan-repos/review-api:dev review-api
docker build -t ai-scan-repos/scanner-worker:dev scanner-worker
```
Both run as UID 10001. review-api serves uvicorn on :8001; migrations run separately (K8s Job `review-api-migrate`, Argo CD PreSync; compose service `migrate`).

## API summary

All non-probe endpoints require `X-Internal-Token`; user endpoints also take `X-User-Id`
(meant to be set by a future gateway). Full detail: [docs/diagrams/architecture.md](docs/diagrams/architecture.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz`, `/readyz`, `/metrics` | probes (readyz checks DB), Prometheus |
| POST/GET | `/reviews`, `/reviews/{id}` | create (launches job) / list / get |
| POST | `/reviews/{id}/pause`, `/resume` | pause via `PAUSE` file, resume relaunches |
| GET | `/reviews/{id}/report`, `/report.md` | report when `completed` |
| GET/POST/PATCH/DELETE | `/admin/ai-providers` | admin-managed AI providers (AES-256-GCM keys); `GET /ai-providers` lists them without keys |
| GET/POST | `/internal/reviews/{id}`, `/internal/reviews/{id}/progress` | worker heartbeat/progress |

## Folder READMEs
[contracts](contracts/README.md) · [frontend](frontend/README.md) · [gateway](gateway/README.md) · [review-api](review-api/README.md) · [scanner-worker](scanner-worker/README.md) · [deploy](deploy/README.md) · [infra](infra/README.md)

## Docs
- [docs/adr/](docs/adr/) – [template](docs/adr/0000-template.md), [0001 repo layout](docs/adr/0001-repo-layout.md), [0002 Job per review](docs/adr/0002-k8s-job-per-review.md)
- [docs/diagrams/architecture-v1.md](docs/diagrams/architecture-v1.md) – target architecture ([SVG](docs/diagrams/architecture-v1.svg))
- [docs/diagrams/architecture.md](docs/diagrams/architecture.md) – Phase 1 backend as built: data flow, security, config
- [docs/runbooks/runbook.md](docs/runbooks/runbook.md) – deploy, operate, troubleshoot
- [docs/runbooks/demo-script.md](docs/runbooks/demo-script.md) – presentation demo
- [docs/ci.md](docs/ci.md) – CI/CD and GitOps

## Gaps / TODO
- TODO: align code with `contracts/` (states, checkpoint, report/finding shapes, endpoints) — see [ADR-0002](docs/adr/0002-k8s-job-per-review.md).
- TODO: per-user AI keys are stored but not passed to the worker (worker uses `AI_API_KEY` env).
- TODO: Jenkins push step is a stub (see [docs/ci.md](docs/ci.md)).
