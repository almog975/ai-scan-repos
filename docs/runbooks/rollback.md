# Runbook: roll back a deployment

Git is the source of truth ([ADR-0004](../adr/0004-gitops.md)): roll back by **reverting the tag-bump commit** and letting Argo CD sync. Do **not** `kubectl set image` (selfHeal reverts it), do **not** force-push, and do **not** push to `main` directly – go through a PR.

Flow background: [ci-cd-flow.md](../ci-cd-flow.md).

## 1. Find the bad tag-bump commit

```bash
git fetch origin
git log origin/main --oneline -- deploy/*/overlays/k3s/kustomization.yaml | head
# Jenkins commits look like: ci(k3s): gateway,review-api -> abc1234 [skip ci]
git show <bump-sha> -- deploy/          # confirm which newTag / SCANNER_IMAGE changed
```

Check what is running now:

```bash
kubectl -n ai-scan-repos get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image
kubectl -n ai-scan-repos get cm -o yaml | grep SCANNER_IMAGE   # image used for new scanner Jobs
argocd app get ai-scan-<component>-k3s
```

## 2. Revert on a branch and open a PR

```bash
git switch -c rollback/<component>-<bad-sha> origin/main
git revert --no-edit <bump-sha>
# keep [skip ci] so Jenkins' loop guard does not rebuild (only deploy/ changes anyway)
git commit --amend -m "revert: ci(k3s) <bump-sha> – roll back <component> [skip ci]"
git push -u origin rollback/<component>-<bad-sha>
gh pr create --base main --title "Rollback <component> to previous image" --body "Reverts <bump-sha>."
```

If later tag bumps touched the same file, `git revert` may conflict: resolve by setting `newTag` (or `SCANNER_IMAGE`) to the last known-good tag, then `git revert --continue`.

Merge the PR after review (normal merge, no force).

## 3. Argo CD resync

Auto-sync picks up `main` within the polling interval (~3 min). To apply now:

```bash
argocd app get ai-scan-root --refresh
argocd app sync ai-scan-<component>-k3s
argocd app wait ai-scan-<component>-k3s --health --sync --timeout 300
```

Without the CLI: Argo CD UI → `ai-scan-<component>-k3s` → **Refresh** → **Sync**.

## 4. Verify

```bash
kubectl -n ai-scan-repos rollout status deploy/<component> --timeout=180s
kubectl -n ai-scan-repos get deploy <component> -o jsonpath='{.spec.template.spec.containers[*].image}'; echo
kubectl -n ai-scan-repos get pods
curl -fsS https://<host>/healthz && curl -fsS https://<host>/readyz
```

- The image tag matches the known-good SHA; Argo CD shows **Synced / Healthy**.
- scanner-worker rollback affects **new** review Jobs only; reviews already running keep their image. Pause/resume or retry a review to pick up the old image.
- Database: a rollback does not downgrade the schema. If the bad release included an alembic migration, check compatibility first ([DB parity](../ci.md)); a schema downgrade is a separate, manual step.

## 5. Afterwards

- Fix forward on a feature branch; Jenkins builds a new image and a new tag bump.
- Note: until the Jenkins tag-bump push is enabled (TODO stub, see [ci-cd-flow.md](../ci-cd-flow.md#2-tag-bump-in-deploycomponentoverlaysk3s)), tag bumps on `main` are made by hand via PR, and rolled back the same way.
