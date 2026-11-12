#!/usr/bin/env bash
# shellcheck disable=SC2329  # helpers are called indirectly (check, wait_for, trap)
# End-to-end test of auto-heal on the local stack (infra/k8s/overlays/local), current kubectl context.
# Runs from WSL or Git Bash; needs kubectl and images built/imported.
#   ci/heal/e2e-local.sh [--keep] [--timeout SECONDS] [--no-apply]
# Steps: apply + wait healthy -> baseline heal (nothing fixed) -> inject faults -> heal -> assert fixes ->
# restore -> healthy again -> cleanup -> PASS/FAIL.
set -uo pipefail

NS=ai-scan-repos
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OVERLAY="$ROOT/infra/k8s/overlays/local"
TIMEOUT=300
KEEP=0
APPLY=1
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --timeout) TIMEOUT=$2; shift ;;
    --no-apply) APPLY=0 ;;
    -h|--help) sed -n '3,8p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

k() { kubectl -n "$NS" "$@"; }
PASS=(); FAIL=()
ok() { PASS+=("$1"); echo "  PASS: $1"; }
bad() { FAIL+=("$1"); echo "  FAIL: $1"; }
check() { local what=$1; shift; if "$@"; then ok "$what"; else bad "$what"; fi; }
step() { echo; echo "== $*"; }

# wait_for <seconds> <cmd...>: polls every 5 s
wait_for() {
  local t=$1 end; shift; end=$((SECONDS + t))
  until "$@" >/dev/null 2>&1; do [ "$SECONDS" -ge "$end" ] && return 1; sleep 5; done
}

wait_healthy() {
  k wait --for=condition=complete job/review-api-migrate --timeout="${TIMEOUT}s" &&
  k rollout status statefulset/postgres --timeout="${TIMEOUT}s" &&
  k wait --for=condition=available deployment/review-api deployment/gateway deployment/frontend --timeout="${TIMEOUT}s"
}

# run_heal <job name>: one heal run from the CronJob template, log in $LOG_DIR/<name>.log
LOG_DIR=$(mktemp -d)
run_heal() {
  k delete job "$1" --ignore-not-found >/dev/null
  k create job --from=cronjob/ai-scan-autoheal "$1" >/dev/null || return 1
  k wait --for=condition=complete "job/$1" --timeout="${TIMEOUT}s" >/dev/null 2>&1 ||
    k wait --for=condition=failed "job/$1" --timeout=5s >/dev/null 2>&1
  k logs "job/$1" > "$LOG_DIR/$1.log" 2>&1
  echo "---- $1 report"; sed -n '/^== report/,$p' "$LOG_DIR/$1.log"
}
in_log() { grep -q -- "$2" "$LOG_DIR/$1.log"; }
rev() { k get deployment "$1" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}'; }
pod_waiting() {   # pod_waiting <app label> <reason regex>
  k get pods -l "app=$1" -o jsonpath='{range .items[*]}{.status.containerStatuses[*].state.waiting.reason}{"\n"}{end}' | grep -Eq "$2"
}

cleanup() {
  [ "$KEEP" = 1 ] && { echo "--keep: leaving heal-* jobs and logs in $LOG_DIR"; return; }
  k delete job heal-baseline heal-fix --ignore-not-found >/dev/null 2>&1
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 2; }
echo "context: $(kubectl config current-context)  namespace: $NS  timeout: ${TIMEOUT}s"

step "1. apply overlay and wait until healthy"
if [ "$APPLY" = 1 ]; then check "kubectl apply -k infra/k8s/overlays/local" kubectl apply -k "$OVERLAY"; fi
check "stack healthy (migrate Complete, deployments Available)" wait_healthy

step "2. baseline heal run (expect nothing fixed)"
run_heal heal-baseline
check "baseline report: fixed: 0" in_log heal-baseline "^fixed: 0"
check "baseline report: errors: 0" in_log heal-baseline "^errors: 0"

step "3. inject faults"
FRONT_REV=$(rev frontend); API_REV=$(rev review-api)
# a) frontend image tag that does not exist -> ErrImagePull/ImagePullBackOff
k set image deployment/frontend frontend=ai-scan-repos-frontend:e2e-does-not-exist >/dev/null
check "frontend pod in ImagePullBackOff" wait_for "$TIMEOUT" pod_waiting frontend 'ImagePullBackOff'
# b) review-api container exits 1 on start -> CrashLoopBackOff (and /readyz fails)
k patch deployment review-api --type json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo e2e fault; exit 1"]}]' >/dev/null
check "review-api pod in CrashLoopBackOff" wait_for "$TIMEOUT" pod_waiting review-api 'CrashLoopBackOff'
# c) failed migrate Job: a copy of the real Job whose container exits 1, backoffLimit 0. heal re-runs a failed Job
#    from its own spec, so the re-run fails again by design: we assert the Job was deleted and re-created (new uid).
IMG=$(k get job review-api-migrate -o jsonpath='{.spec.template.spec.containers[0].image}')
k delete job review-api-migrate --wait=true >/dev/null
k create -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: review-api-migrate, namespace: $NS, labels: {app: review-api-migrate, e2e: fault}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {app: review-api-migrate}}
    spec:
      restartPolicy: Never
      securityContext: {runAsNonRoot: true, runAsUser: 10001, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: migrate
          image: $IMG
          imagePullPolicy: IfNotPresent
          command: [sh, -c, "echo e2e migrate fault; exit 1"]
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
YAML
check "migrate Job Failed" k wait --for=condition=failed job/review-api-migrate --timeout="${TIMEOUT}s"
OLD_UID=$(k get job review-api-migrate -o jsonpath='{.metadata.uid}')

step "4. heal run (expect fixes)"
run_heal heal-fix
check "report: frontend ImagePullBackOff pod deleted" in_log heal-fix "delete pod/frontend-.*ImagePull"
check "report: review-api CrashLoopBackOff pod deleted" in_log heal-fix "delete pod/review-api-.*CrashLoopBackOff"
check "report: review-api rollout restart after /readyz failures" in_log heal-fix "rollout restart deployment/review-api"
check "report: migrate Job re-run" in_log heal-fix "re-run job/review-api-migrate"
check "report: Argo CD skipped (not an error)" in_log heal-fix "skipped: Argo CD"
check "report: errors: 0" in_log heal-fix "^errors: 0"
NEW_UID=$(k get job review-api-migrate -o jsonpath='{.metadata.uid}' 2>/dev/null)
check "migrate Job re-created (new uid)" test -n "$NEW_UID" -a "$NEW_UID" != "$OLD_UID"

step "5. restore and re-verify"
k rollout undo deployment/frontend --to-revision="$FRONT_REV" >/dev/null
k rollout undo deployment/review-api --to-revision="$API_REV" >/dev/null
k delete job review-api-migrate --wait=true >/dev/null   # the e2e copy; apply re-creates the real one
kubectl apply -k "$OVERLAY" >/dev/null
check "stack healthy again" wait_healthy
check "no pods left in a bad state" sh -c "! kubectl -n $NS get pods --no-headers | grep -Eq 'CrashLoopBackOff|ImagePullBackOff|ErrImagePull|Evicted'"

echo
echo "== summary: ${#PASS[@]} passed, ${#FAIL[@]} failed"
for f in "${FAIL[@]}"; do echo "  FAILED: $f"; done
[ "${#FAIL[@]}" -eq 0 ] && { echo "PASS"; exit 0; }
echo "FAIL (heal logs: rerun with --keep)"; exit 1
