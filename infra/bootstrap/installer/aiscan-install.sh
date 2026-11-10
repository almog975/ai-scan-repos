#!/usr/bin/env bash
# AI Scan Repos installer (Phase 8). Non-interactive, idempotent, --dry-run prints the plan without touching
# the cluster, the cloud or the working tree. Docs: docs/installer.md.
#
#   aiscan-install.sh [--config FILE] [--dry-run] [--overlay k3s|eks|local] [--mode gitops|direct]
#                     [--registry local|ecr] [--tag TAG] [--yes] [--purge] [--rotate-secrets] COMMAND
#   COMMAND: install (= preflight bootstrap registry secrets images egress deploy verify) | preflight | bootstrap |
#            registry | secrets | images | egress | deploy | verify | ecr-login | uninstall
#
# Values come from (later wins): defaults < --config env file < environment < flags. Secret values are only
# ever read from the environment/config file and written to the cluster through 0600 temp files; they are
# never echoed, logged or written into the repo.
set -Eeuo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ARGOCD_CHART_VERSION_PINNED="10.11.6"   # argo/argo-cd chart -> Argo CD v3.5.6 (multi-source apps need >= 2.6)
APP_NS=ai-scan-repos
COMPONENTS=(platform postgres scanner-worker review-api gateway frontend)
IMAGES=(review-api gateway frontend)    # kustomize `images:` entries; scanner-worker goes via SCANNER_IMAGE

STEP=args
die() { echo "ERROR: [$STEP] $*" >&2; exit 1; }
# any failing kubectl/helm/aws/... command (set -e) names the step it broke
trap 'rc=$?; echo "ERROR: step \"$STEP\" failed (exit $rc) at line $LINENO: $BASH_COMMAND" >&2' ERR
log() { echo "==> $*"; }

# ---- arguments / config -------------------------------------------------------------------------------------
CONFIG="" ; CMD="" ; declare -A FLAG=()
needarg() { [ $# -ge 2 ] && [ -n "$2" ] && [ "${2#-}" = "$2" ] || die "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --config|--overlay|--mode|--registry|--tag|--confirm-purge) needarg "$@" ;;&
    --config) CONFIG="$2"; shift 2 ;;
    --dry-run) FLAG[DRY_RUN]=1; shift ;;
    --overlay) FLAG[OVERLAY]="$2"; shift 2 ;;
    --mode) FLAG[MODE]="$2"; shift 2 ;;
    --registry) FLAG[REGISTRY_TYPE]="$2"; shift 2 ;;
    --tag) FLAG[IMAGE_TAG]="$2"; shift 2 ;;
    --yes) FLAG[ASSUME_YES]=1; shift ;;
    --purge) FLAG[PURGE]=1; shift ;;
    --confirm-purge) FLAG[CONFIRM_PURGE]="$2"; shift 2 ;;
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
    k="${line%%=*}"; v="${line#*=}"
    v="${v%%[[:space:]]#*}"; v="${v%"${v##*[![:space:]]}"}"   # strip a trailing ' # comment' and blanks
    v="${v%\"}"; v="${v#\"}"
    [ -n "${!k+x}" ] || export "$k=$v"
  done < "$CONFIG"
fi
for k in "${!FLAG[@]}"; do export "$k=${FLAG[$k]}"; done

DRY_RUN="${DRY_RUN:-0}"; OVERLAY="${OVERLAY:-k3s}"; MODE="${MODE:-gitops}"; REGISTRY_TYPE="${REGISTRY_TYPE:-local}"
IMAGE_TAG="${IMAGE_TAG:-GIT_SHA_PLACEHOLDER}"; ASSUME_YES="${ASSUME_YES:-0}"; PURGE="${PURGE:-0}"
ROTATE_SECRETS="${ROTATE_SECRETS:-0}"; CONFIRM_PURGE="${CONFIRM_PURGE:-}"; ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-$ARGOCD_CHART_VERSION_PINNED}"
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
    k3s) REGISTRY_HOST="${REGISTRY_HOST:-registry.registry:5000}" ;;   # in-cluster Service, plain HTTP
    local) REGISTRY_HOST="${REGISTRY_HOST:-k3d-ai-scan-registry.localhost:5111}" ;;   # k3d registry (infra/local/k3d.sh)
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
# gen_var VAR BYTES: set VAR to a random hex value unless already non-empty. Aborts if the generator fails or
# returns nothing (no `${X:=$(gen)}`: that swallows the error and leaves an empty password). Never printed.
gen_var() {
  local var=$1 val
  [ -z "${!var:-}" ] || return 0
  if dry; then val="<generated>"; else
    val=$(openssl rand -hex "$2") || die "generating $var failed (openssl rand)"
  fi
  [[ "$val" =~ ^[0-9a-f]{16,}$|^\<generated\>$ ]] || die "generating $var produced no usable value"
  printf -v "$var" '%s' "$val"; export "${var?}"
}
# exists NS KIND NAME: 0 = exists, 1 = NotFound. Any other error (RBAC, API down, timeout) aborts: treating it
# as "absent" would overwrite a live Secret (ai-keys-master!).
exists() {
  dry && return 1
  local err rc=0
  err=$("$KUBECTL" -n "$1" get "$2" "$3" 2>&1 >/dev/null) || rc=$?
  [ "$rc" = 0 ] && return 0
  # only the API's NotFound for exactly this object counts: e.g. 'Error from server (NotFound): secrets "x" not found'.
  # A missing context/namespace/CRD ("context \"x\" not found", "the server could not find …") is an error.
  local plural=$2; case "$2" in secret) plural=secrets ;; deploy|deployment) plural=deployments.apps ;; crd) plural=customresourcedefinitions.apiextensions.k8s.io ;; esac
  if [[ "$err" == *"(NotFound)"* && ( "$err" == *"$plural \"$3\" not found"* || "$err" == *"${plural%%.*} \"$3\" not found"* ) ]]; then return 1; fi
  die "cannot check $2 $1/$3 (kubectl get failed: ${err:-exit $rc}); refusing to guess"
}
# writes NS NAME: does the installer (re)write this Secret? absent, or present and --rotate-secrets
writes() { ! exists "$1" secret "$2" || [ "$ROTATE_SECRETS" = 1 ]; }

# apply_secret NS NAME TYPE KEY=ENVVAR...   (values from env; prints key names only)
apply_secret() {
  local ns=$1 name=$2 type=$3; shift 3
  local keys=() f="$TMP/$name.env"
  : > "$f"; chmod 600 "$f"
  if ! writes "$ns" "$name"; then
    echo "  secret $ns/$name exists: kept (use --rotate-secrets to replace)"; return 0
  fi
  local kv var opt
  for kv in "$@"; do
    var="${kv#*=}"; opt=0; [ "${var%\?}" != "$var" ] && { opt=1; var="${var%\?}"; }
    keys+=("${kv%%=*}")
    [ "$opt" = 1 ] || [ -n "${!var:-}" ] || die "refusing to create $ns/$name: ${kv%%=*} is empty"
    printf '%s=%s\n' "${kv%%=*}" "${!var:-}" >> "$f"
  done
  echo "+ $KUBECTL -n $ns create secret $type $name [keys: ${keys[*]}] --dry-run=client -o yaml | $KUBECTL apply -f -"
  dry && return 0
  "$KUBECTL" -n "$ns" create secret "$type" "$name" --from-env-file="$f" --dry-run=client -o yaml | "$KUBECTL" apply -f - >/dev/null
}

need_env() { for v in "$@"; do [ -n "${!v:-}" ] || die "$v is required (env or --config)"; done; }

# ---- steps --------------------------------------------------------------------------------------------------
preflight() {
  STEP=preflight
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
  if [ "$MODE" = direct ]; then
    # platform overlays ship ServiceMonitor/PrometheusRule: without the prometheus-operator CRDs `apply -k` fails
    # half-way. In gitops mode Argo CD's sync-waves order this (monitoring app first).
    local crd
    for crd in servicemonitors.monitoring.coreos.com prometheusrules.monitoring.coreos.com; do
      exists default crd "$crd" || die "CRD $crd missing: install the prometheus-operator CRDs (kube-prometheus-stack) before --mode direct, see docs/installer.md#monitoring-crds"
    done
  fi
  if [ "$REGISTRY_TYPE" = ecr ]; then aws sts get-caller-identity >/dev/null || die "aws credentials not usable"; fi
  echo "  ok"
}

bootstrap() {
  STEP=bootstrap
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
  STEP=registry
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
  if writes registry registry-htpasswd; then
    echo "+ htpasswd -iBn <push> / <pull> (password on stdin) > \$TMP/htpasswd; $KUBECTL -n registry create secret generic registry-htpasswd --from-file=htpasswd | apply"
    if ! dry; then
      umask 077
      printf '%s' "$REGISTRY_PUSH_PASSWORD" | htpasswd -iBn "$REGISTRY_PUSH_USER" > "$TMP/htpasswd"
      printf '%s' "$REGISTRY_PULL_PASSWORD" | htpasswd -iBn "$REGISTRY_PULL_USER" >> "$TMP/htpasswd"
      [ -s "$TMP/htpasswd" ] || die "htpasswd produced no output"
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
  STEP=ecr-login
  log "ecr-login: refresh regcred for $REGISTRY_HOST"
  [ "$REGISTRY_TYPE" = ecr ] || die "ecr-login needs --registry ecr"
  echo "+ aws ecr get-login-password --region $AWS_REGION  (token not printed)"
  if dry; then regcred "$REGISTRY_HOST" AWS "<token>"; return 0; fi
  local tok; tok=$(aws ecr get-login-password --region "$AWS_REGION") || die "aws ecr get-login-password failed"
  [ -n "$tok" ] || die "empty ECR token"
  regcred "$REGISTRY_HOST" AWS "$tok"
}

secrets() {
  STEP=secrets
  log "secrets (ns $APP_NS): never committed; existing ones are kept unless --rotate-secrets"
  need_env GITHUB_CLIENT_ID GITHUB_CLIENT_SECRET
  : "${POSTGRES_USER:=aiscan}" "${POSTGRES_DB:=aiscan}"
  : "${AI_PROVIDER:=none}" "${AI_MODEL:=}" "${AI_BASE_URL:=}" "${AI_API_KEY:=}"
  export POSTGRES_USER POSTGRES_DB AI_PROVIDER AI_MODEL AI_BASE_URL AI_API_KEY
  [[ "$POSTGRES_USER" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "bad POSTGRES_USER"
  local rotated=()
  # values are generated only for Secrets that are actually (re)written; a generator failure aborts
  if writes "$APP_NS" gateway-secrets; then
    if [ "$ROTATE_SECRETS" = 1 ] && exists "$APP_NS" secret gateway-secrets && [ -z "${GITHUB_WEBHOOK_SECRET:-}" ]; then
      echo "  WARNING: GITHUB_WEBHOOK_SECRET is rotated: update the GitHub App webhook secret now, deliveries fail until then" >&2
    fi
    gen_var GITHUB_WEBHOOK_SECRET 32
    apply_secret "$APP_NS" gateway-secrets generic GITHUB_CLIENT_ID=GITHUB_CLIENT_ID \
      GITHUB_CLIENT_SECRET=GITHUB_CLIENT_SECRET GITHUB_WEBHOOK_SECRET=GITHUB_WEBHOOK_SECRET
    rotated+=(gateway)
  else apply_secret "$APP_NS" gateway-secrets generic; fi
  if writes "$APP_NS" db-secrets; then
    local db_existed=0; exists "$APP_NS" secret db-secrets && db_existed=1
    gen_var POSTGRES_PASSWORD 24
    DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB"; export DATABASE_URL
    # rotating: change the role password in the running database FIRST, then the Secret (else the apps lock out)
    [ "$db_existed" = 1 ] && pg_rotate
    apply_secret "$APP_NS" db-secrets generic POSTGRES_USER=POSTGRES_USER POSTGRES_PASSWORD=POSTGRES_PASSWORD \
      POSTGRES_DB=POSTGRES_DB DATABASE_URL=DATABASE_URL
    rotated+=(review-api)
  else apply_secret "$APP_NS" db-secrets generic; fi
  if writes "$APP_NS" scanner-secrets; then
    gen_var INTERNAL_API_TOKEN 32
    [ "${#INTERNAL_API_TOKEN}" -ge 32 ] || dry || die "INTERNAL_API_TOKEN must be >= 32 chars"
    apply_secret "$APP_NS" scanner-secrets generic INTERNAL_API_TOKEN=INTERNAL_API_TOKEN AI_PROVIDER=AI_PROVIDER \
      AI_MODEL=AI_MODEL? AI_BASE_URL=AI_BASE_URL? AI_API_KEY=AI_API_KEY?
    rotated+=(review-api gateway)
  else apply_secret "$APP_NS" scanner-secrets generic; fi
  # ai-keys-master encrypts every stored provider key: NEVER replaced by the installer (rotation = re-encrypt first)
  if exists "$APP_NS" secret ai-keys-master; then
    echo "  secret $APP_NS/ai-keys-master exists: kept (never rotated by the installer, see docs/secrets-runbook.md)"
  else
    gen_var AI_KEYS_ENCRYPTION_KEY 32
    local save=$ROTATE_SECRETS; ROTATE_SECRETS=1
    apply_secret "$APP_NS" ai-keys-master generic AI_KEYS_ENCRYPTION_KEY=AI_KEYS_ENCRYPTION_KEY
    ROTATE_SECRETS=$save
  fi
  if [ -n "${GRAFANA_ADMIN_PASSWORD:-}" ]; then
    : "${GRAFANA_ADMIN_USER:=admin}"; export GRAFANA_ADMIN_USER
    apply_secret monitoring grafana-admin generic admin-user=GRAFANA_ADMIN_USER admin-password=GRAFANA_ADMIN_PASSWORD
  fi
  [ "$ROTATE_SECRETS" = 1 ] && [ ${#rotated[@]} -gt 0 ] && restart_apps "${rotated[@]}"
  return 0
}

# ALTER USER in the running postgres; the SQL (with the new password) goes over stdin, never argv
pg_rotate() {
  echo "+ $KUBECTL -n $APP_NS exec -i statefulset/postgres -- psql -U $POSTGRES_USER -d $POSTGRES_DB  (ALTER USER via stdin)"
  dry && return 0
  local q="${POSTGRES_PASSWORD//\'/\'\'}"
  printf "ALTER USER \"%s\" WITH PASSWORD '%s';\n" "$POSTGRES_USER" "$q" \
    | "$KUBECTL" -n "$APP_NS" exec -i statefulset/postgres -- psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null \
    || die "ALTER USER failed: db-secrets NOT changed (postgres must be running to rotate POSTGRES_PASSWORD)"
}

# restart deployments that read rotated Secrets (only those that exist)
restart_apps() {
  local d
  for d in $(printf '%s\n' "$@" | sort -u); do
    if exists "$APP_NS" deploy "$d"; then k -n "$APP_NS" rollout restart "deploy/$d"
    else echo "  deploy/$d not present: nothing to restart"; fi
  done
}

# egress: AI_EGRESS_CIDRS (comma list), KUBE_API_ENDPOINT and KUBE_SERVICE_IP replace the NetworkPolicy placeholders in
# deploy/platform/overlays/$OVERLAY/kustomization.yaml and deploy/scanner-worker/overlays/$OVERLAY/networkpolicy-ai-egress.yaml
# (lines tagged "(installer: VAR)"). Unset = placeholder kept (warning). Working tree only, like images.
cidr_ok() {
  local c=$1 ip m o
  [[ "$c" =~ ^([0-9]{1,3}(\.[0-9]{1,3}){3})/([0-9]{1,2})$ ]] || return 1
  ip=${BASH_REMATCH[1]}; m=${BASH_REMATCH[3]}
  [ "$m" -ge 8 ] && [ "$m" -le 32 ] || return 1   # never /0../7: that would open egress to the internet
  for o in ${ip//./ }; do [ "$o" -le 255 ] || return 1; done
}
ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }
# cidr_overlap A B: true when the two IPv4 CIDRs share any address
cidr_overlap() {
  local a=${1%/*} am=${1#*/} b=${2%/*} bm=${2#*/} m mask
  m=$(( am < bm ? am : bm )); mask=$(( m == 0 ? 0 : (0xFFFFFFFF << (32 - m)) & 0xFFFFFFFF ))
  [ $(( $(ip2int "$a") & mask )) -eq $(( $(ip2int "$b") & mask )) ]
}
# cluster ranges an AI CIDR must not overlap (it would open scanner egress to in-cluster pods/Services).
# Defaults: k3s/local 10.42.0.0/16 + 10.43.0.0/16; eks VPC CNI pods = VPC 10.40.0.0/16, Services 172.20.0.0/16.
cluster_cidrs() {
  case "$OVERLAY" in
    eks) echo "${POD_CIDR:-10.40.0.0/16} ${SERVICE_CIDR:-172.20.0.0/16}" ;;
    *) echo "${POD_CIDR:-10.42.0.0/16} ${SERVICE_CIDR:-10.43.0.0/16}" ;;
  esac
}
egress() {
  STEP=egress
  log "egress: NetworkPolicy endpoints (overlay $OVERLAY)"
  local plat="$ROOT/deploy/platform/overlays/$OVERLAY/kustomization.yaml"
  local scan="$ROOT/deploy/scanner-worker/overlays/$OVERLAY/networkpolicy-ai-egress.yaml"
  local list="" c cc ip var
  if [ -n "${AI_EGRESS_CIDRS:-}" ]; then
    for c in ${AI_EGRESS_CIDRS//,/ }; do
      cidr_ok "$c" || die "AI_EGRESS_CIDRS: bad or too broad CIDR '$c' (IPv4 a.b.c.d/8..32)"
      for cc in $(cluster_cidrs); do
        cidr_ok "$cc" || die "POD_CIDR/SERVICE_CIDR: bad CIDR '$cc'"
        ! cidr_overlap "$c" "$cc" || die "AI_EGRESS_CIDRS: '$c' overlaps cluster range $cc (POD_CIDR/SERVICE_CIDR)"
      done
      list+="${list:+, }{ipBlock: {cidr: $c}}"
    done
    echo "+ set AI egress [$AI_EGRESS_CIDRS] in deploy/{platform,scanner-worker}/overlays/$OVERLAY"
    if ! dry; then
      sed -i "s|value: \[.*\]}\( *# .*(installer: AI_EGRESS_CIDRS)\)$|value: [$list]}\1|" "$plat"
      sed -i "s|to: \[.*\]\( *# .*(installer: AI_EGRESS_CIDRS)\)$|to: [$list]\1|" "$scan"
    fi
  else echo "  WARNING: AI_EGRESS_CIDRS unset: AI egress keeps the RFC 5737 placeholder (AI calls are blocked)" >&2; fi
  for var in KUBE_API_ENDPOINT KUBE_SERVICE_IP; do
    if [ -z "${!var:-}" ]; then echo "  WARNING: $var unset: placeholder kept (review-api cannot reach the K8s API)" >&2; continue; fi
    ip="${!var%/32}"
    cidr_ok "$ip/32" || die "$var must be an IPv4 address (optionally /32)"
    echo "+ set $var=$ip/32 in deploy/platform/overlays/$OVERLAY/kustomization.yaml"
    dry || sed -i "s|cidr, value: [0-9./]*}\( *# .*(installer: $var)\)$|cidr, value: $ip/32}\1|" "$plat"
  done
  if ! dry; then
    for var in AI_EGRESS_CIDRS KUBE_API_ENDPOINT KUBE_SERVICE_IP; do
      grep -q "(installer: $var)" "$plat" || die "$plat lost its (installer: $var) marker"
    done
  fi
}

# Rewrites image registry/tag in deploy/<c>/overlays/$OVERLAY in the working tree. Never commits or pushes:
# in gitops mode Argo CD deploys what is on main, so commit the diff through a PR (the CI tag-bump push is a stub).
images() {
  STEP=images
  log "images: $IMAGE_PREFIX/<component>:$IMAGE_TAG (overlay $OVERLAY)"
  for c in "${IMAGES[@]}"; do
    # targeted rewrite of the existing images: entry (kustomize edit reorders/reformats the whole file)
    local kf="$ROOT/deploy/$c/overlays/$OVERLAY/kustomization.yaml"
    echo "+ set image ai-scan/$c=$IMAGE_PREFIX/$c:$IMAGE_TAG in deploy/$c/overlays/$OVERLAY/kustomization.yaml"
    dry && continue
    grep -qE "^  - name: ai-scan/$c\r?$" "$kf" || die "$kf has no images: entry for ai-scan/$c"
    sed -i "/^  - name: ai-scan\/$c\r\?$/,/newTag:/{s|newName: .*|newName: $IMAGE_PREFIX/$c|;s|newTag: .*|newTag: $IMAGE_TAG|}" "$kf"
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
  STEP=deploy
  if [ "$MODE" = gitops ]; then
    log "deploy: Argo CD $ARGOCD_CHART_VERSION (pinned) + AppProjects + app-of-apps"
    run helm repo add argo https://argoproj.github.io/argo-helm --force-update
    run helm upgrade --install argocd argo/argo-cd -n argocd --version "$ARGOCD_CHART_VERSION" \
      --set 'configs.params.server\.insecure=true' --wait --timeout 10m
    k -n argocd patch cm argocd-cm --type merge --patch-file "$ROOT/infra/argocd/argocd-cm-health-patch.yaml"
    k apply -f "$ROOT/infra/argocd/appproject-ai-scan.yaml"
    k apply -f "$ROOT/infra/argocd/appproject-ai-scan-ci.yaml"
    k apply -f "$ROOT/infra/argocd/autoheal-rbac.yaml"
    k apply -n argocd -f "$ROOT/deploy/argocd/root-app.yaml"
  else
    log "deploy: kubectl apply -k deploy/<component>/overlays/$OVERLAY (no Argo CD)"
    for c in "${COMPONENTS[@]}"; do k apply -k "$ROOT/deploy/$c/overlays/$OVERLAY"; done
  fi
}

verify() {
  STEP=verify
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

# shellcheck disable=SC2329  # called from uninstall
# delete_kept DIR: delete everything an overlay builds EXCEPT PersistentVolumeClaims/PersistentVolumes
delete_kept() {
  local f; f="$TMP/delete-$(basename "$(dirname "$(dirname "$1")")").yaml"
  echo "+ kustomize build ${1#"$ROOT"/} minus PersistentVolumeClaim/PersistentVolume > $f; $KUBECTL delete -f $f --ignore-not-found"
  dry && return 0
  kustomize build "$1" | awk 'function out() { if (doc != "" && !pv) printf "---\n%s", doc; doc=""; pv=0 }
    /^---$/ {out(); next} /^kind: PersistentVolume(Claim)?$/ {pv=1} {doc=doc $0 "\n"} END {out()}' > "$f"
  if grep -qE '^kind: PersistentVolume\r?$' "$f"; then die "internal: PVC left in delete set $f"; fi
  [ -s "$f" ] || return 0
  "$KUBECTL" delete -f "$f" --ignore-not-found
}

# shellcheck disable=SC2329  # dispatched via "$CMD"
uninstall() {
  STEP=uninstall
  [ "$ASSUME_YES" = 1 ] || dry || die "uninstall needs --yes"
  if [ "$PURGE" = 1 ] && ! dry && [ "$CONFIRM_PURGE" != "$APP_NS" ]; then
    die "--purge deletes the app data in namespace $APP_NS (PVCs postgres-data, reports, postgres-backups; all Secrets incl. ai-keys-master), Argo CD, and the registry incl. its registry-data PVC (pushed images); it keeps namespaces jenkins and monitoring (incl. jenkins-home) and cluster-scoped PVs/StorageClasses. Repeat with --confirm-purge $APP_NS"
  fi
  log "uninstall (purge=$PURGE): PVCs (postgres-data, reports, postgres-backups), Secrets and namespaces are kept unless --purge"
  if [ "$MODE" = gitops ]; then
    k -n argocd delete application ai-scan-root --ignore-not-found --wait=true
    if [ "$PURGE" != 1 ]; then
      # child Applications carry resources-finalizer (cascade delete). Drop it on every app so deleting the
      # Application orphans its resources; workloads are then removed below without the PVCs. The PVCs also carry
      # argocd.argoproj.io/sync-options: Delete=false,Prune=false as a second guard.
      local app apps=application/ai-scan-platform-k3s
      echo "+ for app in \$($KUBECTL -n argocd get applications -o name): $KUBECTL -n argocd patch \$app finalizers=null"
      dry || apps=$("$KUBECTL" -n argocd get applications -o name) || die "cannot list Argo CD applications"
      dry && apps=""
      for app in $apps; do
        k -n argocd patch "$app" --type merge -p '{"metadata":{"finalizers":null}}'
      done
    fi
    k delete -n argocd -f "$ROOT/deploy/argocd/apps/" --ignore-not-found --wait=true
  fi
  local i
  if [ "$PURGE" = 1 ]; then
    for ((i=${#COMPONENTS[@]}-1; i>=0; i--)); do
      k delete -k "$ROOT/deploy/${COMPONENTS[$i]}/overlays/$OVERLAY" --ignore-not-found
    done
    k -n "$APP_NS" delete pvc --all --ignore-not-found
    [ "$MODE" = gitops ] && run helm uninstall argocd -n argocd --ignore-not-found
    [ "$REGISTRY_TYPE" = local ] && k delete -f "$ROOT/infra/registry/registry.yaml" --ignore-not-found
    k delete -k "$ROOT/infra/ingress/overlays/$OVERLAY" --ignore-not-found
    k delete namespace "$APP_NS" registry argocd --ignore-not-found
    echo "  NOTE: cluster-scoped bootstrap (PVs/StorageClasses, namespaces jenkins/monitoring) left in place;"
    echo "        reports PV has reclaimPolicy Retain (docs/storage-plan.md)."
  else
    # platform keeps ServiceAccounts/RBAC and the reports PVC: only removed with --purge
    for ((i=${#COMPONENTS[@]}-1; i>=1; i--)); do delete_kept "$ROOT/deploy/${COMPONENTS[$i]}/overlays/$OVERLAY"; done
    echo "  kept: PVCs in $APP_NS (postgres-data, reports, postgres-backups), Secrets, namespaces; reinstall reuses them"
  fi
}

case "$CMD" in
  install) preflight; bootstrap; registry; secrets; images; egress; deploy; [ "${SKIP_VERIFY:-0}" = 1 ] || verify ;;
  preflight|bootstrap|registry|secrets|images|egress|deploy|verify|uninstall) "$CMD" ;;
  ecr-login) ecr_login ;;
  *) die "unknown command $CMD" ;;
esac
dry && echo "(dry-run: nothing was changed)"
exit 0
