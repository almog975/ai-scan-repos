# Auto-heal job (`ai-scan-autoheal`)

A Jenkins pipeline job (JCasC in `deploy/jenkins/values.yaml`, pipeline `ci/heal/Jenkinsfile`, script
`ci/heal/heal.sh`) that runs every 10 minutes (`H/10 * * * *`) and on **Build Now**. Concurrent builds are disabled.

## What it does (namespace `ai-scan-repos`, env `NAMESPACE`)

| Check | Action |
|---|---|
| Pod in `CrashLoopBackOff`, `ImagePullBackOff`/`ErrImagePull`, or `Evicted` | delete the pod (its Deployment/StatefulSet/Job recreates it) |
| Job `review-api-migrate` has condition `Failed` | delete it and create it again from its **live** spec (status, uid, resourceVersion, creationTimestamp, managedFields, `spec.selector` and controller-uid/job-name labels stripped) |
| `review-api` `/readyz` :8001, `gateway` `/readyz` :8000, `frontend` `/nginx-health` :8080 fail `READYZ_FAILS` (3) times in a row, `READYZ_INTERVAL` (10 s) apart | `kubectl rollout restart deployment/<name>` |
| `pg_isready` in the postgres pod (`kubectl exec`) | report only |
| Argo CD Application in `argocd`, project `ai-scan`, `OutOfSync` with no operation running | patch `.operation.sync.revision` = its `targetRevision` (same as `argocd app sync`) |

It only deletes, restarts or re-runs. It never edits a spec, a manifest or git, so Argo CD selfHeal has nothing to revert.

**Why N checks inside one run** (not a counter across runs): it needs no state, no extra RBAC (no annotation
writes on Deployments), and survives Jenkins restarts. A service that is down for 3 checks 10 s apart is really down;
a blip during a rollout recovers before the 3rd check.

## Permissions

ServiceAccount `jenkins/ai-scan-autoheal` (`deploy/jenkins/manifests/autoheal-sa.yaml`), used only by the
`autoheal` pod template. Namespaced Roles only, no ClusterRole:

- `ai-scan-repos` (`deploy/platform/base/autoheal-rbac.yaml`): pods get/list/delete, pods/exec create, jobs get/list/create/delete, deployments get/list/patch.
- `argocd` (`infra/argocd/autoheal-rbac.yaml`, applied by the installer): applications get/list/patch.

The `jenkins-agent` SA of the normal CI pipeline still has no API access. NetworkPolicies: `autoheal-egress` (ns
jenkins) and `autoheal-readyz-ingress` (ns ai-scan-repos) allow only pods labelled `jenkins/label=autoheal` to reach
the three services' ports. The K8s API and DNS were already allowed for Jenkins pods.

## Trigger

- Wait for the cron (every 10 min), or
- Jenkins → **AI Scan auto-heal** → **Build with Parameters** → (optionally tick `DRY_RUN`) → Build.
- From a shell with cluster access, the same script: `DRY_RUN=1 NAMESPACE=ai-scan-repos bash ci/heal/heal.sh`

## Verify

1. Console output of the build ends with a report:
   ```
   == report
   found: 1
     - pod/gateway-7c9d-abcde is ImagePullBackOff
   fixed: 1
     - delete pod/gateway-7c9d-abcde (ImagePullBackOff)
   errors: 0
   ```
2. `kubectl -n ai-scan-repos get pods` and `kubectl -n ai-scan-repos get events --sort-by=.lastTimestamp | tail -20`
   (deleted pods, `rollout restart`, re-created Job). Argo CD syncs show `initiatedBy: ai-scan-autoheal`.
3. Force a fault, e.g. ImagePullBackOff with a missing image tag (not committed to git; Argo CD selfHeal reverts it):
   ```bash
   kubectl -n ai-scan-repos set image deployment/frontend frontend=ai-scan/frontend:does-not-exist
   # run the job (Build Now) with DRY_RUN ticked: report shows "would delete pod/frontend-..."
   # run it without DRY_RUN: the pod is deleted; Argo CD restores the real image
   ```
   Or a failed migration: `kubectl -n ai-scan-repos get job review-api-migrate` shows a new Job (new AGE) after the next run.

## Dry run

`DRY_RUN` build parameter (or `DRY_RUN=1` env): every action is printed as `DRY-RUN: would ...` and listed in the
report; no delete, patch, create or restart is sent.
