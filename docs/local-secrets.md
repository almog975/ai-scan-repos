# Local secrets on Docker Desktop (no commit, ever)

The `ai-scan` chart ships **placeholder** Secrets (`gateway-secrets`, `scanner-secrets`, ...; see
[secrets-runbook.md](secrets-runbook.md)). Under GitOps (`values-docker-desktop.yaml`, `secrets.create: false`)
Argo CD renders the chart from git, so real values can't come from the chart. Instead you create two **optional
override Secrets** by hand. The pods load them *after* the placeholder Secret, so on a duplicate key the real
value wins. Pods start fine without them (`optional: true`); the feature just stays on placeholders.

| Override Secret (ns `ai-scan-repos`) | Overrides | Read by | Chart value |
|---|---|---|---|
| `gateway-oauth` | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` in `gateway-secrets` | gateway Deployment (`envFrom`) | `gateway.oauthSecretName` |
| `scanner-ai` | `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_API_KEY`, `AI_KIND` in `scanner-secrets` | every scanner Job (review-api adds it via `SCANNER_AI_SECRET_NAME`) | `scanner.aiSecretName` |

Rules:
- Every value below is a **placeholder**. Type the real ones in your own shell; never paste them into a chat,
  an issue, a commit, or a file in the repo. `kubectl create secret ... --from-literal` keeps them out of git.
- To avoid shell history, use `read -rs` (shown below) or `--from-env-file` with a file outside the repo.
- Argo CD doesn't manage these two Secrets (they aren't in the chart), so it never prunes or overwrites them.

## 1. `gateway-oauth`: GitHub sign-in

1. GitHub > **Settings > Developer settings > OAuth Apps > New OAuth App**:
   - Application name: `AI Scan (local)`
   - Homepage URL: `http://localhost:30080`
   - Authorization callback URL: `http://localhost:30080/auth/github/callback` (exactly; the gateway builds it
     from `PUBLIC_URL` = `gateway.publicUrl` in `values-docker-desktop.yaml`)
2. **Generate a new client secret** and copy the Client ID and the secret.
3. Create the Secret and restart the gateway:
   ```bash
   read -rp  'GitHub OAuth Client ID: '     GH_ID
   read -rsp 'GitHub OAuth Client secret: ' GH_SECRET; echo
   kubectl -n ai-scan-repos create secret generic gateway-oauth \
     --from-literal=GITHUB_CLIENT_ID="$GH_ID" \
     --from-literal=GITHUB_CLIENT_SECRET="$GH_SECRET" \
     --dry-run=client -o yaml | kubectl apply -f -
   unset GH_ID GH_SECRET
   kubectl -n ai-scan-repos rollout restart deploy/gateway
   ```
   Example shape with placeholders only:
   `--from-literal=GITHUB_CLIENT_ID=<your-oauth-app-client-id> --from-literal=GITHUB_CLIENT_SECRET=<your-oauth-app-client-secret>`
4. Check: http://localhost:30080 > **Log in with GitHub** goes to github.com and back. Only logins in
   `gateway.allowedUsers` get in.

`envFrom` is read only at container start, so after **any** change to `gateway-oauth` run the
`rollout restart`. With ingress on the LoadBalancer (:80) instead of NodePort 30080, use `http://localhost` in
the OAuth App and set `gateway.publicUrl` to match.

## 2. `scanner-ai`: default AI provider for scans

Without it, `scanner-secrets` has `AI_PROVIDER=none` and an empty `AI_API_KEY`, so scans run the static scanners
(semgrep, gitleaks, trivy) but skip the AI review.

There are two ways to give the scanner an AI provider:
- **Product way (preferred for the demo):** sign in as admin, add the provider under **AI providers** in the UI
  ([runbooks/admin-ai-providers.md](runbooks/admin-ai-providers.md)). The key is stored encrypted in Postgres and
  each review gets its own short-lived Secret. That per-review Secret always wins over `scanner-ai`.
- **Cluster default (this section):** the `scanner-ai` Secret, used for reviews that have no provider.

```bash
read -rsp 'AI API key: ' AI_KEY; echo
kubectl -n ai-scan-repos create secret generic scanner-ai \
  --from-literal=AI_PROVIDER=openai \
  --from-literal=AI_MODEL=gpt-4o-mini \
  --from-literal=AI_BASE_URL=https://api.openai.com \
  --from-literal=AI_KIND=cloud \
  --from-literal=AI_API_KEY="$AI_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset AI_KEY
```
Placeholder shape: `--from-literal=AI_API_KEY=<your-ai-api-key>`.

- `AI_PROVIDER`: one of `openai`, `anthropic`, `gemini`, `azure_openai`, `mistral`, `groq`, `ollama`,
  `openai_compatible` (`azure_openai` and `openai_compatible` need `AI_BASE_URL`).
- `AI_KIND`: `cloud` or `onprem`. It goes into the report; with `cloud`, code snippets leave your machine.
- On-prem example (no key): `AI_PROVIDER=ollama AI_BASE_URL=http://host.docker.internal:11434 AI_KIND=onprem AI_MODEL=qwen2.5-coder:7b`.

No restart is needed: each scan is a new Job, and Jobs read the Secret when they start. Check the next scan:
```bash
kubectl -n ai-scan-repos get jobs -l app=scanner --sort-by=.metadata.creationTimestamp | tail -1
kubectl -n ai-scan-repos get job <scan-job> -o jsonpath='{.spec.template.spec.containers[0].envFrom}'; echo
# [{"secretRef":{"name":"scanner-secrets"}},{"secretRef":{"name":"scanner-ai","optional":true}}]
```
The report's provider block then shows your provider instead of `none`.

### Without GitOps (plain `helm upgrade`)

With `secrets.create: true` (the chart's default), you can instead put the values in the gitignored
`infra/helm/ai-scan/values.local.yaml` (template: `values.local.yaml.example`): `secrets.aiApiKey`,
`scanner.aiProvider`, `scanner.aiModel`, `scanner.aiBaseUrl`. Under Argo CD that file is never read, so use the
Secrets above.

## 3. Rotation and removal

- Rotate: run the same `create ... | kubectl apply` again (then `rollout restart deploy/gateway` for OAuth).
- Remove: `kubectl -n ai-scan-repos delete secret scanner-ai` (scans fall back to no AI) or
  `delete secret gateway-oauth` + restart (sign-in falls back to placeholders and fails).
- A key that was ever pasted somewhere public (chat, issue, log): revoke it at the provider first.

## 4. Production: don't hand-create Secrets

Hand-made Secrets are fine for one laptop. For a shared or production cluster, keep the secret in git only in
encrypted form, or keep it out of git entirely:
- **Sealed Secrets** (bitnami-labs/sealed-secrets): `kubeseal` encrypts a Secret with the cluster's public key;
  the `SealedSecret` is safe to commit and Argo CD syncs it like any manifest.
- **External Secrets Operator**: an `ExternalSecret` points at Vault / AWS Secrets Manager / GCP Secret Manager
  / 1Password, and the operator creates and refreshes the Kubernetes Secret.

Either one produces Secrets named `gateway-oauth` and `scanner-ai`, so the chart needs no change. Neither is
installed in this project.
