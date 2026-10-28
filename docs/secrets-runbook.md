# Secrets runbook (names and keys only; never commit values)

All secrets live in ns `ai-scan-repos` unless noted.

| Secret | Keys | Used by |
|---|---|---|
| `gateway-secrets` | GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, SESSION_KEY, WEBHOOK_SECRET | gateway |
| `db-secrets` | POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL | postgres, review-api |
| `ai-keys-master` | AI_KEYS_ENCRYPTION_KEY (Fernet) | review-api |
| `scanner-secrets` | INTERNAL_API_TOKEN, AI_PROVIDER, AI_MODEL, AI_BASE_URL, AI_API_KEY (optional defaults) | scanner Jobs, review-api (token) |
| `grafana-admin` (ns monitoring) | admin-user, admin-password | Grafana |
| `registry-htpasswd` (ns registry) | htpasswd | registry (TODO) |
| `registry-tls` (ns registry) | tls.crt, tls.key | Traefik |

Example: `kubectl -n ai-scan-repos create secret generic ai-keys-master --from-literal=AI_KEYS_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"`
Rotation: re-create the secret, then `kubectl rollout restart deploy/<svc>`. Rotating ai-keys-master requires re-encrypting the stored keys first.
