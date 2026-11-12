# ai-scan Helm chart

Helm chart for running the app on Docker Desktop Kubernetes.

It installs the frontend, gateway, review-api, postgres, and the scanner Job template.

Argo CD uses this chart with `values-docker-desktop.yaml` and `values-images.yaml`.

The placeholder secrets in `values.yaml` are for local use only.

## Install

You need Docker Desktop with Kubernetes, `kubectl`, and `helm`.

```powershell
kubectl config use-context docker-desktop
helm upgrade --install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx -n ingress-nginx --create-namespace --wait
kubectl -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=180s
helm upgrade --install ai-scan ./infra/helm/ai-scan -n ai-scan-repos --create-namespace
kubectl -n ai-scan-repos get pods -w
```

Then open http://localhost.

Without ingress:

```powershell
helm upgrade --install ai-scan ./infra/helm/ai-scan -n ai-scan-repos --create-namespace --set ingress.enabled=false
kubectl -n ai-scan-repos port-forward svc/frontend 8080:8080
```

## GitHub login

Add `http://localhost/auth/github/callback` as the callback URL of your GitHub OAuth App (or `http://localhost:8080/auth/github/callback` with port-forward).

Real credentials never go in git.

Copy `values.local.yaml.example` to `values.local.yaml` (gitignored) and pass it with `-f`, or create the `gateway-oauth` Secret as shown in [local secrets](../../../docs/local-secrets.md).

## Useful values

- `global.imageRegistry=""` uses locally built `ai-scan-repos-*:latest` images.
- `gateway.publicUrl` sets the OAuth callback base URL.
- `monitoring.enabled=true` adds ServiceMonitors, alert rules, and the Grafana dashboard (needs kube-prometheus-stack).
- `autoheal.cronjob.enabled=true` runs the auto-heal script as a CronJob.
- `frontend.autoscaling.enabled=true` (same for `gateway` and `reviewApi`) adds an HPA (needs metrics-server).

`files/heal.sh`, the dashboard JSON, and the alert rules are copies, because Helm can't read files outside the chart.

`ci/tests/test_helm_chart_sync.py` fails if they drift from the originals.

## Upgrade and remove

```powershell
helm upgrade ai-scan ./infra/helm/ai-scan -n ai-scan-repos
helm rollback ai-scan 1 -n ai-scan-repos
helm uninstall ai-scan -n ai-scan-repos
kubectl delete ns ai-scan-repos
```

The `postgres-data` and `reports` PVCs are kept on uninstall and removed with the namespace.

CI/CD with Jenkins and Argo CD is in [CI/CD on Docker Desktop](../../../docs/cicd-docker-desktop.md).
