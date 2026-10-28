# AI Scan Repos: infrastructure

See kustomize-layout.md, storage-plan.md, secrets-runbook.md.

## Apply order (OVERLAY=local|k3s|eks)
1. `kubectl apply -k deploy/platform/overlays/$OVERLAY`. On the first run the ServiceMonitor CRD is missing, so apply after step 3 or ignore the error.
2. Create the secrets (secrets-runbook.md).
3. `infra/helm/kube-prometheus-stack/install.sh` (ns monitoring, Grafana at grafana.<domain>)
4. `kubectl apply -f infra/registry/registry.yaml`, then put `infra/registry/registries.yaml.example` on the K3s nodes
5. `kubectl apply -k deploy/postgres/overlays/$OVERLAY`
6. `kubectl apply -k deploy/scanner-worker/overlays/$OVERLAY` (RBAC + job template)
7. review-api, gateway, frontend overlays (images are set by Pipelines)

Installer: `docker build -t ai-scan-installer infra/installer && docker run -p 8088:8088 ai-scan-installer`, then open http://localhost:8088.
