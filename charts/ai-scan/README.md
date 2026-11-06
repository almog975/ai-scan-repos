# ai-scan Helm chart (Docker Desktop)

Basic Helm chart for AI Scan Repos: **frontend, gateway, review-api, postgres**, plus the
scanner Job template review-api uses. It replaces `kubectl apply -k deploy/overlays/local-registry`
for local runs. `deploy/` (Kustomize, used by Argo CD / k3s / EKS) is unchanged.

> Placeholder secrets in `values.yaml` are **NOT FOR PRODUCTION**.

## Run on Docker Desktop (Windows / WSL)

Prereqs: Docker Desktop with Kubernetes enabled, `kubectl`, `helm`.

```powershell
kubectl config use-context docker-desktop

# 1. Ingress controller (once). Docker Desktop exposes its LoadBalancer on localhost:80
helm upgrade --install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx -n ingress-nginx --create-namespace --wait
# The admission webhook must be serving before any Ingress is created,
# otherwise helm install ai-scan fails with "failed calling webhook validate.nginx.ingress.kubernetes.io".
kubectl -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=180s

# 2. If the old Kustomize stack is installed, remove it first (names would clash)
kubectl delete ns ai-scan-repos --ignore-not-found

# 3. Install
helm install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace
kubectl -n ai-scan-repos get pods -w      # postgres, review-api, gateway, frontend -> Running/Ready

# 4. Open
start http://localhost                    # or open it in the browser
curl.exe http://localhost/nginx-health
```

### Access options

**A. LoadBalancer on port 80 (default, recommended): http://localhost**

ingress-nginx's Service is `type: LoadBalancer`; Docker Desktop binds it to `localhost:80/443`.
Check it with:
```powershell
kubectl -n ingress-nginx get svc ingress-nginx-controller   # EXTERNAL-IP should be "localhost"
```
Port 80 (and 443) must be free on Windows. If EXTERNAL-IP stays `<pending>`, something else holds the port:
```powershell
netstat -ano | findstr ":80 "          # find the PID
tasklist /fi "pid eq <PID>"            # IIS -> net stop w3svc ; PID 4 (http.sys) -> net stop http
docker ps                              # stop old k3d/Traefik containers publishing :80
```
Then recreate the Service (`kubectl -n ingress-nginx delete svc ingress-nginx-controller` and re-run the
ingress-nginx `helm upgrade --install`) or restart Docker Desktop.

**B. NodePort 30080 (fallback when port 80 can't be freed): http://localhost:30080**

Docker Desktop exposes NodePorts on `localhost`, and the Ingress host `localhost` still matches.
```powershell
helm upgrade --install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx -n ingress-nginx --create-namespace `
  --set controller.service.type=NodePort --set controller.service.nodePorts.http=30080 --wait
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace `
  --set gateway.publicUrl=http://localhost:30080
curl.exe http://localhost:30080/nginx-health
```
`gateway.publicUrl` makes the GitHub callback `http://localhost:30080/auth/github/callback`;
add that URL to the OAuth App. To go back to A, re-run the ingress-nginx install with
`--set controller.service.type=LoadBalancer` and drop `gateway.publicUrl`.

**C. No ingress at all:** see "Without ingress (port-forward)" below (http://localhost:8080).

### GitHub login
Add this **Authorization callback URL** to your GitHub OAuth App:
`http://localhost/auth/github/callback` (NodePort: `http://localhost:30080/auth/github/callback`; without ingress: `http://localhost:8080/auth/github/callback`).

Real credentials are never committed. Either:
- copy `values.local.yaml.example` to `values.local.yaml` (gitignored), fill it, and add
  `-f charts/ai-scan/values.local.yaml` to `helm install/upgrade`; or
- create the optional Secret the gateway already reads:
  `kubectl -n ai-scan-repos create secret generic gateway-oauth --from-env-file=deploy/overlays/local/secrets/gateway-oauth.local.env`
  then `kubectl -n ai-scan-repos rollout restart deploy/gateway`.

### Without ingress (port-forward)
```powershell
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace --set ingress.enabled=false
kubectl -n ai-scan-repos port-forward svc/frontend 8080:8080   # http://localhost:8080
```

### Locally built images
`docker build` images named `ai-scan-repos-*:latest` (Docker Desktop shares the image store):
```powershell
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace --set global.imageRegistry=""
```

### Upgrade / teardown
```powershell
git pull
helm upgrade ai-scan ./charts/ai-scan -n ai-scan-repos
kubectl -n ai-scan-repos rollout restart deploy   # re-pull :latest if you changed images
helm list -n ai-scan-repos ; helm history ai-scan -n ai-scan-repos
helm rollback ai-scan 1 -n ai-scan-repos

helm uninstall ai-scan -n ai-scan-repos
kubectl delete ns ai-scan-repos          # also deletes the kept PVCs (postgres-data, reports)
```

## Design notes
- **Migrations:** review-api has two initContainers, `wait-postgres` then `migrate` (`alembic upgrade head`),
  exactly like the old local overlay. So the API never starts on an old schema, and `helm install --wait`
  can't deadlock (nothing waits on a hook). A `review-api-migrate` Job also runs as a
  `post-install,post-upgrade` hook (idempotent no-op normally; same name the auto-heal script re-runs).
  Disable with `--set reviewApi.migrateJob.enabled=false`.
- **Ingress:** one rule `host: localhost`, path `/` -> `frontend:8080`. The frontend's nginx already proxies
  `/api`, `/auth`, `/me`, `/webhooks` to `gateway:8000`. Annotations turn off buffering and raise timeouts for
  the SSE progress stream. With ingress on, gateway `PUBLIC_URL` defaults to `http://<host>`.
- **Names** (services `frontend`, `gateway`, `review-api`, `postgres`; secrets `db-secrets`, `ai-keys-master`,
  `gateway-secrets`, `scanner-secrets`; configmaps `scanner-image`, `scanner-job-template`) are identical to
  Kustomize because the app config and nginx refer to them. Pod/container securityContexts are the restricted
  ones from the bases.
- PVCs `postgres-data` and `reports` have `helm.sh/resource-policy: keep` so `helm uninstall` keeps data.

## Kustomize -> Helm mapping (for the defense)
| Kustomize (deploy/) | Helm (this chart) |
|---|---|
| `base/` YAML per component | `templates/<component>/*.yaml` |
| `overlays/local`, `local-registry` patches | `values.yaml` (+ `--set` / `-f values.local.yaml`) |
| `images:` transformer (newName/newTag) | `global.imageRegistry`, `global.imageTag` -> `ai-scan.image` helper |
| `secretGenerator` from `secrets/*.env` | `templates/secrets.yaml` from `secrets:` values |
| `patches` (env, replicas, resources) | `{{ .Values... }}` in templates |
| `$patch: delete` of NetworkPolicy/Traefik/etc. | simply not in the chart (or `enabled:` flags) |
| Argo CD PreSync migrate Job | review-api initContainer + Helm `post-install/post-upgrade` hook |
| `kubectl apply -k` / `delete -k` | `helm install/upgrade` / `helm uninstall` (+ `helm rollback`) |

## Not in the basic chart (future `enabled: false` flags)
NetworkPolicies, Traefik IngressRoutes/Middlewares, ServiceMonitors, PrometheusRules, Grafana dashboard,
postgres backup / reports-cleanup / auto-heal CronJobs. Use `deploy/` (Kustomize/Argo CD) for those today.

**Status:** validated with `helm lint`, `helm template` and `kubeconform`; not yet run on a real cluster.

## Troubleshooting: ingress-nginx webhook error

`Internal error occurred: failed calling webhook "validate.nginx.ingress.kubernetes.io"` means the
controller's admission webhook wasn't reachable when the Ingress was created (controller still starting,
or a stale webhook left from an older ingress-nginx install). The failed release has no Ingress.

```
kubectl -n ingress-nginx get pods,svc,endpoints          # controller Running? admission endpoint present?
kubectl get validatingwebhookconfigurations              # stale entries from old installs?
kubectl -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=180s
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace
kubectl -n ai-scan-repos get ingress
```
If the controller is healthy but the error persists (stale webhook from a removed install):
`kubectl delete validatingwebhookconfiguration ingress-nginx-admission`, then
`helm upgrade --install ingress-nginx ...` again (it recreates it) and re-run the ai-scan upgrade.
