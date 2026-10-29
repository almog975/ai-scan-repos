# Running on K3s (Phase 5)

Every component takes its configuration **only from environment variables** (no config files in the images).
The Deployments set `APP_ENV=production`. In that mode review-api and the gateway refuse to start when a
dev default is still in place, and they log one `config:` line per problem before exiting
(`app/config.py` `problems()`):

| Component | Refuses to start when |
|---|---|
| review-api | `DATABASE_URL` is sqlite; `INTERNAL_API_TOKEN` is the dev default or shorter than 32 characters; `AI_KEYS_ENCRYPTION_KEY` is missing or invalid; `JOB_LAUNCHER` is not `kubernetes`; `SCANNER_IMAGE` is not pinned |
| gateway | `DATABASE_URL` is sqlite; `INTERNAL_API_TOKEN` as above; `GITHUB_CLIENT_ID`/`SECRET` missing; `PUBLIC_URL` is not https; `COOKIE_SECURE` is false |

A pod in `CrashLoopBackOff` right after a deploy usually means a missing secret key. Run `kubectl -n ai-scan-repos logs deploy/<name>` to see which one.

## Where the env comes from

| Var | review-api | gateway |
|---|---|---|
| `DATABASE_URL` | `db-secrets` (envFrom) | `db-secrets` key `DATABASE_URL` only |
| `INTERNAL_API_TOKEN` | `scanner-secrets` key only | `scanner-secrets` key only (must be the same value) |
| `AI_KEYS_ENCRYPTION_KEY` | `ai-keys-master` | — |
| `GITHUB_CLIENT_ID/SECRET`, `GITHUB_WEBHOOK_SECRET` | — | `gateway-secrets` |
| `PUBLIC_URL`, `COOKIE_SECURE`, `FORWARDED_ALLOW_IPS` | — | Deployment env (the eks overlay patches `PUBLIC_URL`) |

The AI_* defaults in `scanner-secrets` are meant for scanner Jobs, so the API pods no longer load that secret wholesale.

## Probes

| Pod | startup | liveness | readiness |
|---|---|---|---|
| review-api | `/healthz` (up to 60 s) | `/healthz` (process only) | `/readyz`: DB, AES key, K8s API (`get jobs`) |
| gateway | `/healthz` (up to 60 s) | `/healthz` | `/readyz`: DB |
| frontend | — | `/nginx-health` | `/nginx-health` |
| postgres | — | `pg_isready` | `pg_isready` |

Liveness never checks dependencies, so a DB outage takes pods out of rotation instead of restarting them in a loop.
The gateway and frontend pods do not mount a ServiceAccount token.

## Checks

`ci/kubeconform.sh` runs `kustomize build` on every overlay and validates the rendered output with kubeconform
(strict; Traefik/Argo CD CRDs from the datree catalog). review-api `tests/test_phase5_k3s.py` asserts the env,
the probes and least privilege on the rendered k3s/local/eks overlays.
