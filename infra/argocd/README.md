# Argo CD (ns argocd)

## Install order (scripted: infra/bootstrap/installer/scripts/install-argocd.sh)
Pinned: chart `argo/argo-cd` **10.11.6** = Argo CD **v3.5.6** (>= 2.6 required for the multi-source `ai-scan-jenkins` app).
```bash
kubectl apply -k infra/bootstrap/cluster/overlays/k3s            # 0. namespaces (incl. argocd, jenkins) + PVs/SCs + jenkins-home
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update
helm upgrade --install argocd argo/argo-cd -n argocd --version 10.11.6 --set 'configs.params.server\.insecure=true'
# or manifests: kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml
kubectl -n argocd patch cm argocd-cm --type merge --patch-file infra/argocd/argocd-cm-health-patch.yaml   # 1b
# repo credentials only if the repo is private
kubectl apply -f infra/argocd/appproject-ai-scan.yaml     # 2a  MUST be before the root app
kubectl apply -f infra/argocd/appproject-ai-scan-ci.yaml  # 2b  MUST be before the root app (Jenkins app)
kubectl apply -n argocd -f infra/argocd/root-app.yaml    # 3
```
Both AppProjects must exist before the root app syncs, otherwise children referencing them fail.
Optional before 3: build the agent image, `infra/bootstrap/installer/build-jenkins-agent.sh` (docker or nerdctl), see infra/jenkins.

`root-app.yaml` uses project `default`; the child apps in `apps/` use project `ai-scan`.

The Docker Desktop Application for the Helm chart is `docker-desktop/ai-scan.yaml`.
