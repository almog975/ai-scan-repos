# Argo CD (ns argocd)

## Install order
```bash
kubectl apply -k infra/bootstrap/overlays/k3s            # namespaces (incl. argocd) + PV/SC; done by installer
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update
helm upgrade --install argocd argo/argo-cd -n argocd --set configs.params."server\.insecure"=true
# or: kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd patch cm argocd-cm --type merge --patch-file infra/argocd/argocd-cm-health-patch.yaml
# repo credentials: docs/argocd-repo-credentials.md (Almog creates them)
kubectl apply -f infra/argocd/appproject-ai-scan.yaml    # MUST be applied BEFORE the root app
kubectl apply -n argocd -f deploy/argocd/root-app.yaml   # Pipelines-owned
```
The AppProject `ai-scan` must exist before the root app syncs, otherwise children referencing it fail.

## Ownership
- Infra: `appproject-ai-scan.yaml`, `argocd-cm-health-patch.yaml`, `infra/bootstrap/` (not Argo CD-managed).
- Pipelines: `deploy/argocd/root-app.yaml` (project `default`) and `deploy/argocd/apps/*.yaml`
  (project `ai-scan`, finalizer `resources-finalizer.argocd.argoproj.io`).
