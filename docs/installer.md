# Installer (Phase 8)

`infra/installer/aiscan-install.sh` installs AI Scan Repos onto an existing cluster. It is non-interactive
(flags + env + an env file), idempotent (every step can be re-run), and `--dry-run` prints the full plan without
calling `kubectl`, `helm` or `aws` and without touching the working tree.

```bash
cp infra/installer/installer.env.example infra/installer/installer.env   # .gitignored; fill in REPLACE_ME
infra/installer/aiscan-install.sh --config infra/installer/installer.env --dry-run install   # review the plan
infra/installer/aiscan-install.sh --config infra/installer/installer.env install
```

Precedence: defaults < `--config` file < environment < flags. The config file accepts `KEY=VALUE` lines only
(anything else is rejected, it is never `source`d).

| Flag / env | Values | Default |
|---|---|---|
| `--overlay` / `OVERLAY` | `k3s`, `eks`, `local` | `k3s` |
| `--mode` / `MODE` | `gitops` (Argo CD app-of-apps; k3s only, because `deploy/argocd/apps` point at k3s overlays), `direct` (`kubectl apply -k`) | `gitops` |
| `--registry` / `REGISTRY_TYPE` | `local` (in-cluster registry:2 + htpasswd), `ecr` | `local` |
| `--tag` / `IMAGE_TAG` | image tag (`[A-Za-z0-9_.-]`) | `GIT_SHA_PLACEHOLDER` |
| `--dry-run` / `DRY_RUN=1` | print only | off |
| `--rotate-secrets` | replace existing Secrets (except `ai-keys-master`) | off |
| `--yes`, `--purge` | uninstall confirmation / also delete data and namespaces | off |
| `ARGOCD_CHART_VERSION` | argo/argo-cd chart | **10.11.6** (Argo CD v3.5.6, pinned) |

## Commands

`install` runs, in order: **preflight → bootstrap → registry → secrets → images → deploy → verify**
(`SKIP_VERIFY=1` to skip the last). Each is also a command on its own, plus `ecr-login` and `uninstall`.

| Step | What it does |
|---|---|
| `preflight` | Required tools for the chosen options (`kubectl kustomize openssl` + `helm` for gitops, `aws` for ECR, `htpasswd` for the local registry); `kubectl version`; `kubectl auth can-i` for namespaces, CRDs, ClusterRoles, Secrets; `aws sts get-caller-identity` for ECR |
| `bootstrap` | `infra/bootstrap/overlays/<overlay>` (namespaces, reports PV/StorageClass; `NODE_NAME` substituted on k3s, `EFS_FILESYSTEM_ID` on eks) and `infra/ingress/overlays/<overlay>` |
| `registry` | **local:** Secret `registry/registry-htpasswd` (bcrypt push + pull users), `infra/registry/registry.yaml`, `regcred` (pull user) in `ai-scan-repos` and `jenkins`, `jenkins/registry-push` (push user, Jenkins credential labels). **ecr:** creates the ECR repos if missing (`ai-scan/{review-api,gateway,frontend,scanner-worker,jenkins-agent}`, scan on push, immutable tags) and runs `ecr-login` |
| `ecr-login` | `aws ecr get-login-password` → `regcred` (dockerconfigjson) in `ai-scan-repos` and `jenkins`. ECR tokens last 12h: run it from a cron/systemd timer (or rely on the EKS node role for pulls) |
| `secrets` | `gateway-secrets` (GitHub OAuth `GITHUB_CLIENT_ID/SECRET` + webhook secret), `db-secrets`, `scanner-secrets` (`INTERNAL_API_TOKEN`, AI defaults), `ai-keys-master`, optional `monitoring/grafana-admin` (see [secrets-runbook.md](secrets-runbook.md)) |
| `images` | Sets registry + tag in `deploy/{review-api,gateway,frontend}/overlays/<overlay>` (`kustomize edit set image`) and `SCANNER_IMAGE` in `deploy/scanner-worker/overlays/<overlay>` |
| `deploy` | **gitops:** `helm upgrade --install argocd` (pinned), argocd-cm health patch, AppProjects `ai-scan` and `ai-scan-ci`, `deploy/argocd/root-app.yaml`. **direct:** `kubectl apply -k` platform → postgres → scanner-worker → review-api → gateway → frontend |
| `verify` | Root Application Healthy (gitops), rollout status of postgres/review-api/gateway/frontend, `/readyz` of review-api and gateway from inside their pods |
| `uninstall` | Needs `--yes`. Deletes the root + child Applications (gitops) or the component overlays (direct). Keeps Secrets, PVCs and namespaces (the platform Application is orphaned first so the reports PVC survives). `--purge` also deletes the platform overlay, Argo CD, the registry, ingress and namespaces `ai-scan-repos`, `registry`, `argocd`. Cluster-scoped bootstrap (PVs, StorageClasses) and `jenkins`/`monitoring` are left alone |

## Secrets

- Values come only from the environment / config file. They are written to the cluster through 0600 temp files
  (`kubectl create secret --from-env-file … --dry-run=client -o yaml | kubectl apply -f -`) and the temp dir is
  removed on exit. Output shows **names and keys only**; nothing is written into the repo.
- Idempotent: an existing Secret is kept unless `--rotate-secrets`. `GITHUB_WEBHOOK_SECRET`,
  `INTERNAL_API_TOKEN` (≥ 32 chars), `POSTGRES_PASSWORD` and `AI_KEYS_ENCRYPTION_KEY` are generated with
  `openssl rand` only when the Secret does not exist yet.
- `ai-keys-master` is **never** replaced by the installer, not even with `--rotate-secrets`: it encrypts every
  stored provider key (rotation = re-encrypt first, docs/secrets-runbook.md).
- `infra/installer/installer.env` is in `.gitignore`; the committed `installer.env.example` has placeholders only.

## Images and GitOps

`images` edits the overlays **in the working tree only**. The installer never commits or pushes. In gitops mode
Argo CD deploys what is on `main`, so open a PR with the diff the installer prints. The CI Deploy tag-bump push
stays a documented stub (docs/ci-cd.md) until it is decided.

## RBAC

The installer itself creates **no** RBAC objects. What it needs from the operator's kubeconfig:

- **Cluster admin for `install --mode gitops` and `bootstrap`**: namespaces, PV/StorageClass, and the Argo CD Helm
  chart (CRDs, its own ClusterRoles). `preflight` checks `can-i create namespaces / customresourcedefinitions /
  clusterroles` and fails early otherwise.
- **Namespace-scoped** for `secrets`, `registry` secrets, `ecr-login`, `images`/`deploy --mode direct` and `verify`:
  create/patch Secrets and apply the app manifests in `ai-scan-repos`, `registry`, `jenkins`, `monitoring`;
  `pods/exec` in `ai-scan-repos` for the smoke check. `ecr-login` can run from a ServiceAccount limited to
  Secrets `regcred` in those two namespaces.
- AWS (ECR): `ecr:DescribeRepositories`, `ecr:CreateRepository`, `ecr:GetAuthorizationToken`; pushes stay with CI.

The RBAC of the app itself (review-api Job launcher, scanner SA, Jenkins agent) is unchanged and lives in the
component manifests.

## Older scripts

`infra/installer/scripts/install-{local,ssh-k3s,eks,argocd}.sh` remain as host/cluster provisioning stubs
(K3s over SSH, Terraform EKS). After the cluster exists, use `aiscan-install.sh`.

## Tests

`ci/tests/test_installer.py`: shellcheck, the dry-run plan (no kubectl/helm/aws call, no working-tree change, no
secret value in the output), ECR naming, flag/config validation, secret idempotency against a fake kubectl,
image rewrite idempotency + `kustomize build` of the result. `bash ci/kubeconform.sh` validates every overlay.
