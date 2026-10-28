# Argo CD (ns argocd)

## Install
```bash
kubectl create namespace argocd
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update
helm upgrade --install argocd argo/argo-cd -n argocd --version 7.6.12 \
  --set configs.params."server\.insecure"=true   # TLS terminated at Traefik
# or plain manifests:
# kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl apply -f infra/argocd/project.yaml -f infra/argocd/root-app.yaml
```

## Layout
- `project.yaml` – AppProject `ai-scan-repos`.
- `root-app.yaml` – app-of-apps `ai-scan-root`, renders `deploy/argocd-app.yaml` (Pipelines-owned, 6 children
  `ai-scan-<component>-k3s` → `deploy/<component>/overlays/k3s`). Child Applications are NOT duplicated here.

## Requested alignment in `deploy/argocd-app.yaml` (Pipelines owns it)
For every child Application:
1. `spec.project: ai-scan-repos` (currently `default`).
2. `syncOptions: [CreateNamespace=false]` (currently `true`) – the `platform` app creates the namespaces.
3. `metadata.annotations: {argocd.argoproj.io/sync-wave: "<w>"}` with platform `-1`, postgres `0`,
   frontend/gateway/review-api/scanner-worker `1`.
Until (1) lands the root app still works; children just stay in project `default`.
