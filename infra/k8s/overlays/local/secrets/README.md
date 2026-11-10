# Local-only Secret placeholders (NOT FOR PRODUCTION)

These files feed `secretGenerator` in `infra/k8s/overlays/local`. Values are
fixed, well-known placeholders so a clean `kubectl apply -k infra/k8s/overlays/local`
needs no manual `kubectl create secret`.

**Never** reuse these values outside a personal Docker Desktop / k3d cluster.
For k3s/eks, keep creating Secrets from the CLI (see `docs/secrets-runbook.md`);
those overlays do not include these generators.

Real GitHub OAuth credentials: copy `gateway-oauth.local.env.example` to `gateway-oauth.local.env` (gitignored) and
create the optional Secret `gateway-oauth` from it (docs/local-k3d.md). Never edit `gateway-secrets.env` with real values.
