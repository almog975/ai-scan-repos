# Local full stack on k3d (Docker Desktop)

## Lightweight overlay (`deploy/overlays/local`)

One `kubectl apply -k` for Docker Desktop Kubernetes **or** k3d. No in-cluster registry, no
manual Secret creation, and **no NetworkPolicies** (the strict default-deny + allows stay in
`deploy/platform/base` and the k3s/eks overlays). Secrets are committed **dev-only placeholders**
marked `ai-scan.local/not-for-production` / `NOT FOR PRODUCTION` — never reuse them outside a
personal laptop cluster.

`deploy/*/overlays/local` + `make local-k8s` (below) are unchanged: they still use the k3d
registry (`:dev`, `imagePullPolicy: Always`) and Argo CD.

### Quickstart (PowerShell or bash)

```powershell
docker build -t ai-scan-repos-review-api:latest -f review-api/Dockerfile .
docker build -t ai-scan-repos-gateway:latest gateway
docker build -t ai-scan-repos-frontend:latest frontend
docker build -t ai-scan-repos-scanner-worker:latest -f scanner-worker/Dockerfile .
# k3d only — Docker Desktop Kubernetes already sees local images:
# k3d image import ai-scan-repos-review-api:latest ai-scan-repos-gateway:latest ai-scan-repos-frontend:latest ai-scan-repos-scanner-worker:latest -c <cluster>
kubectl apply -k deploy/overlays/local
kubectl get pods -n ai-scan-repos
kubectl -n ai-scan-repos port-forward svc/frontend 8080:8080
```

Open http://localhost:8080. Teardown:

```powershell
kubectl delete -k deploy/overlays/local
```

Migrate Job logs (and the review-api `migrate` initContainer also runs `alembic upgrade head`):

```powershell
kubectl -n ai-scan-repos logs job/review-api-migrate
kubectl -n ai-scan-repos logs deploy/review-api -c migrate
```

Re-apply note: Jobs are immutable. Either wait for `ttlSecondsAfterFinished` (120s in this
overlay) or `kubectl -n ai-scan-repos delete job review-api-migrate --ignore-not-found` before
a second apply. The Deployment initContainer keeps the schema current either way.

OAuth (real GitHub login): never edit the tracked `secrets/gateway-secrets.env` (placeholders only). Register the
GitHub OAuth App callback `http://localhost:8080/auth/github/callback`, then (PowerShell or bash, from the repo root):

```powershell
Copy-Item deploy/overlays/local/secrets/gateway-oauth.local.env.example deploy/overlays/local/secrets/gateway-oauth.local.env
notepad deploy/overlays/local/secrets/gateway-oauth.local.env   # set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET
kubectl -n ai-scan-repos create secret generic gateway-oauth --from-env-file=deploy/overlays/local/secrets/gateway-oauth.local.env --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ai-scan-repos rollout restart deploy/gateway
```

`gateway-oauth.local.env` is gitignored; the gateway reads Secret `gateway-oauth` as an optional `envFrom` after
`gateway-secrets`, so `kubectl apply -k deploy/overlays/local` works without it (login then just fails at GitHub).

---

One command brings up postgres, review-api (+ migrations), scanner Jobs, gateway, frontend and Argo CD on a local
[k3d](https://k3d.io) cluster (K3s in Docker). K3s ships Traefik and the `local-path` StorageClass, so the
`deploy/*/overlays/local` overlays behave like the K3s ones. Docker Desktop's built-in Kubernetes is **not** used.

```bash
cp .env.example .env          # same file as docker compose; see "The .env" below
make local-k8s                # = infra/local/k3d.sh up      (ARGS=--no-argocd to skip Argo CD)
# UI: https://ai-scan.localhost  (self-signed certificate: accept the browser warning)
make local-k8s-status         # pods, jobs, URLs
make local-k8s-down           # deletes cluster + registry (asks; ARGS=--yes for non-interactive)
```

`infra/local/k3d.sh --dry-run up` prints every command without touching anything.

## Prerequisites

| Tool | Linux / macOS | Windows |
|---|---|---|
| Docker Desktop (or Docker Engine on Linux) | docker.com | Docker Desktop with the **WSL2 backend**, and *Settings > Resources > WSL integration* enabled for your distro |
| bash | built in | run everything **inside WSL2** (Ubuntu): `wsl --install -d Ubuntu`, clone the repo in the WSL filesystem (`~/`), not `/mnt/c` |
| k3d >= 5.6 | `curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh \| bash` | same, inside WSL |
| kubectl | package manager / `curl -LO https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl` | same, inside WSL |
| kustomize | `curl -s https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh \| bash` | same, inside WSL |
| htpasswd | `apt install apache2-utils` (macOS: built in) | `sudo apt install apache2-utils` |
| helm, openssl | `apt install openssl`, helm.sh install script | same, inside WSL |

Give Docker Desktop at least 4 CPUs / 8 GB RAM. Ports 80 and 443 on the host must be free.

## What `up` does

1. Creates the k3d registry `k3d-ai-scan-registry.localhost:5111` and cluster `ai-scan` (host 80/443 -> Traefik).
   The script uses the cluster's own kubeconfig, never your current kubectl context.
2. Builds the 4 images and pushes them as `localhost:5111/ai-scan/<svc>:dev`; the cluster pulls
   `k3d-ai-scan-registry.localhost:5111/ai-scan/<svc>:dev` (the local overlays point there, `imagePullPolicy: Always`).
3. Installs the Prometheus Operator **CRDs only** (ServiceMonitor, PrometheusRule; no Prometheus) so the platform
   overlay applies unchanged. Monitoring itself is not installed locally.
4. Creates `ai-scan-repos/ai-scan-tls`, a self-signed certificate for `ai-scan.localhost` (kept on re-runs).
5. Runs `infra/installer/aiscan-install.sh --overlay local --mode direct` (steps preflight, bootstrap, secrets,
   images, egress, deploy, verify) on a **temporary copy** of `deploy/` and `infra/`, so your working tree is never
   edited. The egress step pins `KUBE_API_ENDPOINT` (from `kubectl get endpoints kubernetes`), `KUBE_SERVICE_IP`,
   `POD_CIDR=10.42.0.0/16` and `SERVICE_CIDR=10.43.0.0/16` (k3d defaults). The migrations Job is re-run every time.
6. Installs Argo CD (helm chart pinned in `aiscan-install.sh`) and `deploy/argocd/local/root-app.yaml`, whose
   children sync `deploy/*/overlays/local` from GitHub **main**.
7. Prints the URLs. Secrets are never printed.

Re-run `make local-k8s` after code changes: it rebuilds, re-pushes and restarts the deployments.

## The .env

The script reads the repo `.env` (gitignored; `--env FILE` for another). Used keys:

| Key | Note |
|---|---|
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | required |
| `INTERNAL_API_TOKEN` | **>= 32 chars** (production mode in K8s). The `.env.example` value is too short: `openssl rand -hex 32`. Empty = generated |
| `AI_KEYS_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `GITHUB_WEBHOOK_SECRET` | empty = generated |
| `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_API_KEY` | scanner defaults (`scanner-secrets`) |
| `AI_EGRESS_CIDRS` | optional: AI endpoint IPs for the NetworkPolicies, otherwise AI egress is blocked |

Secrets that already exist in the cluster are **kept** (the installer's rule); to change one, delete it
(`kubectl -n ai-scan-repos delete secret gateway-secrets`) and re-run, or `make local-k8s-down` and start over.
`GITHUB_REPO_TOKEN` (environment only, never in a file) is a read-only GitHub token for Argo CD:
`GITHUB_REPO_TOKEN=... make local-k8s`.

## GitHub OAuth callback

The gateway sends `redirect_uri = PUBLIC_URL + /auth/github/callback` (override: `GITHUB_CALLBACK_URL`). It must be
registered in the GitHub OAuth App exactly:

| How you run it | PUBLIC_URL | Callback URL to register |
|---|---|---|
| `docker compose` / `kubectl apply -k deploy/overlays/local` + port-forward svc/frontend 8080 | `http://localhost:8080` | `http://localhost:8080/auth/github/callback` |
| `make local-k8s` (k3d + Traefik) | `https://ai-scan.localhost` | `https://ai-scan.localhost/auth/github/callback` |
| k3s overlay | `https://ai-scan.127.0.0.1.nip.io` | `https://ai-scan.127.0.0.1.nip.io/auth/github/callback` |
| eks overlay | `https://ai-scan.example.com` (your host) | `https://<your host>/auth/github/callback` |

Scope: login asks only `read:user` (`GITHUB_OAUTH_SCOPES`). The user token is used once for `GET /user` and
discarded; review-api clones with its own `GITHUB_TOKEN`. The old `repo` scope triggered org "access restricted" notices.
GitHub errors on the callback (`?error=redirect_uri_mismatch|access_denied|...`) are shown as a 400 with the reason.

### k3d (`make local-k8s`) OAuth App

Create a separate GitHub OAuth App for this cluster (the compose one uses `http://localhost:8080`):

- Homepage URL: `https://ai-scan.localhost`
- Authorization callback URL: **`https://ai-scan.localhost/auth/github/callback`** (add it to the same OAuth App
  next to `http://localhost:8080/auth/github/callback`, or GitHub says "The redirect_uri is not associated with this application")

`*.localhost` resolves to 127.0.0.1 in Chrome, Edge and Firefox without editing the hosts file. If your browser or
`curl` does not resolve it, add `127.0.0.1 ai-scan.localhost` to `/etc/hosts` (Windows:
`C:\Windows\System32\drivers\etc\hosts`).

## Argo CD

```bash
kubectl -n argocd port-forward svc/argocd-server 8081:80        # http://localhost:8081, user admin
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

Ingress hosts `argocd.ai-scan.localhost` / `jenkins.ai-scan.localhost` also exist (Traefik default certificate).
(`k3d kubeconfig merge ai-scan --kubeconfig-switch-context` points your kubectl at the cluster.) Argo CD deploys
GitHub `main`, not your working tree: local overlay edits reach it only after they are merged. The script's direct
apply and Argo CD manage the same objects; Argo CD wins after its next sync, **except the egress endpoints**: the
NetworkPolicies `review-api-ai-egress`, `review-api-kube-api-egress` (platform app) and `scanner-egress`
(scanner-worker app) get cluster-specific `spec.egress` from the installer (`AI_EGRESS_CIDRS`, `KUBE_API_ENDPOINT`).
The local apps list them under `ignoreDifferences` (`/spec/egress`) with `RespectIgnoreDifferences=true`, so
self-heal neither reports them OutOfSync nor reverts them to the committed placeholders. To change them, re-run
`make local-k8s` (with a new `AI_EGRESS_CIDRS`), not a git commit. The k3s apps in `deploy/argocd/apps/`
are not used locally.

## Troubleshooting

- `docker is not running`: start Docker Desktop; on Windows enable WSL integration for your distro.
- Ports 80/443 busy: stop the other service (IIS, another k3d/kind cluster, `docker compose` frontend is on 8080 and fine).
- `INTERNAL_API_TOKEN must be >= 32 chars`: set a longer one in `.env`.
- `ImagePullBackOff`: `docker push localhost:5111/...` failed or the registry is missing: `k3d registry list`.
- review-api `ContainerCreating`: `kubectl -n ai-scan-repos describe pod -l app=review-api`; ConfigMaps
  `scanner-job-template`/`scanner-image` come from the scanner-worker overlay.
- Scan Jobs cannot be created: the `review-api-kube-api-egress` NetworkPolicy (K3s enforces NetworkPolicies) needs
  the API endpoint; `make local-k8s` pins it. Without the script the committed default is the Docker range 172.16.0.0/12.
- `ModuleNotFoundError: psycopg2`: the apps normalize `postgresql://` to `postgresql+psycopg://`; rebuild images.
- Scanner Jobs pull `:dev` with `Always` (review-api env `SCANNER_IMAGE_PULL_POLICY`, local overlay only), so a
  rebuilt scanner-worker is used by the next scan.
- Login redirects to the wrong host: the gateway's `PUBLIC_URL` is `https://ai-scan.localhost`
  (`deploy/gateway/overlays/local`); it must match the OAuth App.
