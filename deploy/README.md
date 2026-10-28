# deploy/

Part of the standalone `almog975/ai-scan-repos` repo. Kubernetes manifests watched by
Argo CD (GitOps). See the [top-level README](../README.md) and [ADR-0001](../docs/adr/0001-repo-layout.md).

## Layout
```
deploy/<component>/base/                    # Infra-owned
deploy/<component>/overlays/{local,k3s,eks} # Infra-owned; Jenkins only rewrites image tags here
deploy/argocd-app.yaml                      # Pipelines-owned
```
Components: `frontend`, `gateway`, `review-api`, `postgres`, `scanner-worker`, `platform` (6 × 3 = 18 overlays).
Everything deploys to the single namespace `ai-scan-repos`.

| Target | Registry host |
|---|---|
| local | `registry.ai-scan.local` |
| k3s   | `registry.ai-scan.127.0.0.1.nip.io` |
| eks   | `registry.ai-scan.example.com` |

Images are always `<host>/ai-scan/<svc>:<git-sha>`, never `latest`. Overlays ship with `GIT_SHA_PLACEHOLDER`.

## Argo CD (6 Applications)
`argocd-app.yaml` defines `ai-scan-<component>-k3s` for all 6 components, each tracking
`deploy/<component>/overlays/k3s` on `main`, namespace `ai-scan-repos`, auto-sync with prune + selfHeal.
For another target, swap `k3s` in the name and path.

## Tag flow
1. A push to a service folder triggers Jenkins (via smee relay, see `docs/ci.md`).
2. Jenkins builds only the changed services, scans with Trivy (fails on HIGH/CRITICAL) and pushes
   `<host for TARGET>/ai-scan/<svc>:<sha>` (TARGET defaults to `k3s`).
3. Deploy edits `deploy/<svc>/overlays/${TARGET}/kustomization.yaml`:
   - frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<host>/ai-scan/<svc>:<sha>`
   - scanner-worker: `kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=<host>/ai-scan/scanner-worker:<sha>`;
     review-api reads `SCANNER_IMAGE` via `configMapKeyRef` and launches per-review scanner Jobs with it.
   - postgres and platform have no image to set.
4. Jenkins commits with `[skip ci]` (the loop guard also skips deploy-only changes) and pushes (TODO stub);
   Argo CD syncs the new tag.

## Build / check locally
`kustomize build deploy/<component>/overlays/<target>` (or `kubectl kustomize ...`).

## Config
No Secret YAML in git; create secrets from the CLI.

## Owners
Infra: `base/` and `overlays/` content. Pipelines: image tags, `argocd-app.yaml`, `Jenkinsfile`. See `.github/CODEOWNERS`.
