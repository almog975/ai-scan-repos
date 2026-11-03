# Local overlays on Docker Desktop Kubernetes

The `overlays/local` overlays target single-node dev clusters (Docker Desktop, k3d, K3s on a laptop).
Their PVCs (`reports`, `postgres-data`, `postgres-backups`) have **no `storageClassName`**, so they use the
cluster's default StorageClass: `hostpath` on Docker Desktop, `local-path` on k3d/K3s. The k3s and eks overlays
are unchanged. Check the default with `kubectl get storageclass`; one class must be marked `(default)`.

## 1. Secrets (namespace `ai-scan-repos`) - create before applying

| Secret | Keys | Used by |
|---|---|---|
| `db-secrets` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL` | postgres, review-api (+ migrate Job), gateway |
| `scanner-secrets` | `INTERNAL_API_TOKEN` (+ AI defaults, see secrets-runbook.md) | review-api, gateway, scanner Jobs |
| `ai-keys-master` | `AI_KEYS_ENCRYPTION_KEY` (`openssl rand -hex 32`) | review-api |
| `gateway-secrets` | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, webhook secret | gateway |

Use `DATABASE_URL=postgresql+psycopg://USER:PASS@postgres:5432/DB`. The images ship psycopg v3 only; a bare
`postgresql://` / `postgres://` is normalized to `postgresql+psycopg://` by the apps, but write the explicit
driver anyway. `infra/installer/aiscan-install.sh` creates all of these for you; see
[secrets-runbook.md](secrets-runbook.md) for the full key list.

## 2. Apply order

review-api mounts ConfigMap `scanner-job-template` and reads ConfigMap `scanner-image`, both from the
scanner-worker overlay. If scanner-worker is not applied first, review-api stays in `ContainerCreating`.

```bash
for c in platform postgres scanner-worker review-api gateway frontend; do
  kubectl apply -k deploy/$c/overlays/local
done
```

Locally built images (e.g. `docker build -t ... gateway/`) are visible to Docker Desktop's cluster directly;
set the image tags in the overlays or with `kustomize edit set image`.

## Troubleshooting

- `ContainerCreating` on review-api: `kubectl describe pod` -> missing ConfigMap `scanner-job-template` /
  `scanner-image` (apply scanner-worker) or PVC `reports` Pending (no default StorageClass).
- `ModuleNotFoundError: No module named 'psycopg2'`: old image with a bare `postgresql://` DATABASE_URL; rebuild
  or set `postgresql+psycopg://` in `db-secrets`.
