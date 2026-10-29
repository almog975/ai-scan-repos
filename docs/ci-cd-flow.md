# CI/CD flow: Jenkins → registry → Git tag bump → Argo CD

How a commit becomes a running deployment on k3s. Source of truth: the root [`Jenkinsfile`](../Jenkinsfile) and [`deploy/argocd/`](../deploy/argocd/). Pipeline details, agent and webhook relay: [ci.md](ci.md) (owned by Pipelines); make targets: [ci-cd.md](ci-cd.md). Decision record: [ADR-0004](adr/0004-gitops.md).

```mermaid
flowchart LR
  dev[git push] -->|webhook via smee-client<br/>or pollSCM H/5| G
  subgraph J[Jenkins – k8s agent pod ai-scan-agent]
    G[Loop guard<br/>skip: bot author, '[skip ci]', deploy/-only] --> T[Test<br/>contracts, ci/tests, vitest,<br/>pytest, schemathesis, db-parity]
    T --> B[Build<br/>kaniko → image tarball]
    B --> S[Scan<br/>trivy --input, fail on HIGH/CRITICAL]
    S --> P[Push – main only<br/>crane push]
    P --> K[Deploy – main only<br/>tag bump in deploy/&lt;svc&gt;/overlays/k3s<br/>commit '[skip ci]']
  end
  P --> REG[(Registry)]
  K -.->|git push: OPEN ITEM<br/>stub| GH[(GitHub main)]
  GH -->|poll| A[Argo CD ai-scan-root]
  A --> Apps[ai-scan-&lt;component&gt;-k3s<br/>ns ai-scan-repos]
  Apps -->|pull image| REG
```

## 1. Jenkins: test, build, scan, push

Runs on the Kubernetes agent pod template `ai-scan-agent` (containers `tools` and `kaniko`; no Docker daemon). Triggers: `githubPush()` (GitHub webhook via the smee-client Deployment) and `pollSCM('H/5 * * * *')` as fallback. Parameter `TARGET` = `k3s` (default) | `local` | `eks`; registry host per target (k3s: `registry.registry:5000`, the in-cluster Service over plain HTTP; `crane push --insecure`, kaniko builds with `--no-push`).

| Stage | What it does |
|---|---|
| Loop guard | Skips (NOT_BUILT) if the last commit is by the CI bot (`ci-bot@ai-scan.local`), has `[skip ci]`, or only touches `deploy/`. Picks the changed components (all of them if `contracts/`, `mk/`, `Makefile` changed or the diff base is missing) |
| Test | CI venvs → **Contracts** (`make contracts`, `contracts/tests`, `ci/tests`) → per changed component: frontend `npm ci` + vitest + build; gateway `make test`; review-api `make test` + `ci/schemathesis.sh 50` + `ci/db-parity.sh`; scanner-worker `make test`. JUnit reports published |
| Build | kaniko builds each changed component to a tarball (no push) |
| Scan | `trivy image --input <tar> --exit-code 1 --severity CRITICAL,HIGH` |
| Push | `main` only: `crane push` to `<registry>/ai-scan/<svc>:<short sha>` with credential `registry-push` |
| Deploy | `main` only: tag bump (below) |

Images are never `latest`. postgres and platform have no image built here.

## 2. Tag bump in `deploy/<component>/overlays/k3s`

Overlays ship with `newTag: GIT_SHA_PLACEHOLDER`. For each changed component, in `deploy/<svc>/overlays/${TARGET}`:

- frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<registry>/ai-scan/<svc>:<sha>`
- scanner-worker: `kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=<registry>/ai-scan/scanner-worker:<sha>`. review-api reads it via `configMapKeyRef` and uses it for each review's Job.

Then it commits as `ai-scan-ci-bot` with `ci(<target>): <svcs> -> <sha> [skip ci]`.

> **Open item, awaiting Almog's decision:** the `git push` of that commit is a commented-out stub (`TODO(push)`, credential `github-bot` not created). The choice is between Jenkins committing the tag bump directly to `main` and opening an automatic PR. Until then the bump stays in the Jenkins workspace, and tags in `deploy/` are bumped manually through a PR (the installer's `images` step prints the diff).

## 3. Argo CD sync

App-of-apps: `kubectl apply -n argocd -f deploy/argocd/root-app.yaml` (the installer does this in `gitops` mode, with Argo CD chart 10.11.6 and AppProjects `ai-scan` / `ai-scan-ci`).

- Root `ai-scan-root` (ns `argocd`) syncs `deploy/argocd/apps/` on `main`.
- One child Application per component, project `ai-scan`, source `deploy/<component>/overlays/k3s` on `main`, destination namespace `ai-scan-repos`, auto-sync with `prune` + `selfHeal`, `CreateNamespace=true`:

| Application | Sync wave |
|---|---|
| `ai-scan-platform-k3s` | 0 |
| `ai-scan-postgres-k3s` | 1 |
| `ai-scan-review-api-k3s` | 2 |
| `ai-scan-gateway-k3s`, `ai-scan-frontend-k3s`, `ai-scan-scanner-worker-k3s` | 3 |

When a tag-bump commit lands on `main`, Argo CD renders the overlay and rolls out the new image. `selfHeal` reverts manual `kubectl` changes – change Git instead. Rollback: [runbooks/rollback.md](runbooks/rollback.md).

## Troubleshooting

- No build after a push: Jenkins also polls (`pollSCM H/5`); otherwise use **Build Now**.
- GitHub **Redeliver** to the gateway auto-review webhook returns 409 within 24 h of the original (replay protection; after that, an old push is rejected as stale; a failed delivery, answered 502, is not recorded and can be redelivered). See [runbook.md](runbooks/runbook.md#github-webhooks-redeliver-returns-409).
