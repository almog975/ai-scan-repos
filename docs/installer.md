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
| `--rotate-secrets` | replace existing Secrets (except `ai-keys-master`), see [Rotation](#rotation) | off |
| `--yes`, `--purge`, `--confirm-purge ai-scan-repos` | uninstall confirmation / also delete data and namespaces (needs the explicit `--confirm-purge`) | off |
| `ARGOCD_CHART_VERSION` | argo/argo-cd chart | **10.11.6** (Argo CD v3.5.6, pinned) |

## Commands

`install` runs, in order: **preflight → bootstrap → registry → secrets → images → egress → deploy → verify**
(`SKIP_VERIFY=1` to skip the last). Each is also a command on its own, plus `ecr-login` and `uninstall`.

| Step | What it does |
|---|---|
| `preflight` | Required tools for the chosen options (`kubectl kustomize openssl` + `helm` for gitops, `aws` for ECR, `htpasswd` for the local registry); `kubectl version`; `kubectl auth can-i` for namespaces, CRDs, ClusterRoles, Secrets; `aws sts get-caller-identity` for ECR; with `--mode direct` the prometheus-operator CRDs `servicemonitors`/`prometheusrules.monitoring.coreos.com` must exist (<a id="monitoring-crds"></a>install kube-prometheus-stack or its CRDs first, else preflight stops with a clear error) |
| `bootstrap` | `infra/bootstrap/overlays/<overlay>` (namespaces, reports PV/StorageClass; `NODE_NAME` substituted on k3s, `EFS_FILESYSTEM_ID` on eks) and `infra/ingress/overlays/<overlay>` |
| `registry` | **local:** Secret `registry/registry-htpasswd` (bcrypt push + pull users; passwords go to `htpasswd -iBn` on stdin, never argv), `infra/registry/registry.yaml`, `regcred` (pull user) in `ai-scan-repos` and `jenkins`, `jenkins/registry-push` (push user, Jenkins credential labels). **ecr:** creates the ECR repos if missing (`ai-scan/{review-api,gateway,frontend,scanner-worker,jenkins-agent}`, scan on push, immutable tags) and runs `ecr-login` |
| `ecr-login` | `aws ecr get-login-password` → `regcred` (dockerconfigjson) in `ai-scan-repos` and `jenkins`. ECR tokens last 12h: run it from a cron/systemd timer (or rely on the EKS node role for pulls) |
| `secrets` | `gateway-secrets` (GitHub OAuth `GITHUB_CLIENT_ID/SECRET` + webhook secret), `db-secrets`, `scanner-secrets` (`INTERNAL_API_TOKEN`, AI defaults), `ai-keys-master`, optional `monitoring/grafana-admin` (see [secrets-runbook.md](secrets-runbook.md)) |
| `images` | Sets registry + tag in `deploy/{review-api,gateway,frontend}/overlays/<overlay>` (targeted rewrite of the existing `images:` entry, `newName`/`newTag` only: minimal stable diff) and `SCANNER_IMAGE` in `deploy/scanner-worker/overlays/<overlay>` |
| `egress` | Replaces the NetworkPolicy placeholders in `deploy/platform/overlays/<overlay>/kustomization.yaml` (review-api AI + K8s API egress) and `deploy/scanner-worker/overlays/<overlay>/networkpolicy-ai-egress.yaml` from `AI_EGRESS_CIDRS` (comma list, IPv4 `/8`–`/32`, anything broader is refused), `KUBE_API_ENDPOINT` and `KUBE_SERVICE_IP` (IPv4, written as `/32`). Only lines tagged `(installer: VAR)` change; working tree only (open a PR in gitops mode). An unset variable keeps the placeholder with a warning: that traffic stays blocked. See [Egress](#egress) |
| `deploy` | **gitops:** `helm upgrade --install argocd` (pinned), argocd-cm health patch, AppProjects `ai-scan` and `ai-scan-ci`, `deploy/argocd/root-app.yaml`. **direct:** `kubectl apply -k` platform → postgres → scanner-worker → review-api → gateway → frontend |
| `verify` | Root Application Healthy (gitops), rollout status of postgres/review-api/gateway/frontend, `/readyz` of review-api and gateway from inside their pods |
| `uninstall` | Needs `--yes`. **Without `--purge` no data is deleted:** PVCs `postgres-data`, `reports`, `postgres-backups`, all Secrets and the namespaces stay. gitops: the `resources-finalizer` is removed from every Application first, so deleting the Applications orphans their resources (no cascade); the PVCs also carry `argocd.argoproj.io/sync-options: Delete=false,Prune=false`. Then the workloads of postgres, scanner-worker, review-api, gateway, frontend are deleted from their rendered overlays **with PersistentVolumeClaims/PersistentVolumes filtered out** (never `kubectl delete -k` on an overlay with a PVC); platform (ServiceAccounts, reports PVC) is kept. A reinstall reuses the data. `--purge` (needs `--yes --purge --confirm-purge ai-scan-repos`) deletes the app and its data: all overlays incl. platform and the PVCs `postgres-data`, `reports`, `postgres-backups`, the registry with its `registry-data` PVC, Argo CD, the registry, ingress and namespaces `ai-scan-repos`, `registry`, `argocd`. It is **not** everything: namespaces `jenkins` (incl. the `jenkins-home` PVC) and `monitoring`, and cluster-scoped bootstrap (PVs, StorageClasses; reports PV is Retain) are left alone |

## Secrets

- Values come only from the environment / config file. They are written to the cluster through 0600 temp files
  (`kubectl create secret --from-env-file … --dry-run=client -o yaml | kubectl apply -f -`) and the temp dir is
  removed on exit. Output shows **names and keys only**; nothing is written into the repo.
- Idempotent: an existing Secret is kept unless `--rotate-secrets`. `GITHUB_WEBHOOK_SECRET`,
  `INTERNAL_API_TOKEN` (≥ 32 chars), `POSTGRES_PASSWORD` and `AI_KEYS_ENCRYPTION_KEY` are generated with
  `openssl rand` only when that Secret is (re)written. A generator failure aborts, and every required value is
  checked non-empty before any Secret is created.
- Existence checks: only the API's `NotFound` for that exact object (`secrets "<name>" not found`) counts as "absent"; a missing kube context or namespace does not. Any other error (RBAC, API
  unreachable, timeout) aborts the run instead of overwriting a Secret.
- Every failing command prints `ERROR: step "<step>" failed …` naming the step.
- `ai-keys-master` is **never** replaced by the installer, not even with `--rotate-secrets`: it encrypts every
  stored provider key (rotation = re-encrypt first, docs/secrets-runbook.md).
- `infra/installer/installer.env` is in `.gitignore`; the committed `installer.env.example` has placeholders only.

## Rotation

`--rotate-secrets secrets` (or `install`):

- `POSTGRES_PASSWORD`: `ALTER USER` runs first in the running database (`kubectl exec -i statefulset/postgres --
  psql`, SQL on stdin), and only then is `db-secrets` updated. If it fails, the Secret is left unchanged.
- `GITHUB_WEBHOOK_SECRET` (when not given explicitly): a **warning** is printed; update the GitHub App's webhook
  secret right away, deliveries fail until then.
- `INTERNAL_API_TOKEN`: new value in `scanner-secrets`.
- Afterwards `review-api` and `gateway` get a `kubectl rollout restart` (when they exist); scan Jobs pick up the
  new values on their next run.
- `ai-keys-master` is never rotated.

## Egress

| Variable | Used for | Find it with |
|---|---|---|
| `AI_EGRESS_CIDRS` | review-api provider tests and scanner Jobs → AI provider (TCP 443) | the provider's IPs / Tailscale subnet (docs/onprem-ai-tailscale.md); hostnames are not possible in NetworkPolicy |
| `KUBE_API_ENDPOINT` | review-api → API server (Job launcher) | `kubectl get endpoints kubernetes -n default -o jsonpath='{.subsets[0].addresses[0].ip}'` |
| `KUBE_SERVICE_IP` | review-api → `kubernetes.default` ClusterIP | `kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}'` (k3s `10.43.0.1`) |

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
