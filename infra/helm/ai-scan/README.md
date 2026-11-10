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

## Auto-heal, Jenkins and Grafana on Docker Desktop

Extra pieces, all optional. Give Docker Desktop at least **6 GB RAM** (Settings > Resources) for the full set.

| Piece | Where | URL |
|---|---|---|
| Auto-heal Role (+ Jenkins RoleBinding) | `charts/ai-scan` `autoheal.*` (on by default) | - |
| Auto-heal CronJob (no Jenkins) | `charts/ai-scan` `autoheal.cronjob.enabled=true` | - |
| Jenkins with only the `ai-scan-autoheal` job | `deploy/jenkins/local/values-local.yaml` | http://jenkins.localhost |
| Prometheus + Grafana | `infra/helm/kube-prometheus-stack/values-local.yaml` | http://grafana.localhost |
| ServiceMonitors, alert rules, "AI Scan Repos" dashboard | `charts/ai-scan` `monitoring.enabled=true` | - |

Chrome and Edge resolve `*.localhost` to 127.0.0.1. If yours doesn't: `curl.exe -H "Host: jenkins.localhost" http://localhost/`.
With the NodePort option (B above), use `http://jenkins.localhost:30080` and `http://grafana.localhost:30080`. For Jenkins, also add `--set controller.jenkinsUrl=http://jenkins.localhost:30080` to its `helm upgrade --install`.

### Install (PowerShell, from the repo root, in this order)

From **WSL (Ubuntu)** the same commands work with bash line continuations (`\` instead of the backtick), `curl`
instead of `curl.exe`, and the password decoded with
`kubectl -n jenkins get secret jenkins -o jsonpath='{.data.jenkins-admin-password}' | base64 -d; echo`.
WSL's kubectl must use the `docker-desktop` context (Docker Desktop > Settings > Resources > WSL integration).

```powershell
kubectl config use-context docker-desktop

# 1. Prometheus + Grafana (installs the CRDs ServiceMonitor / PrometheusRule)
helm upgrade --install kube-prometheus-stack kube-prometheus-stack `
  --repo https://prometheus-community.github.io/helm-charts --version 65.1.1 `
  -n monitoring --create-namespace -f infra/helm/kube-prometheus-stack/values-local.yaml --wait

# 2. Jenkins (auto-heal job only) + the ServiceAccount its agent pods use
kubectl create namespace jenkins --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/jenkins/local/autoheal-sa.yaml
helm upgrade --install jenkins jenkins --repo https://charts.jenkins.io --version 5.10.3 `
  -n jenkins -f deploy/jenkins/local/values-local.yaml --wait --timeout 10m
# admin password (user: admin)
kubectl -n jenkins get secret jenkins -o jsonpath="{.data.jenkins-admin-password}" | %{ [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }

# 3. The app with monitoring objects (auto-heal RBAC is on by default)
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --create-namespace --set monitoring.enabled=true
#    NodePort option (B): keep the GitHub callback on :30080
#    add: --set gateway.publicUrl=http://localhost:30080

# 4. Check
kubectl -n ai-scan-repos get role,rolebinding ai-scan-autoheal
kubectl auth can-i delete pods -n ai-scan-repos --as system:serviceaccount:jenkins:ai-scan-autoheal   # yes
kubectl auth can-i delete pods -n jenkins       --as system:serviceaccount:jenkins:ai-scan-autoheal   # no
kubectl get ingress -A
```

- **`--set` again on every upgrade.** Pass the same `--set` flags (or a `-f` file) each time. `--reuse-values`
  also works, including from a release installed before `autoheal`/`monitoring` existed: the chart fills in
  missing keys with its defaults.
- **Interrupted `--wait`:** if an install or upgrade is killed or times out, the release can stay in
  `pending-install` / `pending-upgrade` and the next `helm upgrade` fails with "another operation is in progress".
  Check with `helm history <release> -n <ns>`. Roll back with `helm rollback <release> <last-good-rev> -n <ns>`.
  For a first install that never finished, run `helm uninstall <release> -n <ns>` and install again.

- **Jenkins:** http://jenkins.localhost, log in as `admin`. The job **AI Scan auto-heal** runs every 10 minutes (`H/10`) and on **Build with Parameters**. It checks out `ci/heal/Jenkinsfile` from `main` of the public repo (no credentials).
- **Grafana:** http://grafana.localhost, `admin` / `admin-local-dev-only` (local demo only). Open Dashboards > folder **AI Scan Repos** > **AI Scan Repos**. Prometheus targets: `kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090` and http://localhost:9090/targets (review-api and gateway should be UP).

### Auto-heal demo

What heal.sh does and doesn't do: it deletes broken pods, restarts Deployments whose `/readyz` keeps failing, and re-runs a failed `review-api-migrate` Job. It never edits a spec. In the full setup Argo CD (selfHeal) restores a wrong spec from git; locally there is no Argo CD, so after a *spec* fault you undo it yourself (`rollout undo`). The demo shows detection + action in the Jenkins console:

```powershell
# a) break the frontend image -> ImagePullBackOff
kubectl -n ai-scan-repos set image deployment/frontend frontend=ghcr.io/almog975/ai-scan-repos-frontend:does-not-exist
kubectl -n ai-scan-repos get pods -w        # wait for ImagePullBackOff, then Ctrl+C
```
- Jenkins > **AI Scan auto-heal** > **Build with Parameters** > tick `DRY_RUN` > Build. The console report shows `would delete pod/frontend-...`.
- Build again without `DRY_RUN`. The report shows `fixed: 1  - delete pod/frontend-... (ImagePullBackOff)`.
- Restore the spec (Argo CD's job in production) and confirm a clean run:
```powershell
kubectl -n ai-scan-repos rollout undo deployment/frontend
kubectl -n ai-scan-repos rollout status deployment/frontend
```
- Build once more: `found: 0`, `errors: 0`.

In Grafana the dashboard shows the app metrics (reviews, heartbeats); pod restarts are visible in the built-in
dashboard **Kubernetes / Compute Resources / Namespace (Pods)** for namespace `ai-scan-repos`.

### Without Jenkins: the CronJob

```powershell
helm upgrade --install ai-scan ./charts/ai-scan -n ai-scan-repos --reuse-values --set autoheal.cronjob.enabled=true
kubectl -n ai-scan-repos create job --from=cronjob/ai-scan-autoheal heal-now
kubectl -n ai-scan-repos logs job/heal-now -f
```
The script is a copy in `files/heal.sh` (Helm can't read files outside the chart); `ci/tests/test_helm_chart_sync.py`
fails if it drifts from `ci/heal/heal.sh`. Same for the dashboard JSON and the alert rules.

### CI/CD + GitOps (Jenkins builds, Argo CD deploys)

Push to GitHub, and Jenkins tests, builds, scans and pushes `ghcr.io/almog975/ai-scan-repos-*:<git sha>`, then
commits the tag into `values-images.yaml`; Argo CD deploys it. Setup, demo and rollback are in
[docs/cicd-docker-desktop.md](../../docs/cicd-docker-desktop.md).

### Teardown
```powershell
helm uninstall jenkins -n jenkins; kubectl delete ns jenkins
helm uninstall kube-prometheus-stack -n monitoring; kubectl delete ns monitoring
# CRDs stay installed (kubectl get crd | findstr monitoring.coreos.com); remove them only if nothing else uses them.
```
