# Local-only Secret placeholders (NOT FOR PRODUCTION)

These files feed `secretGenerator` in `deploy/overlays/local`. Values are
fixed, well-known placeholders so a clean `kubectl apply -k deploy/overlays/local`
needs no manual `kubectl create secret`.

**Never** reuse these values outside a personal Docker Desktop / k3d cluster.
For k3s/eks, keep creating Secrets from the CLI (see `docs/secrets-runbook.md`);
those overlays do not include these generators.
