# infra/jenkins (Pipelines-owned)

Jenkins controller + agents in namespace `jenkins`, deployed by Argo CD (`infra/argocd/apps/jenkins.yaml`,
project `ai-scan-ci`).

| Path | What |
|---|---|
| `values.yaml` | Values for Helm chart `jenkins/jenkins` 5.10.3: `fullnameOverride: jenkins` (Service `jenkins:8080`), `persistence.existingClaim: jenkins-home`, plugins, JCasC (multibranch job `ai-scan-repos`, 5 min indexing fallback, URL), agent pod template `ai-scan-agent` |
| `manifests/` | `smee-client` Deployment (forwards to `http://jenkins.jenkins.svc:8080/github-webhook/`) and agent ServiceAccount `jenkins-agent` (no API access) |
| `../../ci/agent/Dockerfile` | `tools` container image of the agent pod: node 22, python 3, trivy, kustomize, crane |

Why Helm values (not raw manifests): the upstream chart already handles the controller StatefulSet, plugin
install, JCasC sidecar reload and agent RBAC; we only keep a small values file in git, rendered by Argo CD.

Why kaniko (not DinD) on k3s: DinD needs a privileged pod and its own storage; kaniko builds in an unprivileged
user namespace-free container with no daemon. Images are built to a tarball, scanned with `trivy --input`, then
pushed with `crane` (main only), so nothing unscanned reaches the registry. Note: upstream kaniko was archived
in 2025; if the image becomes unavailable, switch to the Chainguard-maintained fork or BuildKit rootless.

## Infra-owned prerequisites (not in this folder)
Namespace `jenkins` and PVC `jenkins-home` (infra/bootstrap), IngressRoute `jenkins` + TLS secret `jenkins-tls`
(infra/bootstrap/ingress), AppProject `ai-scan-ci` (infra/argocd), registry htpasswd (docs/registry-auth-runbook.md).

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

# github-bot: fine-grained PAT (see "github-bot PAT" below). Used by multibranch scanning and by the Deploy
# stage (ci/deploy/open-pr.sh) to push deploy/<c>-<sha> and open the PR to main.
kubectl -n jenkins create secret generic github-bot \
  --from-literal=username='<GITHUB_USER>' --from-literal=password='<GITHUB_PAT>'
kubectl -n jenkins label secret github-bot jenkins.io/credentials-type=usernamePassword
kubectl -n jenkins annotate secret github-bot jenkins.io/credentials-description='GitHub fine-grained PAT (deploy PRs)'

# smee-url: smee.io channel URL, read by the smee-client Deployment (key url) and exposed to Jenkins as secret text
kubectl -n jenkins create secret generic smee-url --from-literal=url='<SMEE_CHANNEL_URL>'
kubectl -n jenkins label secret smee-url jenkins.io/credentials-type=secretText
kubectl -n jenkins annotate secret smee-url jenkins.io/credentials-keybinding-text=url
```
The GitHub webhook (repo Settings > Webhooks) points at the smee channel URL, content type JSON, push events.

## github-bot PAT (Deploy PR flow)

Created by Almog (repo owner); Jenkins never sees more than this one repo.

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → *Generate new token*.
2. Name `ai-scan-jenkins-deploy`, **Expiration 90 days** (put the renewal date in the calendar; max allowed 1 year).
   Resource owner `almog975`.
3. **Repository access: Only select repositories → `almog975/ai-scan-repos`.**
4. Repository permissions:
   - **Contents: Read and write** (push `deploy/*` branches)
   - **Pull requests: Read and write** (open the PR, look up an existing one on 422)
   - **Metadata: Read-only** (added automatically, required)
   - everything else: *No access*. (Commit statuses/Webhooks are not needed; multibranch scanning works with
     Contents + Metadata + Pull requests.)
5. Store it as the Jenkins credential (placeholders only, run from your shell, never commit the value):
   ```bash
   kubectl -n jenkins create secret generic github-bot \
     --from-literal=username='<GITHUB_USER>' --from-literal=password='<GITHUB_PAT>'
   kubectl -n jenkins label secret github-bot jenkins.io/credentials-type=usernamePassword
   ```
   Rotation: create the new token, `kubectl -n jenkins create secret generic github-bot ... --dry-run=client -o yaml
   | kubectl apply -f -`, then delete the old token on GitHub.
6. Branch protection on `main` (recommended): require a PR; Almog merges. The bot cannot push to `main` because the
   flow only ever pushes `deploy/*` (enforced in ci/deploy/open-pr.sh).

Token handling in the pipeline: `withCredentials(usernamePassword 'github-bot')` masks it in the log; the script
runs with `set +x`, gives it to git through a temporary `GIT_ASKPASS` script (not in the URL, argv or
`.git/config`) and to curl through a `-H @file` header file. Proven by `ci/tests/test_deploy_pr.py`.

Loop guard for the PR flow: `deploy/*` and `rollback/*` are excluded from branch indexing (`headWildcardFilter` in
values.yaml); PR-N jobs whose source branch is `deploy/*` are skipped by the Jenkinsfile (`CHANGE_BRANCH`); the
commit Almog's merge puts on `main` is skipped because it only touches `infra/k8s/**` (and carries `[skip ci]` from
the PR title for merge/squash).
