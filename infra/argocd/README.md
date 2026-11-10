# Argo CD (ns argocd)

## Install order (scripted: infra/bootstrap/installer/scripts/install-argocd.sh)
Pinned: chart `argo/argo-cd` **10.11.6** = Argo CD **v3.5.6** (>= 2.6 required for the multi-source `ai-scan-jenkins` app).
```bash
kubectl apply -k infra/bootstrap/cluster/overlays/k3s            # 0. namespaces (incl. argocd, jenkins) + PVs/SCs + jenkins-home
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update
helm upgrade --install argocd argo/argo-cd -n argocd --version 10.11.6 --set 'configs.params.server\.insecure=true'
# or manifests: kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml
kubectl -n argocd patch cm argocd-cm --type merge --patch-file infra/argocd/argocd-cm-health-patch.yaml   # 1b
# repo credentials: docs/argocd-repo-credentials.md (Almog creates them)
kubectl apply -f infra/argocd/appproject-ai-scan.yaml     # 2a  MUST be before the root app
kubectl apply -f infra/argocd/appproject-ai-scan-ci.yaml  # 2b  MUST be before the root app (Jenkins app)
kubectl apply -n argocd -f infra/argocd/root-app.yaml    # 3   Pipelines-owned
```
Both AppProjects must exist before the root app syncs, otherwise children referencing them fail.
Optional before 3: build the agent image, `infra/bootstrap/installer/build-jenkins-agent.sh` (docker or nerdctl), see infra/jenkins.

## Ownership
- Infra: `appproject-ai-scan.yaml`, `appproject-ai-scan-ci.yaml` (ns jenkins only; adopted from Pipelines), `argocd-cm-health-patch.yaml`, `infra/bootstrap/` (not Argo CD-managed).
- Pipelines: `infra/argocd/root-app.yaml` (project `default`) and `infra/argocd/apps/*.yaml`
  (project `ai-scan`, finalizer `resources-finalizer.argocd.argoproj.io`).
