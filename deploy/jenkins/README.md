# deploy/jenkins (Pipelines-owned)

Jenkins controller + agents in namespace `jenkins`, deployed by Argo CD (`deploy/argocd/apps/jenkins.yaml`,
project `ai-scan-ci`).

| Path | What |
|---|---|
| `values.yaml` | Values for Helm chart `jenkins/jenkins` 5.10.3: `fullnameOverride: jenkins` (Service `jenkins:8080`), `persistence.existingClaim: jenkins-home`, plugins, JCasC (multibranch job `ai-scan-repos`, 5 min indexing fallback, URL), agent pod template `ai-scan-agent` |
| `manifests/` | `smee-client` Deployment (forwards to `http://jenkins.jenkins.svc:8080/github-webhook/`) and agent ServiceAccount `jenkins-agent` (no API access) |
| `../../ci/agent/Dockerfile` | `tools` container image of the agent pod: node 20, python 3, trivy, kustomize, crane |

Why Helm values (not raw manifests): the upstream chart already handles the controller StatefulSet, plugin
install, JCasC sidecar reload and agent RBAC; we only keep a small values file in git, rendered by Argo CD.

Why kaniko (not DinD) on k3s: DinD needs a privileged pod and its own storage; kaniko builds in an unprivileged
user namespace-free container with no daemon. Images are built to a tarball, scanned with `trivy --input`, then
pushed with `crane` (main only), so nothing unscanned reaches the registry. Note: upstream kaniko was archived
in 2025; if the image becomes unavailable, switch to the Chainguard-maintained fork or BuildKit rootless.

## Infra-owned prerequisites (not in this folder)
Namespace `jenkins` and PVC `jenkins-home` (infra/bootstrap), IngressRoute `jenkins` + TLS secret `jenkins-tls`
(infra/ingress), AppProject `ai-scan-ci` (infra/argocd), registry htpasswd (docs/registry-auth-runbook.md).

## Secrets Almog creates (never in git)
Jenkins credentials are K8s Secrets read by the kubernetes-credentials-provider plugin (label + annotations):
```bash
# Jenkins admin login (chart uses existingSecret, no random password drift)
kubectl -n jenkins create secret generic jenkins-admin \
  --from-literal=jenkins-admin-user=admin --from-literal=jenkins-admin-password='<ADMIN_PASSWORD>'

# registry-push: Jenkins credential (username/password) used by crane in the Push stage
kubectl -n jenkins create secret generic registry-push \
  --from-literal=username='<PUSH_USER>' --from-literal=password='<PUSH_PASSWORD>'
kubectl -n jenkins label secret registry-push jenkins.io/credentials-type=usernamePassword
kubectl -n jenkins annotate secret registry-push jenkins.io/credentials-description='registry push user'

# github-bot: GitHub fine-grained PAT (contents: read/write on almog975/ai-scan-repos). Multibranch scan
# uses it now; the Deploy git push stays a TODO stub until Almog approves it.
kubectl -n jenkins create secret generic github-bot \
  --from-literal=username='<GITHUB_USER>' --from-literal=password='<GITHUB_PAT>'
kubectl -n jenkins label secret github-bot jenkins.io/credentials-type=usernamePassword

# smee-url: smee.io channel URL, read by the smee-client Deployment (key url) and exposed to Jenkins as secret text
kubectl -n jenkins create secret generic smee-url --from-literal=url='<SMEE_CHANNEL_URL>'
kubectl -n jenkins label secret smee-url jenkins.io/credentials-type=secretText
kubectl -n jenkins annotate secret smee-url jenkins.io/credentials-keybinding-text=url
```
The GitHub webhook (repo Settings > Webhooks) points at the smee channel URL, content type JSON, push events.
