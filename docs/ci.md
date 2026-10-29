# CI/CD

- Webhook relay (smee.io): the channel URL is not stored in the repo. It lives in the Jenkins
  secret-text credential `smee-url` (or the `SMEE_URL` env var on the Jenkins host).
  - On the Jenkins host: `smee -u "$SMEE_URL" -t http://localhost:8080/github-webhook/`
  - GitHub webhook payload URL = the smee URL (JSON, push events).
- Loop guard: builds are skipped (NOT_BUILT) when the last commit message has `[skip ci]` or every
  changed file is under `deploy/`. Jenkins' own tag commits carry `[skip ci]`.
- Pipeline: root `Jenkinsfile` — Loop guard, Test, Build, Scan (fail on HIGH/CRITICAL), Push, Deploy.
- Only services whose folder changed are built; images `<registry>/ai-scan/<svc>:<git-sha>`, never `latest`.
- Deploy layout: `deploy/{frontend,gateway,review-api,postgres,scanner-worker,platform}/{base,overlays/{local,k3s,eks}}`.
  Each built service maps to its own folder. `TARGET` is a parameter (default k3s).
- Phase 6 pipeline (Kubernetes agent pod `ai-scan-agent`: containers `tools` + `kaniko`, see deploy/jenkins/README.md):
  Loop guard (skip if last commit author is `ci-bot@ai-scan.local`, message has `[skip ci]`, or only `deploy/**`
  changed) → Test → Build (kaniko to tarball) → Scan (`trivy image --exit-code 1 --severity CRITICAL,HIGH --input`)
  → Push (crane, credential `registry-push`, main only) → Deploy (main only). Each component stage has
  `when { anyOf { changeset '<component>/**'; <changed list> } }`: `changeset` is empty on a branch's first build,
  so the git-diff `changed` list (vs GIT_PREVIOUS_SUCCESSFUL_COMMIT) is kept as fallback.
  JUnit: vitest → `frontend/reports/junit.xml`, pytest → `reports/<svc>-junit.xml`, published in `post { always }`.
  Triggers: GitHub webhook via smee-client Deployment, `pollSCM H/5` + 5 min branch indexing as fallback.
- Deploy stage (in `deploy/<svc>/overlays/${TARGET}`):
  - frontend, gateway, review-api: `kustomize edit set image ai-scan/<svc>=<registry>/ai-scan/<svc>:<sha>`.
  - scanner-worker: sets `SCANNER_IMAGE=<registry>/ai-scan/scanner-worker:<sha>` in the `scanner-image`
    configMapGenerator via `kustomize edit set configmap` (kustomize >= 5.1 in the agent image).
  - Commits as `ai-scan-ci-bot <ci-bot@ai-scan.local>` with `[skip ci]`, main only; push is a TODO stub
    (`github-bot` credential, pending Almog's approval).
- Argo CD (app-of-apps): bootstrap once with `kubectl apply -n argocd -f deploy/argocd/root-app.yaml`
  (after Infra's AppProjects `ai-scan` and `ai-scan-ci`). Jenkins itself is the child app `ai-scan-jenkins`
  (deploy/argocd/apps/jenkins.yaml, ns jenkins, project ai-scan-ci); see deploy/jenkins/README.md.
  Root `ai-scan-root` syncs `deploy/argocd/apps/`, which holds 6 child Applications (frontend, gateway, review-api,
  postgres, platform, scanner-worker) → `deploy/<name>/overlays/k3s` on `main`, namespace ai-scan-repos, auto-sync
  prune + selfHeal. Sync waves: platform 0, postgres 1, review-api 2, gateway/frontend/scanner-worker 3.
  review-api reads `SCANNER_IMAGE` via `configMapKeyRef` on ConfigMap `scanner-image` (generated in the scanner-worker overlay).
