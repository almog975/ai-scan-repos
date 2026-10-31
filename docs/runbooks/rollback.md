# Runbook: roll back a deployment (git revert → Argo CD sync)

Git is the source of truth ([ADR-0004](../adr/0004-gitops.md)): roll back by **reverting the tag-bump commit** and
letting Argo CD sync. Do **not** `kubectl set image` (selfHeal undoes it within seconds), do **not** force-push.
Flow background: [ci-cd-flow.md](../ci-cd-flow.md).

## 0. Preconditions
- Argo CD apps `ai-scan-<component>-k3s` are **Synced/Healthy** and track `main` (`deploy/argocd/apps/*.yaml`).
- The known-good image (`<registry>/ai-scan/<component>:<good-sha>`) is still in the registry (never delete old tags
  before a demo).
- You can push a branch to `almog975/ai-scan-repos` and open/merge a PR to `main` (`gh` logged in, or the GitHub UI).
- Tools: `git`, `kubectl` (context = the k3s cluster), optional `argocd` CLI logged in with
  `argocd login argocd.ai-scan.127.0.0.1.nip.io --grpc-web` (argocd-server runs insecure behind Traefik).

## 1. Find the bad tag-bump commit
Jenkins deploys arrive as merged PRs from `deploy/<component>-<sha>` (or `deploy/multi-<sha>`); revert the merge
(`git revert -m 1 <merge-sha>`) or, for a squash/rebase merge, the bump commit itself.
```bash
git fetch origin
git log origin/main --oneline -- deploy/<component>/overlays/k3s
# Jenkins commits: "ci(k3s): gateway,scanner-worker -> bbbbbbb [skip ci]" by ai-scan-ci-bot <ci-bot@ai-scan.local>
git show <bump-sha> -- deploy/          # which newTag / SCANNER_IMAGE changed
kubectl -n ai-scan-repos get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image
kubectl -n ai-scan-repos get cm scanner-image -o jsonpath='{.data.SCANNER_IMAGE}'; echo
```
A Jenkins bump commit can touch several components (e.g. gateway + scanner-worker). Reverting it rolls back all of
them; to roll back only one, edit that overlay back to the good tag instead (same `kustomize edit` commands as below).

## 2. Revert through a PR
```bash
git switch -c rollback/<component>-<bad-sha> origin/main
git revert --no-edit <bump-sha>           # merge commit of the deploy PR: git revert --no-edit -m 1 <merge-sha>
kustomize build deploy/<component>/overlays/k3s | grep -E 'image:|SCANNER_IMAGE'   # must show the good tag
git push -u origin rollback/<component>-<bad-sha>
gh pr create --base main --title "Rollback <component> to <good-sha>" --body "Reverts <bump-sha>."
# or the GitHub UI: "Revert" button on the merged deploy PR creates the same revert PR.
# Merge the PR (normal merge, no force, no direct push to main), then Argo CD syncs (step 3).
```
If later bumps touched the same file, `git revert` conflicts: set the good tag by hand, then `git revert --continue`:
```bash
(cd deploy/<component>/overlays/k3s && kustomize edit set image ai-scan/<component>=<registry>/ai-scan/<component>:<good-sha>)
# scanner-worker:
(cd deploy/scanner-worker/overlays/k3s && kustomize edit set configmap scanner-image \
   --from-literal=SCANNER_IMAGE=<registry>/ai-scan/scanner-worker:<good-sha>)
```

## 3. Argo CD sync
Auto-sync picks up `main` on its next poll (default ~3 min; immediately if a GitHub → Argo CD webhook is set). To speed up:
```bash
argocd app sync ai-scan-<component>-k3s --grpc-web
argocd app wait ai-scan-<component>-k3s --sync --health --timeout 300 --grpc-web
```
Without the CLI: UI → `ai-scan-<component>-k3s` → **Refresh** → **Sync**.

## 4. Verify
```bash
kubectl -n ai-scan-repos rollout status deploy/<component> --timeout=180s
kubectl -n ai-scan-repos get deploy <component> -o jsonpath='{.spec.template.spec.containers[*].image}'; echo
kubectl -n ai-scan-repos get cm scanner-image -o jsonpath='{.data.SCANNER_IMAGE}'; echo    # scanner-worker
curl -fsS https://ai-scan.127.0.0.1.nip.io/healthz
```
Expect the good tag and Argo CD **Synced / Healthy**. scanner-worker has no Deployment: the rollback changes the
`scanner-image` ConfigMap, which affects **new** scan Jobs only; running reviews keep their image.

## Alternative: `argocd app rollback`
```bash
argocd app history ai-scan-<component>-k3s --grpc-web
argocd app set ai-scan-<component>-k3s --sync-policy none --grpc-web     # REQUIRED first, see below
argocd app rollback ai-scan-<component>-k3s <history-id> --grpc-web
```
Caveat: rollback is refused while auto-sync is on, and with auto-sync/selfHeal re-enabled Argo CD syncs back to `main`
(the bad tag). It is a fast stop-gap only; still do the git revert, then re-enable auto-sync:
`argocd app set ai-scan-<component>-k3s --sync-policy automated --self-heal --auto-prune --grpc-web`.
Also, the root app `ai-scan-root` (selfHeal) re-applies the child Application spec, which may restore the child's
sync policy; disable auto-sync on `ai-scan-root` too for the duration, or prefer the git revert.

## Timing (estimate)
| Step | Time |
|---|---|
| Find commit + revert + push/merge | 1–2 min |
| Argo CD detects the change | ≤ 3 min poll (seconds with webhook or `argocd app sync`) |
| Rollout (image cached on node) | 20–60 s |
| **Total** | **~1.5 min with manual sync, ~5 min waiting for the poll** |

## Gotchas
- **No CI rebuild:** the revert touches only `deploy/**` and its subject contains `[skip ci]` (`Revert "ci(k3s): … [skip ci]"`),
  so the Jenkins loop guard skips it (rehearsed; see below). Do not edit the message to drop `[skip ci]` and do not
  mix code changes into the rollback commit. A PR merge commit also only changes `deploy/**`, so it is skipped too.
- **Poll interval:** ~3 min (`timeout.reconciliation` in `argocd-cm`) unless the Argo CD webhook is configured.
- **DB migrations do not roll back.** review-api's migrations Job only upgrades. If the bad release added an alembic
  migration, the old image must be compatible with the new schema; a downgrade is a separate manual step.
- **Next bump:** the following Jenkins build writes a new tag again; fix forward on a branch.
- **Deploy PRs:** Jenkins opens a PR per build (`deploy/*`); an unmerged deploy PR is simply closed, no rollback
  needed. `rollback/*` branches are excluded from Jenkins indexing and the merge is skipped as `deploy/**`-only.

## Demo script
```bash
C=gateway; R=registry.registry:5000
BAD=$(git log origin/main --first-parent --format=%h -1 -- deploy/$C/overlays/k3s)
git show --stat $BAD
git switch -c rollback/$C-$BAD origin/main
git revert --no-edit -m 1 $BAD      # BAD = merge commit of the deploy PR; for a squash merge drop "-m 1"
kustomize build deploy/$C/overlays/k3s | grep "image: $R/ai-scan/$C"
git push -u origin HEAD && gh pr create --fill --base main && gh pr merge --merge   # or open/merge in the UI
argocd app sync ai-scan-$C-k3s --grpc-web && argocd app wait ai-scan-$C-k3s --sync --health --grpc-web
kubectl -n ai-scan-repos rollout status deploy/$C --timeout=180s
kubectl -n ai-scan-repos get deploy $C -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
```

## Rehearsal (local, no cluster, 2026-10-29)
Two simulated Jenkins bumps (author `ai-scan-ci-bot`, `[skip ci]`, same `kustomize edit` commands as the Jenkinsfile),
then `git revert --no-edit <B>`:
```
after A : gateway:aaaaaaa  SCANNER_IMAGE=…/scanner-worker:aaaaaaa   guard: skip (bot author)
after B : gateway:bbbbbbb  SCANNER_IMAGE=…/scanner-worker:bbbbbbb   guard: skip (bot author)
revert  : author = human, subject 'Revert "ci(k3s): gateway,scanner-worker -> bbbbbbb [skip ci]"'
          files = deploy/gateway/overlays/k3s/kustomization.yaml, deploy/scanner-worker/overlays/k3s/kustomization.yaml
          guard: skip ([skip ci]; would also skip as deploy/**-only)
build   : gateway:aaaaaaa  SCANNER_IMAGE=…/scanner-worker:aaaaaaa   (tree identical to A)
```
Not rehearsed: the Argo CD sync and rollout (needs a live cluster).
