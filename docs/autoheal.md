# Auto-heal job (`ai-scan-autoheal`)

A Jenkins pipeline job (JCasC in `infra/jenkins/values.yaml`, pipeline `ci/heal/Jenkinsfile`, script
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

ServiceAccount `jenkins/ai-scan-autoheal` (`infra/jenkins/manifests/autoheal-sa.yaml`), used only by the
`autoheal` pod template. Namespaced Roles only, no ClusterRole:

- `ai-scan-repos` (`infra/k8s/platform/base/autoheal-rbac.yaml`): pods get/list/delete, pods/exec create, jobs get/list/create/delete, deployments get/list/patch.
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

## Local CronJob

`infra/k8s/overlays/local` runs the same script in-cluster, without Jenkins:

- CronJob `ai-scan-repos/ai-scan-autoheal`: `*/10 * * * *`, `concurrencyPolicy: Forbid`, `alpine/k8s:1.31.4`
  (`IfNotPresent`), PSA-restricted (uid 65534, runAsNonRoot, drop ALL, seccomp RuntimeDefault, no privilege
  escalation, read-only root filesystem; emptyDir on `/tmp`, `HOME=/tmp` for kubectl's cache).
- Script: ConfigMap `ai-scan-autoheal-script` generated from `ci/heal/heal.sh` by `ci/heal/kustomization.yaml`
  (one copy of the script; the overlay includes that directory, which the default load restrictor allows).
- RBAC: SA `ai-scan-repos/ai-scan-autoheal` bound (`ai-scan-autoheal-local`) to the same Role
  `ai-scan-autoheal` the Jenkins job uses. The Jenkins RoleBinding also renders locally; its subject
  `jenkins/ai-scan-autoheal` does not exist there, which is harmless.
- NetworkPolicies: the overlay deletes all of them, including `autoheal-readyz-ingress` (left in place it would
  ingress-isolate review-api/gateway/frontend).
- No Argo CD locally: the Argo CD step prints `skipped: Argo CD (...)`, not an error.

Run once: `kubectl -n ai-scan-repos create job --from=cronjob/ai-scan-autoheal heal-now`, then
`kubectl -n ai-scan-repos logs job/heal-now`.

## End-to-end test (local)

`ci/heal/e2e-local.sh [--keep] [--timeout SECONDS] [--no-apply]` (bash: WSL or Git Bash; current kubectl context):

1. `kubectl apply -k infra/k8s/overlays/local`; waits for job `review-api-migrate` Complete, postgres rolled out,
   review-api/gateway/frontend Available.
2. `heal-baseline` job from the CronJob: asserts `fixed: 0` and `errors: 0`.
3. Faults:
   - frontend image → `ai-scan-repos-frontend:e2e-does-not-exist` (ImagePullBackOff);
   - review-api container command → `exit 1` (CrashLoopBackOff, and `/readyz` fails);
   - `review-api-migrate` replaced by a copy whose container exits 1 (`backoffLimit: 0`) → Failed.
4. `heal-fix` job: asserts the report deleted the frontend and review-api pods, restarted review-api, re-ran the
   migrate Job, skipped Argo CD and had `errors: 0`; and that the migrate Job has a **new uid**. heal.sh re-runs a
   failed Job from its own live spec, so the re-run of the faulty copy fails again by design. The test checks the
   heal action (delete + re-create), not that the migration then succeeds.
5. Restore: `rollout undo --to-revision` for frontend and review-api, delete the faulty Job, re-apply the overlay
   (re-creates the real migrate Job), wait healthy, check that no pod is in a bad state. Deletes the heal jobs
   (`--keep` leaves them and the logs). Prints PASS/FAIL, exits 1 on any failure.

Not covered by the e2e test: Evicted pods (hard to force on a laptop cluster; covered by unit tests) and the
Argo CD sync (no Argo CD in this overlay; covered by unit tests).
