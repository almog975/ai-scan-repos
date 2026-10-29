# Secrets runbook (names and keys only; never commit values)

All secrets live in ns `ai-scan-repos` unless noted.

| Secret | Keys | Used by |
|---|---|---|
| `gateway-secrets` | GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET | gateway |
| `db-secrets` | POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL | postgres, review-api, gateway (DATABASE_URL only) |
| `ai-keys-master` | AI_KEYS_ENCRYPTION_KEY (AES-256-GCM, 32 bytes hex/base64) | review-api |
| `scanner-secrets` | INTERNAL_API_TOKEN, AI_PROVIDER, AI_MODEL, AI_BASE_URL, AI_API_KEY (optional defaults) | scanner Jobs; review-api and gateway read only INTERNAL_API_TOKEN (>= 32 chars) |
| `grafana-admin` (ns monitoring) | admin-user, admin-password | Grafana |
| `registry-htpasswd` (ns registry) | htpasswd (bcrypt, push + pull users) | registry (docs/registry-auth-runbook.md) |
| `regcred` (ns ai-scan-repos, docker-registry) | .dockerconfigjson (pull user) | SAs default, review-api, scanner; scanner Job template |
| `registry-push` (ns jenkins, docker-registry; same name as the Jenkins credential) | .dockerconfigjson (push user) | kaniko/buildkit agents only |
| Jenkins credential `registry-push` | username/password (push user) | Jenkinsfile docker login |
| `ai-scan-tls` / `jenkins-tls` / `argocd-ingress-tls` | tls.crt, tls.key | Traefik (docs/tls-runbook.md) |
| `registry-tls` (ns registry) | tls.crt, tls.key | Traefik |

Example: `kubectl -n ai-scan-repos create secret generic ai-keys-master --from-literal=AI_KEYS_ENCRYPTION_KEY="$(openssl rand -hex 32)"`
Rotation: re-create the secret, then `kubectl rollout restart deploy/<svc>`. Rotating ai-keys-master requires re-encrypting the stored keys first.
