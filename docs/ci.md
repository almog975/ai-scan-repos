# CI/CD

- Webhook relay (smee.io): the channel URL is not stored in the repo. It lives in the Jenkins
  secret-text credential `smee-url` (or the `SMEE_URL` env var on the Jenkins host).
  - On the Jenkins host: `smee -u "$SMEE_URL" -t http://localhost:8080/github-webhook/`
  - GitHub webhook payload URL = the smee URL (JSON, push events).
- Loop guard: builds are skipped (NOT_BUILT) when the last commit message has `[skip ci]` or every
  changed file is under `deploy/`. Jenkins' own tag commits carry `[skip ci]`.
- Pipeline: root `Jenkinsfile` — Detect changes, Test, Build, Trivy scan (fail on HIGH/CRITICAL), Push, Deploy.
- Only services whose folder changed are built; images `<registry>/ai-scan/<svc>:<git-sha>`, never `latest`.
- Deploy layout: `deploy/{frontend,gateway,review-api,postgres,scanner-worker,platform}/{base,overlays/{local,k3s,eks}}`.
  Each built service maps to its own folder. `TARGET` is a parameter (default k3s).
- Deploy stage (in `deploy/<svc>/overlays/${TARGET}`):
  - frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<registry>/ai-scan/<svc>:<sha>`.
  - scanner-worker: sets `SCANNER_IMAGE=<registry>/ai-scan/scanner-worker:<sha>` in the `scanner-image`
    configMapGenerator via `kustomize edit set configmap` (fallback yq, then sed). If the overlay has no such
    generator yet, it falls back to `kustomize edit set image`.
  - Commits with `[skip ci]`; push is a TODO stub (`github-bot` credential).
- Argo CD: `deploy/argocd-app.yaml` — 6 Applications for frontend, gateway, review-api, postgres, platform, scanner-worker →
  `deploy/<name>/overlays/k3s`, namespace ai-scan-repos, auto-sync prune + selfHeal.
  review-api reads `SCANNER_IMAGE` via `configMapKeyRef` on ConfigMap `scanner-image` (generated in the scanner-worker overlay).
