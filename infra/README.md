# infra/

Everything needed to run the app on Kubernetes.

- `helm/` — the `ai-scan` chart (Docker Desktop) and kube-prometheus-stack values
- `k8s/` — Kustomize bases and overlays per component
- `argocd/` — Argo CD install values, AppProjects, and Applications
- `jenkins/` — Jenkins Helm values and manifests
- `bootstrap/` — cluster-scoped objects, the installer, registry, ingress, and the DB schema
