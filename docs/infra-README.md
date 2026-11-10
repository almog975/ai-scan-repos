# AI Scan Repos: infrastructure

See kustomize-layout.md, storage-plan.md, secrets-runbook.md.

## Apply order (OVERLAY=local|k3s|eks)
1. `kubectl apply -k infra/k8s/platform/overlays/$OVERLAY`. On the first run the ServiceMonitor CRD is missing, so apply after step 3 or ignore the error.
2. Create the secrets (secrets-runbook.md).
3. `infra/helm/kube-prometheus-stack/install.sh` (ns monitoring, Grafana at grafana.<domain>)
4. `kubectl apply -f infra/bootstrap/registry/registry.yaml`, then put `infra/bootstrap/registry/registries.yaml.example` on the K3s nodes
5. `kubectl apply -k infra/k8s/postgres/overlays/$OVERLAY`
6. `kubectl apply -k infra/k8s/scanner-worker/overlays/$OVERLAY` (RBAC + job template)
7. review-api, gateway, frontend overlays (images are set by Pipelines)

Installer: `infra/bootstrap/installer/aiscan-install.sh` (CLI, no web UI; see [installer.md](installer.md)). The former unauthenticated web wizard (port 8088) was removed in Phase 9.
