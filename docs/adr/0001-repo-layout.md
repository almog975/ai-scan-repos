# ADR-0001: Repository layout — standalone repo `almog975/ai-scan-repos`

- **Status:** Accepted (2026-10-28, decided by Almog; supersedes the monorepo-folder and 6+2-repo layouts)
- **Date:** 2026-10-28
- **Deciders:** Almog (owner), AI Scan Planner (A); Coder, Pipelines, Infra, Docs informed
- **Related:** [ADR-0002](0002-k8s-job-per-review.md), [architecture v1](../diagrams/architecture-v1.md)

## Context
AI Scan Repos needs its own lifecycle (pipelines, branch protection, webhooks, Argo CD) independent of
other course work. The team is small (one human + bots) and contracts, code and manifests usually change
together in the same PR.

## Decision
AI Scan Repos is a **standalone repository, `almog975/ai-scan-repos`**, with all folders at the repo root:

| Folder | Contents | Owner (R) |
|---|---|---|
| `contracts/` | `gateway.openapi.yaml`, `review-api.openapi.yaml`, `finding/report/checkpoint.schema.json` — **source of truth** | Planner |
| `frontend/` | React+Vite SPA, nginx image | Coder |
| `gateway/` | GitHub OAuth, sessions, webhook HMAC, `/api` proxy | Coder |
| `review-api/` | reviews REST, reconciler/watchdog, Job launcher | Coder |
| `scanner-worker/` | scanner Job image (semgrep, gitleaks, trivy, AI) | Coder |
| `deploy/` | `deploy/<component>/{base,overlays}` — Kustomize base + overlays (`local`, `k3s`, `eks`) per component | Infra (content), Pipelines (image tags, Argo CD apps) |
| `infra/` | K3s config, DB schema/migrations, kube-prometheus-stack values, installer/Terraform (TODO) | Infra |
| `docs/` | `adr/`, `diagrams/`, `runbooks/` | Docs |

Rules:
- **Contracts are the source of truth.** Where code and `contracts/` disagree, the code changes to follow
  the contracts (via PR that also updates contract tests). One exception has been adopted into the plan:
  **Brief depth sends the top 10 findings** (with code context) to the AI, not every file with findings.
- **GitOps:** Argo CD Applications point at `deploy/<component>/overlays/<target>` paths in this
  repo (targets: `local`, `k3s`, `eks`). Jenkins bumps image tags only under `deploy/` (`kustomize edit set image`).
- Per-component CI: Jenkins path filters (`frontend/**`, `gateway/**`, …) trigger only the affected build;
  each folder has `make test` (root `Makefile` runs all, `make contracts` validates contracts).
- No Secret YAML anywhere in git; secrets are created from the CLI (runbook). Gitleaks runs on every build.
- CODEOWNERS per folder enforces the Infra/Pipelines split in `deploy/`.

- **Argo CD** has one Application per component and target, each watching
  `deploy/<component>/overlays/<target>` where `<target>` ∈ `local`, `k3s`, `eks`.

## Consequences
- + One PR can change a contract, its implementations and manifests atomically; no cross-repo versioning.
- + Own webhooks, branch protection, CI and Argo CD source — nothing shared with other projects.
- + Paths in docs, CI and Argo CD are relative to the repo root (no project prefix).
- − Jenkins needs write access to this repo (limited by convention + CODEOWNERS to `deploy/` tag bumps);
  Argo CD Applications are path-scoped so commits outside `deploy/` don't trigger syncs.
- − Pipelines must use path filters to avoid rebuilding every component.
- Done: manifests live in `deploy/<component>/{base,overlays/{local,k3s,eks}}`, matching `deploy/argocd-app.yaml`, the Jenkinsfile and [docs/ci.md](../ci.md).

## Alternatives
| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Folder inside the `almog975/FinalProject` monorepo | Already existed; one repo for all course work | Shared webhooks/CI/branch rules with unrelated projects; every path prefixed; Argo CD and Jenkins scoped to a sub-folder | **Rejected** by Almog — project moved to its own repo |
| 6+2 separate repos (3 code, 3 deploy, contracts, installer) | Independent pipelines; Jenkins writes only to deploy repos | 8 repos to create/protect; cross-repo contract versioning; more webhooks | **Rejected** — too much overhead for the team |
