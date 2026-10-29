# CI/CD flow: Jenkins → registry → Git tag bump → Argo CD

How a commit becomes a running deployment on k3s. Source of truth: the root [`Jenkinsfile`](../Jenkinsfile) and [`deploy/argocd/`](../deploy/argocd/). Pipeline details and the webhook relay: [ci.md](ci.md) (owned by Pipelines). Decision record: [ADR-0004](adr/0004-gitops.md).

```mermaid
flowchart LR
  dev[git push] -->|GitHub webhook via smee.io| J[Jenkins]
  subgraph Jenkins pipeline
    G[Loop guard<br/>skip on '[skip ci]' or deploy/-only] --> D[Detect changes<br/>git diff → services]
    D --> C[Contracts + Test<br/>make test / vitest]
    C --> P[DB parity + Schemathesis]
    P --> B[docker build<br/>&lt;registry&gt;/ai-scan/&lt;svc&gt;:&lt;sha&gt;]
    B --> T[Trivy image<br/>fail on HIGH/CRITICAL]
    T --> R[docker push<br/>main only]
    R --> K[Deploy: tag bump in<br/>deploy/&lt;svc&gt;/overlays/k3s<br/>commit '[skip ci]']
  end
  J --> G
  K -->|git push – TODO stub| GH[(GitHub main)]
  GH -->|poll| A[Argo CD ai-scan-root]
  A --> Apps[ai-scan-&lt;component&gt;-k3s<br/>ns ai-scan-repos]
  R --> REG[(Registry)]
  Apps -->|pull image| REG
```

## 1. Jenkins: build, test, scan

Triggered by `githubPush()` (webhook relayed by smee.io). Parameter `TARGET` = `k3s` (default) | `local` | `eks`; registry host per target, e.g. `registry.registry:5000` for k3s (in-cluster Service, plain HTTP; crane push uses `--insecure`).

| Stage | What it does |
|---|---|
| Loop guard | Skips (NOT_BUILT) if the last commit message has `[skip ci]` or only `deploy/` changed – so Jenkins' own tag-bump commits don't re-trigger it |
| Detect changes | `git diff` since the last successful build → changed services among frontend, gateway, review-api, scanner-worker |
| Install CI deps | Two venvs (scanner-worker separate because of a semgrep/schemathesis dependency conflict) |
| Contracts | `make contracts` + `pytest contracts/tests` |
| Test | `make test` in each changed Python service |
| DB parity (Postgres) | `ci/db-parity.sh`: alembic head == `infra/db/schema.sql`, downgrade/upgrade round trip |
| Schemathesis | `ci/schemathesis.sh 50` – review-api against `contracts/review-api.openapi.yaml` |
| Build / Trivy scan | gateway, review-api: `docker build` then `trivy image --exit-code 1 --severity HIGH,CRITICAL` |
| Scanner worker image | build + Trivy (+ push on `main`) |
| Frontend image | `npm ci`, vitest (JUnit report published), `npm run build`, docker build + Trivy (+ push on `main`) |
| Push | gateway, review-api pushed on `main` (`registry-creds`) |
| Deploy | tag bump (below), `main` only |

Images are always `<registry>/ai-scan/<svc>:<short git sha>`, never `latest`. postgres and platform have no image built here.

## 2. Tag bump in `deploy/<component>/overlays/k3s`

Overlays ship with `newTag: GIT_SHA_PLACEHOLDER`. For each changed service the Deploy stage runs, in `deploy/<svc>/overlays/${TARGET}`:

- frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<registry>/ai-scan/<svc>:<sha>`
- scanner-worker: sets `SCANNER_IMAGE=<registry>/ai-scan/scanner-worker:<sha>` in the `scanner-image` configMapGenerator (`kustomize edit set configmap`, fallback `yq`, then `sed`). review-api reads it via `configMapKeyRef` and uses it for the per-review Jobs.

Then it commits as `jenkins` with message `ci(<target>): <svcs> -> <sha> [skip ci]`.

> **Current state:** the `git push` of that commit is a commented-out **TODO stub** in the Jenkinsfile **Open item, awaiting Almog's decision:** either Jenkins commits the tag bump directly to `main`, or it opens an automatic PR. Until it is enabled, the tag bump stays on the Jenkins workspace and Argo CD (which tracks `main`) does not see it.

## 3. Argo CD sync

App-of-apps, bootstrapped once: `kubectl apply -n argocd -f deploy/argocd/root-app.yaml`.

- Root `ai-scan-root` (ns `argocd`) syncs `deploy/argocd/apps/` on `main`.
- One child Application per component, project `ai-scan`, source `deploy/<component>/overlays/k3s` on `main`, destination namespace `ai-scan-repos`, auto-sync with `prune` + `selfHeal`, `CreateNamespace=true`:

| Application | Sync wave |
|---|---|
| `ai-scan-platform-k3s` | 0 |
| `ai-scan-postgres-k3s` | 1 |
| `ai-scan-review-api-k3s` | 2 |
| `ai-scan-gateway-k3s`, `ai-scan-frontend-k3s`, `ai-scan-scanner-worker-k3s` | 3 |

When a tag-bump commit lands on `main`, Argo CD renders the overlay with Kustomize and rolls out the new image. Because `selfHeal` is on, manual `kubectl` changes are reverted – change Git instead. Rollback: [runbooks/rollback.md](runbooks/rollback.md).
