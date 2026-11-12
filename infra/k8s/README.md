# infra/k8s/

Part of the standalone `almog975/ai-scan-repos` repo. Kubernetes manifests watched by
Argo CD (GitOps). See the [top-level README](../../README.md).

## Layout
```
infra/k8s/<component>/base/
infra/k8s/<component>/overlays/{local,k3s,eks} # Jenkins only rewrites image tags here
infra/k8s/overlays/local/                      # Lightweight compose overlay (Docker Desktop / k3d; no registry)
infra/argocd/root-app.yaml                 # app-of-apps root
infra/argocd/apps/<component>.yaml         # one child Application per component
```
Components: `frontend`, `gateway`, `review-api`, `postgres`, `scanner-worker`, `platform` (6 × 3 = 18 overlays),
plus top-level `infra/k8s/overlays/local` for a one-shot local apply. Everything deploys to the single namespace `ai-scan-repos`.

| Target | Registry host |
|---|---|
| local | `registry.ai-scan.local` |
| k3s   | `registry.registry:5000` (in-cluster Service; nodes pull via the K3s registries.yaml mirror) |
| eks   | `registry.ai-scan.example.com` |

Images are always `<host>/ai-scan/<svc>:<git-sha>`, never `latest`. Overlays ship with `GIT_SHA_PLACEHOLDER`.

## Argo CD (app-of-apps, 6 child Applications)
Bootstrap once (Argo CD installed in namespace `argocd`). First apply the AppProject `ai-scan`
from `infra/argocd/` (all child apps use `project: ai-scan` and will not sync without it), then:
```
kubectl apply -n argocd -f infra/argocd/root-app.yaml
```
The root Application `ai-scan-root` tracks `infra/argocd/apps` on `main` (auto-sync, prune + selfHeal) and
creates one child `ai-scan-<component>-k3s` per file there. Each child tracks `infra/k8s/<component>/overlays/k3s`
on `main`, namespace `ai-scan-repos`, project `ai-scan`, auto-sync with prune + selfHeal and `CreateNamespace=true`,
with `resources-finalizer.argocd.argoproj.io` (deleting a child deletes its resources). The root stays in project `default`.

Sync waves (`argocd.argoproj.io/sync-wave`): `platform` 0 → `postgres` 1 → `review-api` 2 →
`gateway`, `frontend`, `scanner-worker` 3. To add a component, add `infra/argocd/apps/<component>.yaml`.
For another target, swap `k3s` in the name and path.

## Tag flow
1. A push to a service folder triggers Jenkins (via smee relay).
2. Jenkins builds only the changed services, scans with Trivy (fails on HIGH/CRITICAL) and pushes
   `<host for TARGET>/ai-scan/<svc>:<sha>` (TARGET defaults to `k3s`).
3. Deploy edits `infra/k8s/<svc>/overlays/${TARGET}/kustomization.yaml`:
   - frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<host>/ai-scan/<svc>:<sha>`
   - scanner-worker: `kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=<host>/ai-scan/scanner-worker:<sha>`;
     review-api reads `SCANNER_IMAGE` via `configMapKeyRef` and launches per-review scanner Jobs with it.
   - postgres and platform have no image to set.
4. Jenkins commits with `[skip ci]` (the loop guard also skips deploy-only changes) and opens a PR;
   Argo CD syncs the new tag.

## Build / check locally
`kustomize build infra/k8s/<component>/overlays/<target>` (or `kubectl kustomize ...`).

## Config
No Secret YAML in git for k3s/eks (create from the CLI). `infra/k8s/overlays/local` is the exception: committed **NOT FOR PRODUCTION** placeholders via secretGenerator.

## Helm alternative (Docker Desktop)
For a simple local run without Kustomize, use the Helm chart in [`infra/helm/ai-scan`](../helm/ai-scan/README.md):
`helm install ai-scan ./infra/helm/ai-scan -n ai-scan-repos --create-namespace`, then open http://localhost
(needs ingress-nginx). Don't install it and a Kustomize overlay in the same namespace at the same time.
