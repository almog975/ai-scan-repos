# CI/CD – build interface (Phase 6)

Pipeline flow and Argo CD: [ci.md](ci.md), [ci-cd-flow.md](ci-cd-flow.md), [ADR-0004](adr/0004-gitops.md).
This page is the contract between the Jenkinsfile and the components.

## Prerequisites (once per cluster, in this order)

1. **Secret `registry-htpasswd`** (ns of the registry, key `htpasswd`). The in-cluster registry
   (`infra/bootstrap/registry/registry.yaml`) starts with `REGISTRY_AUTH=htpasswd` and **will not start without it**.
   Create it only via [registry-auth-runbook.md](registry-auth-runbook.md) or the installer – never commit it.
2. `regcred` pull secret (namespaces ai-scan-repos and jenkins) and the Jenkins `registry-push` credential – same runbook.
3. Argo CD AppProjects `ai-scan` and `ai-scan-ci` (`infra/argocd/`), then `infra/argocd/root-app.yaml`.

## Make targets (non-interactive, real exit codes)

| Target | frontend | gateway / review-api / scanner-worker |
|---|---|---|
| `make test` | `npm ci` (if needed) + vitest, JUnit `reports/junit.xml` | pytest, JUnit `reports/junit.xml` (or the `--junitxml` passed in `PYTEST_ADDOPTS`) |
| `make build` | `docker build --build-arg VITE_APP_VERSION=$(GIT_SHA)` | `docker build` |
| `make dist` | static bundle only (`npm run build`) | – |

- Root `Makefile`: `make test` / `make build` delegate to every component (`make test-gateway`, …); `make contracts`.
- Shared variables in `ci/mk/common.mk`: `GIT_SHA` (default `git rev-parse --short HEAD`), `REGISTRY`, `IMAGE=$(REGISTRY)/<component>:$(GIT_SHA)`.
  Images are always tagged with the git sha, never `latest`.
- A missing tool (pytest, npm, docker) fails with exit 2 and a message; nothing is silently skipped.
- Tests run on the Jenkins agent only; Dockerfiles only build. In Jenkins, images are built by kaniko (no Docker daemon)
  with the same Dockerfiles and the same `VITE_APP_VERSION=<sha>` build arg.

## Version badge

The frontend footer shows `Version <sha>` (`services/frontend/src/version.ts`, `import.meta.env.VITE_APP_VERSION`, `dev` when
not injected). It equals the image tag, so the running build can be matched to a commit and to the Argo CD revision.

## DB parity (`ci/db-parity.sh`)

- Jenkins: the `ai-scan-agent` pod has a `postgres:16-alpine` sidecar (pod-local, trust auth, no password);
  the tools container gets `PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres` and `postgresql-client-16`.
- Locally: without `PGHOST` the script falls back to `docker run postgres:16-alpine`. With your own server:
  `PGHOST=… PGPORT=… [PGPASSWORD=…] ci/db-parity.sh` (throwaway databases are created and dropped).

## Deploy tag bump – automatic PR

The Deploy stage (main only) runs `kustomize edit set image` / `set configmap scanner-image`, commits with
`[skip ci]` on `deploy/<svc>-<sha>` (or `deploy/multi-<sha>`), and `ci/deploy/open-pr.sh` pushes it and opens a PR
to `main` (credential `github-bot`, see infra/jenkins/README.md). Almog merges; Argo CD syncs. Details:
[ci-cd-flow.md](ci-cd-flow.md).
