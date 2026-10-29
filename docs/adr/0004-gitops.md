# ADR-0004: GitOps with Argo CD and Kustomize

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** AI Scan Planner, Pipelines, Infra, Docs; Almog
- **Related:** [ADR-0001 repo layout](0001-repo-layout.md), [`Jenkinsfile`](../../Jenkinsfile), [`deploy/argocd/`](../../deploy/argocd/), [ci-cd-flow.md](../ci-cd-flow.md), [ci.md](../ci.md), [rollback runbook](../runbooks/rollback.md)

## Context
AI Scan Repos has 6 deployable components (frontend, gateway, review-api, postgres, scanner-worker, platform) on k3s (later EKS). We need repeatable deploys, an audit trail, easy rollback, and CI that does not hold cluster credentials. Manifests already live in `deploy/<component>/{base,overlays/{local,k3s,eks}}` (Kustomize).

## Decision
We will use GitOps:
- **Git is the source of truth.** The desired state of every component is `deploy/<component>/overlays/<target>` on `main` in `almog975/ai-scan-repos`.
- **Argo CD** deploys it: app-of-apps root `ai-scan-root` → one Application per component, `ai-scan-<component>-k3s`, namespace `ai-scan-repos`, project `ai-scan`, auto-sync with `prune` + `selfHeal`, sync waves platform 0 → postgres 1 → review-api 2 → gateway/frontend/scanner-worker 3.
- **Kustomize** renders overlays; overlays ship with `GIT_SHA_PLACEHOLDER`.
- **Jenkins only builds, tests, scans and bumps tags.** It pushes images `<registry>/ai-scan/<svc>:<sha>` (never `latest`) and changes only `newTag` / `SCANNER_IMAGE` in the overlay, committing with `[skip ci]`. Jenkins never runs `kubectl apply` and has no cluster credentials.
- Rollback = `git revert` of the tag-bump commit via PR, then Argo CD sync. No force-push, no direct push to `main`.

## Consequences
- + Every deploy and rollback is a reviewable Git commit; drift is auto-corrected (selfHeal).
- + CI needs only registry and Git credentials, not cluster access.
- − Manual `kubectl` hotfixes are reverted; all changes must go through Git.
- − Jenkins needs write access to the repo for tag bumps; the loop guard (`[skip ci]`, deploy/-only) prevents rebuild loops.
- − Open item: the Jenkins tag-bump `git push` is still a TODO stub (`github-bot` credential not created). Awaiting Almog's decision: a direct commit to `main` by Jenkins, or an automatic PR. Meanwhile tags are bumped manually via PR.
- Schema migrations are not rolled back by Git reverts; handled separately.

## Alternatives
| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Jenkins `kubectl apply` / `helm upgrade` (push-based) | Simple, immediate | CI holds cluster creds; no drift detection; state not in Git | Weaker security and audit trail |
| Helm charts instead of Kustomize | Templating, packaging | More complexity for 6 small components | Kustomize overlays already fit local/k3s/eks |
| Flux CD | Lightweight GitOps | Less UI; team already set up Argo CD | Argo CD UI helps demos and debugging |
| Argo CD Image Updater | No tag-bump commits from CI | Another component; less explicit history | Explicit Jenkins commits are easier to audit and revert |
