# AI Scan Repos

AI-assisted security review of public GitHub repositories. A user submits a repo URL and a scan
depth; the **review-api** launches a short-lived **scanner-worker** run that clones the repo, runs
**Semgrep, Gitleaks and Trivy**, optionally asks an AI model for a risk explanation + fixes, and
writes `report.json` / `report.md` to a shared reports volume.

Part of the Technion DevOps final project (monorepo `FinalProject`, branch `ai-scan-phase-1-backend`).
The sibling project `devsecops-shop-*` holds the existing Jenkins / Helm / Argo CD / monitoring work;
see [`../README.md`](../README.md).

> Status: **Phase 1 – backend only.** No gateway, UI app, Helm chart, K8s manifests or CI pipeline
> exist for AI Scan Repos yet (see [Gaps](#gaps--todo)). HTML mockups of the UI live outside the repo
> at `/workspace/ai-scan-ui/` (login, new scan, depth, progress, results, settings).

## Components

| Component | Path | Tech | Role |
|---|---|---|---|
| review-api | `review-api/` | FastAPI, SQLAlchemy 2, Alembic, Postgres/SQLite | Review CRUD, pause/resume, launches scanner jobs, watchdog, report serving, encrypted AI-key storage, Prometheus `/metrics` |
| scanner-worker | `scanner-worker/` | Python CLI (`python -m scanner.run --review-id X`) + semgrep, gitleaks 8.21.2, trivy 0.58.1 | Clone → scan → AI → report, with checkpoint/resume and pause support |

Scan depths (`quick` / `brief` / `deep`):
- **quick** – scanners + short AI bullet summary of the top 30 findings.
- **brief** – scanners + AI explanation/fix for the top `BRIEF_TOP_FINDINGS` (10) findings with ±5 lines of code context.
- **deep** – additionally an AI review of up to `DEEP_MAX_FILES` (40) code files (≤60 KB each), checkpointed per file.

AI providers: `openai_compatible` (on-prem LM Studio/Ollama/vLLM via `AI_BASE_URL`), `openai`, `grok`,
`anthropic`, `gemini`, or `none`. Reports flag when code left the network (cloud provider).

## Quickstart (local, no Kubernetes)

```bash
cd ai-scan-repos
python3 -m venv .venv && . .venv/bin/activate
pip install -r review-api/requirements.txt -r review-api/requirements-dev.txt \
            -r scanner-worker/requirements.txt -r scanner-worker/requirements-dev.txt

# review-api (SQLite + local subprocess launcher are the defaults)
export INTERNAL_API_TOKEN=dev-token
export AI_KEYS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export REPORTS_DIR=$PWD/reports
export LOCAL_WORKER_CWD=$PWD/scanner-worker
export AI_PROVIDER=none          # or openai_compatible + AI_BASE_URL/AI_MODEL
cd review-api && uvicorn --factory app.main:get_app --port 8000
```

In another shell (gitleaks/trivy are skipped with status `skipped` if not on PATH):

```bash
H='-H X-Internal-Token:dev-token -H X-User-Id:alice -H Content-Type:application/json'
curl -s $H -X POST localhost:8000/reviews -d '{"repo_url":"https://github.com/OWASP/NodeGoat","depth":"quick"}'
curl -s $H localhost:8000/reviews/<id>          # status/progress
curl -s $H localhost:8000/reviews/<id>/report.md
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
Both run as UID 10001. review-api runs `alembic upgrade head` then uvicorn on :8000.

## API summary

All non-probe endpoints require `X-Internal-Token`; user endpoints also take `X-User-Id`
(meant to be set by a future gateway). Full detail: [docs/architecture.md](docs/architecture.md).

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz`, `/readyz`, `/metrics` | probes (readyz checks DB), Prometheus |
| POST/GET | `/reviews`, `/reviews/{id}` | create (launches job) / list / get |
| POST | `/reviews/{id}/pause`, `/resume` | pause via `PAUSE` file, resume relaunches |
| GET | `/reviews/{id}/report`, `/report.md` | report when `completed` |
| PUT/GET | `/ai-keys` | store (Fernet-encrypted) / list per-user AI keys |
| GET/POST | `/internal/reviews/{id}`, `/internal/reviews/{id}/progress` | worker heartbeat/progress |

## Docs
- [docs/architecture.md](docs/architecture.md) – components, data flow, security design
- [docs/runbook.md](docs/runbook.md) – deploy, operate, troubleshoot
- [docs/demo-script.md](docs/demo-script.md) – presentation demo

## Gaps / TODO
- TODO: gateway with GitHub OAuth (models `GithubAccount`/`Session` exist, unused).
- TODO: web UI (only static mockups in `/workspace/ai-scan-ui`).
- TODO: Helm chart / K8s manifests (Namespace, Deployment, Service, RBAC for Job creation, PVC, Secrets, NetworkPolicy).
- TODO: Jenkinsfile and Argo CD Application for AI Scan Repos.
- TODO: per-user AI keys are stored but not yet passed to the worker (worker uses `AI_API_KEY` env).
