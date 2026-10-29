#!/usr/bin/env bash
# AI Scan Repos installer (Phase 8). Non-interactive, idempotent, --dry-run prints the plan without touching
# the cluster, the cloud or the working tree. Docs: docs/installer.md.
#
#   aiscan-install.sh [--config FILE] [--dry-run] [--overlay k3s|eks|local] [--mode gitops|direct]
#                     [--registry local|ecr] [--tag TAG] [--yes] [--purge] [--rotate-secrets] COMMAND
#   COMMAND: install (= preflight bootstrap registry secrets images deploy verify) | preflight | bootstrap |
#            registry | secrets | images | deploy | verify | ecr-login | uninstall
#
# Values come from (later wins): defaults < --config env file < environment < flags. Secret values are only
# ever read from the environment/config file and written to the cluster through 0600 temp files; they are
# never echoed, logged or written into the repo.
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ARGOCD_CHART_VERSION_PINNED="10.11.6"   # argo/argo-cd chart -> Argo CD v3.5.6 (multi-source apps need >= 2.6)
APP_NS=ai-scan-repos
COMPONENTS=(platform postgres scanner-worker review-api gateway frontend)
IMAGES=(review-api gateway frontend)    # kustomize `images:` entries; scanner-worker goes via SCANNER_IMAGE

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }

# ---- arguments / config -------------------------------------------------------------------------------------
CONFIG="" ; CMD="" ; declare -A FLAG=()
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --dry-run) FLAG[DRY_RUN]=1; shift ;;
    --overlay) FLAG[OVERLAY]="$2"; shift 2 ;;
    --mode) FLAG[MODE]="$2"; shift 2 ;;
    --registry) FLAG[REGISTRY_TYPE]="$2"; shift 2 ;;
    --tag) FLAG[IMAGE_TAG]="$2"; shift 2 ;;
    --yes) FLAG[ASSUME_YES]=1; shift ;;
    --purge) FLAG[PURGE]=1; shift ;;
    --rotate-secrets) FLAG[ROTATE_SECRETS]=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) [ -z "$CMD" ] || die "one command only"; CMD="$1"; shift ;;
  esac
done
[ -n "$CMD" ] || die "command required (try --help)"

if [ -n "$CONFIG" ]; then
  [ -f "$CONFIG" ] || die "config $CONFIG not found"
  # env file: KEY=VALUE lines only; values already in the environment win
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || die "bad config line: ${line%%=*}"
    k="${line%%=*}"; v="${line#*=}"; v="${v%\"}"; v="${v#\"}"
    [ -n "${!k+x}" ] || export "$k=$v"
  done < "$CONFIG"
fi
for k in "${!FLAG[@]}"; do export "$k=${FLAG[$k]}"; done

DRY_RUN="${DRY_RUN:-0}"; OVERLAY="${OVERLAY:-k3s}"; MODE="${MODE:-gitops}"; REGISTRY_TYPE="${REGISTRY_TYPE:-local}"
IMAGE_TAG="${IMAGE_TAG:-GIT_SHA_PLACEHOLDER}"; ASSUME_YES="${ASSUME_YES:-0}"; PURGE="${PURGE:-0}"
ROTATE_SECRETS="${ROTATE_SECRETS:-0}"; ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-$ARGOCD_CHART_VERSION_PINNED}"
ECR_REPO_PREFIX="${ECR_REPO_PREFIX:-ai-scan}"; KUBECTL="${KUBECTL:-kubectl}"
case "$OVERLAY" in k3s|eks|local) ;; *) die "--overlay must be k3s|eks|local" ;; esac
case "$MODE" in gitops|direct) ;; *) die "--mode must be gitops|direct" ;; esac
case "$REGISTRY_TYPE" in local|ecr) ;; *) die "--registry must be local|ecr" ;; esac
if [ "$MODE" = gitops ] && [ "$OVERLAY" != k3s ]; then
  die "gitops mode deploys deploy/argocd/apps (k3s overlays); use --mode direct for $OVERLAY"
fi
[[ "$IMAGE_TAG" =~ ^[A-Za-z0-9_.-]{1,128}$ ]] || die "bad --tag"

if [ "$REGISTRY_TYPE" = ecr ]; then
  : "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID required for --registry ecr}" "${AWS_REGION:?AWS_REGION required for --registry ecr}"
  [[ "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || die "AWS_ACCOUNT_ID must be 12 digits"
  [[ "$AWS_REGION" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]$ ]] || die "bad AWS_REGION"
  REGISTRY_HOST="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
  IMAGE_PREFIX="$REGISTRY_HOST/$ECR_REPO_PREFIX"
else
  case "$OVERLAY" in
    k3s) REGISTRY_HOST="${REGISTRY_HOST:-registry.ai-scan.127.0.0.1.nip.io}" ;;
    local) REGISTRY_HOST="${REGISTRY_HOST:-registry.ai-scan.local}" ;;
    eks) : "${REGISTRY_HOST:?REGISTRY_HOST required for eks with a local registry}" ;;
  esac
  IMAGE_PREFIX="$REGISTRY_HOST/ai-scan"
fi
[[ "$REGISTRY_HOST" =~ ^[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || die "bad REGISTRY_HOST"

# ---- helpers ------------------------------------------------------------------------------------------------
dry() { [ "$DRY_RUN" = 1 ]; }
run() { echo "+ $*"; dry || "$@"; }
k() { run "$KUBECTL" "$@"; }
TMP=$(mktemp -d); chmod 700 "$TMP"; trap 'rm -rf "$TMP"' EXIT
rand_hex() { openssl rand -hex "$1"; }
gen() { if dry; then echo "<generated>"; else rand_hex "$1"; fi; }  # never printed
exists() { ! dry && "$KUBECTL" -n "$1" get "$2" "$3" >/dev/null 2>&1; }

# apply_secret NS NAME TYPE KEY=ENVVAR...   (values from env; prints key names only)
apply_secret() {
  local ns=$1 name=$2 type=$3; shift 3
  local keys=() f="$TMP/$name.env"
  : > "$f"; chmod 600 "$f"
  local kv var
  for kv in "$@"; do
    var="${kv#*=}"; keys+=("${kv%%=*}")
    printf '%s=%s\n' "${kv%%=*}" "${!var:-}" >> "$f"
  done
  if exists "$ns" secret "$name" && [ "$ROTATE_SECRETS" != 1 ]; then
    echo "  secret $ns/$name exists: kept (use --rotate-secrets to replace)"; return 0
  fi
  echo "+ $KUBECTL -n $ns create secret $type $name [keys: ${keys[*]}] --dry-run=client -o yaml | $KUBECTL apply -f -"
  dry && return 0
  "$KUBECTL" -n "$ns" create secret "$type" "$name" --from-env-file="$f" --dry-run=client -o yaml | "$KUBECTL" apply -f - >/dev/null
}

need_env() { for v in "$@"; do [ -n "${!v:-}" ] || die "$v is required (env or --config)"; done; }

# ---- steps --------------------------------------------------------------------------------------------------
preflight() {
  log "preflight (overlay=$OVERLAY mode=$MODE registry=$REGISTRY_TYPE tag=$IMAGE_TAG)"
  local tools=(kubectl kustomize openssl)
  [ "$MODE" = gitops ] && tools+=(helm)
  [ "$REGISTRY_TYPE" = ecr ] && tools+=(aws)
  [ "$REGISTRY_TYPE" = local ] && tools+=(htpasswd)
  local missing=()
  for t in "${tools[@]}"; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
  if dry; then echo "  tools needed: ${tools[*]} (dry-run: cluster access not checked)"; return 0; fi
  [ ${#missing[@]} -eq 0 ] || die "missing tools: ${missing[*]}"
  "$KUBECTL" version --request-timeout=10s >/dev/null || die "no cluster access (kubectl version failed)"
  echo "  context: $("$KUBECTL" config current-context 2>/dev/null || echo '?')"
  for check in "create namespaces" "create customresourcedefinitions" "create clusterroles" "create secrets -n $APP_NS"; do
    # shellcheck disable=SC2086
    "$KUBECTL" auth can-i $check >/dev/null 2>&1 || die "insufficient RBAC: cannot '$check' (see docs/installer.md#rbac)"
  done
  if [ "$REGISTRY_TYPE" = ecr ]; then aws sts get-caller-identity >/dev/null || die "aws credentials not usable"; fi
  echo "  ok"
}

bootstrap() {
  log "bootstrap: namespaces, storage (infra/bootstrap/overlays/$OVERLAY)"
  if [ "$OVERLAY" = k3s ]; then
    need_env NODE_NAME
    [[ "$NODE_NAME" =~ ^[a-z0-9.-]+$ ]] || die "bad NODE_NAME"
    echo "+ kustomize build infra/bootstrap/overlays/k3s | sed s/REPLACE_NODE_NAME/$NODE_NAME/ | $KUBECTL apply -f -"
    dry || kustomize build "$ROOT/infra/bootstrap/overlays/k3s" | sed "s/REPLACE_NODE_NAME/$NODE_NAME/" | "$KUBECTL" apply -f -
  elif [ "$OVERLAY" = eks ]; then
    need_env EFS_FILESYSTEM_ID
    [[ "$EFS_FILESYSTEM_ID" =~ ^fs-[0-9a-f]+$ ]] || die "bad EFS_FILESYSTEM_ID"
    echo "+ kustomize build infra/bootstrap/overlays/eks | sed s/fs-REPLACE_ME/$EFS_FILESYSTEM_ID/ | $KUBECTL apply -f -"
    dry || kustomize build "$ROOT/infra/bootstrap/overlays/eks" | sed "s/fs-REPLACE_ME/$EFS_FILESYSTEM_ID/" | "$KUBECTL" apply -f -
  else
    k apply -k "$ROOT/infra/bootstrap/overlays/local"
  fi
  k apply -k "$ROOT/infra/ingress/overlays/$OVERLAY"
}

registry() {
  if [ "$REGISTRY_TYPE" = ecr ]; then
    log "registry: ECR $IMAGE_PREFIX (no in-cluster registry)"
    for r in "${IMAGES[@]}" scanner-worker jenkins-agent; do
      echo "+ aws ecr describe-repositories --repository-names $ECR_REPO_PREFIX/$r || aws ecr create-repository ... --image-scanning-configuration scanOnPush=true"
      dry && continue
      aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO_PREFIX/$r" >/dev/null 2>&1 \
        || aws ecr create-repository --region "$AWS_REGION" --repository-name "$ECR_REPO_PREFIX/$r" \
             --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null
    done
    ecr_login
    return 0
  fi
  log "registry: in-cluster registry $REGISTRY_HOST (htpasswd)"
  need_env REGISTRY_PUSH_USER REGISTRY_PUSH_PASSWORD REGISTRY_PULL_USER REGISTRY_PULL_PASSWORD
  if ! exists registry secret registry-htpasswd || [ "$ROTATE_SECRETS" = 1 ]; then
    echo "+ htpasswd -Bbn <push> / <pull> > \$TMP/htpasswd; $KUBECTL -n registry create secret generic registry-htpasswd --from-file=htpasswd | apply"
    if ! dry; then
      htpasswd -Bbn "$REGISTRY_PUSH_USER" "$REGISTRY_PUSH_PASSWORD" > "$TMP/htpasswd"
      htpasswd -Bbn "$REGISTRY_PULL_USER" "$REGISTRY_PULL_PASSWORD" >> "$TMP/htpasswd"
      "$KUBECTL" -n registry create secret generic registry-htpasswd --from-file=htpasswd="$TMP/htpasswd" \
        --dry-run=client -o yaml | "$KUBECTL" apply -f - >/dev/null
    fi
  else echo "  secret registry/registry-htpasswd exists: kept"; fi
  k apply -f "$ROOT/infra/registry/registry.yaml"
  regcred "$REGISTRY_HOST" "$REGISTRY_PULL_USER" "$REGISTRY_PULL_PASSWORD"
  apply_secret jenkins registry-push generic "username=REGISTRY_PUSH_USER" "password=REGISTRY_PUSH_PASSWORD"
  if ! dry; then
    "$KUBECTL" -n jenkins label --overwrite secret registry-push jenkins.io/credentials-type=usernamePassword >/dev/null
    "$KUBECTL" -n jenkins annotate --overwrite secret registry-push jenkins.io/credentials-description='registry push user' >/dev/null
  fi
}

# regcred SERVER USER PASSWORD -> docker-registry secret regcred in ns ai-scan-repos and jenkins
regcred() {
  local server=$1 user=$2 pass=$3 f="$TMP/dockerconfig.json"
  for ns in "$APP_NS" jenkins; do
    echo "+ $KUBECTL -n $ns create secret generic regcred --type=kubernetes.io/dockerconfigjson [server $server, user $user] | apply"
  done
  dry && return 0
  umask 077
  printf '{"auths":{"%s":{"username":"%s","password":"%s","auth":"%s"}}}' "$server" "$user" "$pass" \
    "$(printf '%s:%s' "$user" "$pass" | base64 | tr -d '\n')" > "$f"
  for ns in "$APP_NS" jenkins; do
    "$KUBECTL" -n "$ns" create secret generic regcred --type=kubernetes.io/dockerconfigjson \
      --from-file=.dockerconfigjson="$f" --dry-run=client -o yaml | "$KUBECTL" apply -f - >/dev/null
  done
}

# ECR tokens expire after 12h: re-run `aiscan-install.sh ecr-login` (cron/systemd timer) to refresh regcred.
ecr_login() {
  log "ecr-login: refresh regcred for $REGISTRY_HOST"
  [ "$REGISTRY_TYPE" = ecr ] || die "ecr-login needs --registry ecr"
  echo "+ aws ecr get-login-password --region $AWS_REGION  (token not printed)"
  if dry; then regcred "$REGISTRY_HOST" AWS "<token>"; return 0; fi
  local tok; tok=$(aws ecr get-login-password --region "$AWS_REGION")
  regcred "$REGISTRY_HOST" AWS "$tok"
}

secrets() {
  log "secrets (ns $APP_NS): never committed; existing ones are kept unless --rotate-secrets"
  need_env GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET
  # generated when absent (only used if the Secret does not exist yet: idempotent)
  : "${GITHUB_WEBHOOK_SECRET:=$(gen 32)}"
  : "${INTERNAL_API_TOKEN:=$(gen 32)}"
  : "${POSTGRES_PASSWORD:=$(gen 24)}"
  : "${POSTGRES_USER:=aiscan}" "${POSTGRES_DB:=aiscan}"
  : "${AI_PROVIDER:=none}" "${AI_MODEL:=}" "${AI_BASE_URL:=}" "${AI_API_KEY:=}"
  [ "${#INTERNAL_API_TOKEN}" -ge 32 ] || dry || die "INTERNAL_API_TOKEN must be >= 32 chars"
  DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB"
  export GITHUB_WEBHOOK_SECRET INTERNAL_API_TOKEN POSTGRES_PASSWORD POSTGRES_USER POSTGRES_DB DATABASE_URL \
         AI_PROVIDER AI_MODEL AI_BASE_URL AI_API_KEY
  apply_secret "$APP_NS" gateway-secrets generic GITHUB_CLIENT_ID=GITHUB_CLIENT_ID \
    GITHUB_CLIENT_SECRET=GITHUB_CLIENT_SECRET GITHUB_WEBHOOK_SECRET=GITHUB_WEBHOOK_SECRET
  apply_secret "$APP_NS" db-secrets generic POSTGRES_USER=POSTGRES_USER POSTGRES_PASSWORD=POSTGRES_PASSWORD \
    POSTGRES_DB=POSTGRES_DB DATABASE_URL=DATABASE_URL
  apply_secret "$APP_NS" scanner-secrets generic INTERNAL_API_TOKEN=INTERNAL_API_TOKEN AI_PROVIDER=AI_PROVIDER \
    AI_MODEL=AI_MODEL AI_BASE_URL=AI_BASE_URL AI_API_KEY=AI_API_KEY
  # ai-keys-master encrypts every stored provider key: NEVER replaced by the installer (rotation = re-encrypt first)
  if exists "$APP_NS" secret ai-keys-master; then
    echo "  secret $APP_NS/ai-keys-master exists: kept (never rotated by the installer, see docs/secrets-runbook.md)"
  else
    : "${AI_KEYS_ENCRYPTION_KEY:=$(gen 32)}"; export AI_KEYS_ENCRYPTION_KEY
    local save=$ROTATE_SECRETS; ROTATE_SECRETS=1
    apply_secret "$APP_NS" ai-keys-master generic AI_KEYS_ENCRYPTION_KEY=AI_KEYS_ENCRYPTION_KEY
    ROTATE_SECRETS=$save
  fi
  if [ -n "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
    : "${GRAFANA_ADMIN_USER:=admin}"; export GRAFANA_ADMIN_USER
    apply_secret monitoring grafana-admin generic admin-user=GRAFANA_ADMIN_USER admin-password=GRAFANA_ADMIN_PASSWORD
  fi
}

# Rewrites image registry/tag in deploy/<c>/overlays/$OVERLAY in the working tree. Never commits or pushes:
# in gitops mode Argo CD deploys what is on main, so commit the diff through a PR (the CI tag-bump push is a stub).
images() {
  log "images: $IMAGE_PREFIX/<component>:$IMAGE_TAG (overlay $OVERLAY)"
  for c in "${IMAGES[@]}"; do
    echo "+ (cd deploy/$c/overlays/$OVERLAY && kustomize edit set image ai-scan/$c=$IMAGE_PREFIX/$c:$IMAGE_TAG)"
    dry || (cd "$ROOT/deploy/$c/overlays/$OVERLAY" && kustomize edit set image "ai-scan/$c=$IMAGE_PREFIX/$c:$IMAGE_TAG")
  done
  local f="$ROOT/deploy/scanner-worker/overlays/$OVERLAY/kustomization.yaml"
  echo "+ sed -i 's|SCANNER_IMAGE=.*|SCANNER_IMAGE=$IMAGE_PREFIX/scanner-worker:$IMAGE_TAG|' deploy/scanner-worker/overlays/$OVERLAY/kustomization.yaml"
  dry || sed -i "s|SCANNER_IMAGE=.*|SCANNER_IMAGE=$IMAGE_PREFIX/scanner-worker:$IMAGE_TAG|" "$f"
  if ! dry && [ "$MODE" = gitops ] && ! git -C "$ROOT" diff --quiet -- deploy; then
    echo "  NOTE: overlays changed in the working tree; Argo CD deploys main. Open a PR with:"
    git -C "$ROOT" --no-pager diff --stat -- deploy
  fi
}

deploy() {
  if [ "$MODE" = gitops ]; then
    log "deploy: Argo CD $ARGOCD_CHART_VERSION (pinned) + AppProjects + app-of-apps"
    run helm repo add argo https://argoproj.github.io/argo-helm --force-update
    run helm upgrade --install argocd argo/argo-cd -n argocd --version "$ARGOCD_CHART_VERSION" \
      --set 'configs.params.server\.insecure=true' --wait --timeout 10m
    k -n argocd patch cm argocd-cm --type merge --patch-file "$ROOT/infra/argocd/argocd-cm-health-patch.yaml"
    k apply -f "$ROOT/infra/argocd/appproject-ai-scan.yaml"
    k apply -f "$ROOT/infra/argocd/appproject-ai-scan-ci.yaml"
    k apply -n argocd -f "$ROOT/deploy/argocd/root-app.yaml"
  else
    log "deploy: kubectl apply -k deploy/<component>/overlays/$OVERLAY (no Argo CD)"
    for c in "${COMPONENTS[@]}"; do k apply -k "$ROOT/deploy/$c/overlays/$OVERLAY"; done
  fi
}

verify() {
  log "verify / smoke"
  local t="${VERIFY_TIMEOUT:-300s}"
  if [ "$MODE" = gitops ]; then
    k -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/ai-scan-root --timeout="$t"
  fi
  k -n "$APP_NS" rollout status statefulset/postgres --timeout="$t"
  for d in review-api gateway frontend; do k -n "$APP_NS" rollout status "deploy/$d" --timeout="$t"; done
  k -n "$APP_NS" exec deploy/review-api -- python -c \
    "import urllib.request as u; assert u.urlopen('http://127.0.0.1:8001/readyz', timeout=5).status == 200"
  k -n "$APP_NS" exec deploy/gateway -- python -c \
    "import urllib.request as u; assert u.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status == 200"
  dry || echo "  smoke ok"
}

# shellcheck disable=SC2329  # dispatched via "$CMD"
uninstall() {
  [ "$ASSUME_YES" = 1 ] || dry || die "uninstall needs --yes"
  log "uninstall (purge=$PURGE): Secrets, PVCs and namespaces are kept unless --purge"
  if [ "$MODE" = gitops ]; then
    k -n argocd delete application ai-scan-root --ignore-not-found --wait=true
    # child Applications carry resources-finalizer: deleting them removes their workloads (cascade). Without
    # --purge the platform app (reports PVC, ServiceAccounts) is orphaned first so its data survives.
    if [ "$PURGE" != 1 ]; then
      k -n argocd patch application ai-scan-platform-k3s --type merge -p '{"metadata":{"finalizers":null}}' || true
    fi
    k delete -n argocd -f "$ROOT/deploy/argocd/apps/" --ignore-not-found --wait=true
  fi
  local i
  for ((i=${#COMPONENTS[@]}-1; i>=1; i--)); do   # platform (PVCs, SA) last and only with --purge
    k delete -k "$ROOT/deploy/${COMPONENTS[$i]}/overlays/$OVERLAY" --ignore-not-found
  done
  if [ "$PURGE" = 1 ]; then
    k delete -k "$ROOT/deploy/platform/overlays/$OVERLAY" --ignore-not-found
    [ "$MODE" = gitops ] && run helm uninstall argocd -n argocd --ignore-not-found
    [ "$REGISTRY_TYPE" = local ] && k delete -f "$ROOT/infra/registry/registry.yaml" --ignore-not-found
    k delete -k "$ROOT/infra/ingress/overlays/$OVERLAY" --ignore-not-found
    k delete namespace "$APP_NS" registry argocd --ignore-not-found
    echo "  NOTE: cluster-scoped bootstrap (PVs/StorageClasses, namespaces jenkins/monitoring) left in place;"
    echo "        reports PV has reclaimPolicy Retain (docs/storage-plan.md)."
  fi
}

case "$CMD" in
  install) preflight; bootstrap; registry; secrets; images; deploy; [ "${SKIP_VERIFY:-0}" = 1 ] || verify ;;
  preflight|bootstrap|registry|secrets|images|deploy|verify|uninstall) "$CMD" ;;
  ecr-login) ecr_login ;;
  *) die "unknown command $CMD" ;;
esac
dry && echo "(dry-run: nothing was changed)"
exit 0
