#!/usr/bin/env bash
# AI Scan Repos auto-heal (Jenkins job ai-scan-autoheal, ci/heal/Jenkinsfile). docs/autoheal.md
# Only restarts, deletes or re-runs things. It never edits a spec, a manifest or git, so Argo CD selfHeal has
# nothing to fight. DRY_RUN=1 reports what it would do and changes nothing.
#
# Env: NAMESPACE (ai-scan-repos), ARGOCD_NAMESPACE (argocd), ARGOCD_PROJECT (ai-scan), MIGRATE_JOB
#      (review-api-migrate), READYZ_FAILS (3) consecutive failures before a restart, READYZ_INTERVAL (10 s),
#      READYZ_TIMEOUT (5 s), DRY_RUN (0), KUBECTL (kubectl), CURL (curl).
set -uo pipefail

NS=${NAMESPACE:-ai-scan-repos}
ARGO_NS=${ARGOCD_NAMESPACE:-argocd}
ARGO_PROJECT=${ARGOCD_PROJECT:-ai-scan}
MIGRATE_JOB=${MIGRATE_JOB:-review-api-migrate}
N=${READYZ_FAILS:-3}
INTERVAL=${READYZ_INTERVAL:-10}
TIMEOUT=${READYZ_TIMEOUT:-5}
KUBECTL=${KUBECTL:-kubectl}
CURL=${CURL:-curl}
case "${DRY_RUN:-0}" in 1|true|TRUE|yes) DRY=1 ;; *) DRY=0 ;; esac
# deployment|service port|probe path (frontend has no /readyz; nginx-health is its readiness probe)
TARGETS=${READYZ_TARGETS:-"review-api|8001|/readyz gateway|8000|/readyz frontend|8080|/nginx-health"}

FOUND=(); FIXED=(); ERRORS=()
k() { "$KUBECTL" "$@"; }
found() { FOUND+=("$1"); echo "FOUND: $1"; }
# act "<description>" cmd...: runs cmd unless dry-run, and records the result
act() {
  local what=$1; shift
  if [ "$DRY" = 1 ]; then FIXED+=("[dry-run] would $what"); echo "DRY-RUN: would $what"; return 0; fi
  if "$@"; then FIXED+=("$what"); echo "FIXED: $what"; else ERRORS+=("failed to $what"); echo "ERROR: failed to $what" >&2; fi
}

# 1) pods in CrashLoopBackOff / ImagePullBackOff / Evicted -> delete (the owning controller recreates them)
heal_pods() {
  local json
  json=$(k -n "$NS" get pods -o json) || { ERRORS+=("cannot list pods in $NS"); return; }
  while IFS=$'\t' read -r name reason; do
    [ -n "$name" ] || continue
    found "pod/$name is $reason"
    act "delete pod/$name ($reason)" k -n "$NS" delete pod "$name" --wait=false
  done < <(jq -r '.items[] |
      ( if .status.reason == "Evicted" then "Evicted"
        else ([(.status.containerStatuses // []) + (.status.initContainerStatuses // []) | .[] | .state.waiting.reason // empty
               | select(. == "CrashLoopBackOff" or . == "ImagePullBackOff" or . == "ErrImagePull")] | first // empty)
        end ) as $r | select($r != null) | [.metadata.name, $r] | @tsv' <<<"$json")
}

# 2) failed migrate Job -> delete it and create it again from its own live spec (cluster state, not the manifests)
heal_migrate() {
  local json failed clean
  json=$(k -n "$NS" get job "$MIGRATE_JOB" -o json 2>/dev/null) || { echo "ok: job/$MIGRATE_JOB not present"; return; }
  failed=$(jq -r '[.status.conditions // [] | .[] | select(.type == "Failed" and .status == "True")] | length' <<<"$json")
  [ "$failed" -gt 0 ] || { echo "ok: job/$MIGRATE_JOB not failed"; return; }
  found "job/$MIGRATE_JOB failed"
  clean=$(clean_job "$json")
  # shellcheck disable=SC2329,SC2317  # invoked through act
  recreate() {
    k -n "$NS" delete job "$MIGRATE_JOB" --wait=true --cascade=foreground --timeout=60s && k -n "$NS" create -f - <<<"$clean"
  }
  act "re-run job/$MIGRATE_JOB (delete + create from its live spec)" recreate
}

# strips server-generated fields so the Job can be created again
clean_job() {
  jq 'del(.status, .metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.generation,
          .metadata.managedFields, .metadata.ownerReferences, .metadata.selfLink,
          .metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"],
          .spec.selector, .spec.manualSelector)
      | del(.metadata.labels["controller-uid"], .metadata.labels["batch.kubernetes.io/controller-uid"],
            .metadata.labels["batch.kubernetes.io/job-name"], .metadata.labels["job-name"])
      | del(.spec.template.metadata.labels["controller-uid"], .spec.template.metadata.labels["batch.kubernetes.io/controller-uid"],
            .spec.template.metadata.labels["batch.kubernetes.io/job-name"], .spec.template.metadata.labels["job-name"])' <<<"$1"
}

# 3) /readyz: N checks in this run, READYZ_INTERVAL apart; restart only if all N fail (stateless, see docs)
heal_readyz() {
  local t dep port path fails i
  for t in $TARGETS; do
    IFS='|' read -r dep port path <<<"$t"
    k -n "$NS" get deployment "$dep" >/dev/null 2>&1 || { echo "ok: deployment/$dep not present"; continue; }
    fails=0
    for ((i = 1; i <= N; i++)); do
      if "$CURL" -fsS -o /dev/null --max-time "$TIMEOUT" "http://$dep.$NS.svc:$port$path"; then break; fi
      fails=$((fails + 1))
      [ "$i" -lt "$N" ] && sleep "$INTERVAL"
    done
    if [ "$fails" -ge "$N" ]; then
      found "deployment/$dep failed $path $fails times in a row"
      act "rollout restart deployment/$dep" k -n "$NS" rollout restart deployment "$dep"
    else
      echo "ok: $dep $path"
    fi
  done
}

# 4) postgres: pg_isready inside the postgres pod (exec goes through the API server, no extra NetworkPolicy)
check_postgres() {
  local pod
  pod=$(k -n "$NS" get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  [ -n "$pod" ] || { found "no postgres pod in $NS"; return; }
  if k -n "$NS" exec "$pod" -c postgres -- pg_isready -q -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "ok: postgres ($pod) accepts connections"
  else
    # report only: a not-ready postgres pod is restarted by the pod rules above (CrashLoopBackOff) or its probes
    found "postgres ($pod) pg_isready failed"
  fi
}

# 5) Argo CD Applications of the stack (project $ARGO_PROJECT) that are OutOfSync -> request a sync via .operation
heal_argocd() {
  local json
  json=$(k -n "$ARGO_NS" get applications.argoproj.io -o json 2>/dev/null) || { echo "skip: cannot list Argo CD applications in $ARGO_NS"; return; }
  while IFS=$'\t' read -r app rev; do
    [ -n "$app" ] || continue
    found "application/$app OutOfSync"
    act "request Argo CD sync of application/$app (revision $rev)" \
      k -n "$ARGO_NS" patch applications.argoproj.io "$app" --type merge \
        -p "{\"operation\":{\"initiatedBy\":{\"username\":\"ai-scan-autoheal\"},\"sync\":{\"revision\":\"$rev\"}}}"
  done < <(jq -r --arg p "$ARGO_PROJECT" '.items[]
      | select(.spec.project == $p and .status.sync.status == "OutOfSync")
      | select(.operation == null and (.status.operationState.phase // "") != "Running")
      | [.metadata.name, (.spec.source.targetRevision // .spec.sources[0].targetRevision // "HEAD")] | @tsv' <<<"$json")
}

echo "== ai-scan auto-heal: namespace $NS$( [ "$DRY" = 1 ] && echo ' (DRY RUN)')"
heal_pods
heal_migrate
heal_readyz
check_postgres
heal_argocd

echo "== report"
echo "found: ${#FOUND[@]}"; for x in "${FOUND[@]}"; do echo "  - $x"; done
echo "fixed: ${#FIXED[@]}"; for x in "${FIXED[@]}"; do echo "  - $x"; done
echo "errors: ${#ERRORS[@]}"; for x in "${ERRORS[@]}"; do echo "  - $x"; done
[ "${#ERRORS[@]}" -eq 0 ]
