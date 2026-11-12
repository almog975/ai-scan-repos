#!/usr/bin/env bash
# Local full stack on k3d (K3s in Docker) on Docker Desktop. Linux/macOS, or WSL2 on Windows.
#
#   infra/bootstrap/local/k3d.sh [--env FILE] [--no-argocd] [--dry-run] [--yes] up|down|status
#
#   up      k3d registry + cluster `ai-scan` (80/443 -> Traefik), build + push the 4 images (:dev), Prometheus Operator
#           CRDs (ServiceMonitor/PrometheusRule only), self-signed TLS, then infra/bootstrap/installer/aiscan-install.sh
#           --overlay local --mode direct (namespaces, secrets from .env, egress endpoints, deploy, verify), then
#           Argo CD (pinned) + infra/argocd/local root app. Idempotent: re-run after code changes.
#   down    delete the cluster and the registry (all data). Asks, or --yes.
#   status  cluster, pods, URLs.
#
# Secrets are read from --env (default: repo .env, the docker-compose file) and only written to the cluster through
# the installer's 0600 temp files; never printed. GITHUB_REPO_TOKEN (env only) = read-only token for Argo CD.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CLUSTER=ai-scan
REG_NAME=k3d-ai-scan-registry.localhost   # in-cluster name (k3d adds the k3d- prefix)
REG_PORT=5111
HOST=ai-scan.localhost
NS=ai-scan-repos
SERVICES=(frontend gateway review-api scanner-worker)
PROM_OPERATOR_VERSION=v0.81.0   # CRDs only (no Prometheus): the platform overlay ships ServiceMonitor/PrometheusRule
ARGOCD_CHART_VERSION=$(sed -n 's/^ARGOCD_CHART_VERSION_PINNED="\([^"]*\)".*/\1/p' "$ROOT/infra/bootstrap/installer/aiscan-install.sh")
REPO_URL=https://github.com/almog975/ai-scan-repos.git

ENV_FILE="$ROOT/.env"; ARGOCD=1; DRY=0; YES=0; CMD=""
die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --env) [ -n "${2:-}" ] || die "--env needs a file"; ENV_FILE="$2"; shift 2 ;;
    --no-argocd) ARGOCD=0; shift ;;
    --dry-run) DRY=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    up|down|status) [ -z "$CMD" ] || die "one command only"; CMD="$1"; shift ;;
    *) die "unknown argument $1 (try --help)" ;;
  esac
done
[ -n "$CMD" ] || die "command required: up|down|status"

dry() { [ "$DRY" = 1 ]; }
run() { echo "+ $*"; dry || "$@"; }
TMP=$(mktemp -d); chmod 700 "$TMP"; trap 'rm -rf "$TMP"' EXIT

prereqs() {
  local t missing=()
  for t in "$@"; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  dry && { echo "  (dry-run) missing tools: ${missing[*]}"; return 0; }
  die "missing tools: ${missing[*]}"
}

# kubeconfig of the k3d cluster only: never acts on another context
use_cluster() {
  export KUBECONFIG="$TMP/kubeconfig"
  echo "+ k3d kubeconfig get $CLUSTER > \$TMP/kubeconfig"
  dry || { ( umask 077; k3d kubeconfig get "$CLUSTER" > "$KUBECONFIG" ); chmod 600 "$KUBECONFIG"; }
}

up() {
  [ -f "$ENV_FILE" ] || dry || die "$ENV_FILE not found: cp .env.example .env and fill it"
  prereqs docker k3d kubectl kustomize htpasswd helm openssl
  dry || docker info >/dev/null 2>&1 || die "docker is not running (start Docker Desktop; on Windows enable WSL2 integration)"

  log "k3d registry $REG_NAME:$REG_PORT + cluster $CLUSTER (ports 80/443 -> Traefik)"
  if dry || ! k3d registry list -o json 2>/dev/null | grep -q "\"$REG_NAME\""; then
    run k3d registry create ai-scan-registry.localhost --port "$REG_PORT"
  else echo "  registry exists"; fi
  if dry || ! k3d cluster list -o json 2>/dev/null | grep -q "\"name\": *\"$CLUSTER\""; then
    run k3d cluster create "$CLUSTER" --registry-use "$REG_NAME:$REG_PORT" \
      -p "80:80@loadbalancer" -p "443:443@loadbalancer" --wait
  else echo "  cluster exists"; fi
  use_cluster

  log "images: build + push localhost:$REG_PORT/ai-scan/<svc>:dev (cluster pulls $REG_NAME:$REG_PORT/...)"
  local s
  for s in "${SERVICES[@]}"; do
    run docker build -t "localhost:$REG_PORT/ai-scan/$s:dev" "$ROOT/$s"
    run docker push "localhost:$REG_PORT/ai-scan/$s:dev"
  done

  log "CRDs: Prometheus Operator $PROM_OPERATOR_VERSION (ServiceMonitor, PrometheusRule only); wait for k3s Traefik CRDs"
  local crd base="https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/$PROM_OPERATOR_VERSION/example/prometheus-operator-crd"
  for crd in servicemonitors prometheusrules; do
    run kubectl apply --server-side -f "$base/monitoring.coreos.com_$crd.yaml"
  done
  echo "+ wait for crd/ingressroutes.traefik.io (k3s installs Traefik shortly after start)"
  if ! dry; then
    for _ in $(seq 1 60); do kubectl get crd ingressroutes.traefik.io >/dev/null 2>&1 && break; sleep 3; done
    kubectl wait --for=condition=established crd/ingressroutes.traefik.io crd/middlewares.traefik.io --timeout=60s >/dev/null
  fi

  log "TLS: self-signed $NS/ai-scan-tls for $HOST (kept if present)"
  run sh -c "kubectl create namespace $NS --dry-run=client -o yaml | kubectl apply -f -"
  if dry || ! kubectl -n "$NS" get secret ai-scan-tls >/dev/null 2>&1; then
    echo "+ openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj /CN=$HOST -addext subjectAltName=DNS:$HOST (key in \$TMP)"
    echo "+ kubectl -n $NS create secret tls ai-scan-tls --cert=... --key=... --dry-run=client -o yaml | kubectl apply -f -"
    if ! dry; then
      ( umask 077
        openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST" \
          -keyout "$TMP/tls.key" -out "$TMP/tls.crt" 2>/dev/null )
      kubectl -n "$NS" create secret tls ai-scan-tls --cert="$TMP/tls.crt" --key="$TMP/tls.key" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    fi
  fi

  log "installer: --overlay local --mode direct on a temp copy of infra/ (the working tree is never edited)"
  mkdir -p "$TMP/repo"; cp -r "$ROOT/infra" "$TMP/repo/"
  # egress endpoints from the live cluster; k3d = K3s default pod/service CIDRs
  if dry; then KUBE_API_ENDPOINT=192.0.2.1; KUBE_SERVICE_IP=10.43.0.1; else
    KUBE_API_ENDPOINT=$(kubectl get endpoints kubernetes -n default -o jsonpath='{.subsets[0].addresses[0].ip}')
    KUBE_SERVICE_IP=$(kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}')
  fi
  [ -n "$KUBE_API_ENDPOINT" ] && [ -n "$KUBE_SERVICE_IP" ] || die "cannot read the kubernetes endpoint/service IP"
  echo "  KUBE_API_ENDPOINT=$KUBE_API_ENDPOINT KUBE_SERVICE_IP=$KUBE_SERVICE_IP POD_CIDR=10.42.0.0/16 SERVICE_CIDR=10.43.0.0/16"
  export KUBE_API_ENDPOINT KUBE_SERVICE_IP POD_CIDR=10.42.0.0/16 SERVICE_CIDR=10.43.0.0/16
  export REGISTRY_HOST="$REG_NAME:$REG_PORT"
  local inst=("$TMP/repo/infra/bootstrap/installer/aiscan-install.sh" --overlay local --mode direct --registry local --tag dev)
  [ -f "$ENV_FILE" ] && inst+=(--config "$ENV_FILE")
  dry && inst+=(--dry-run)
  local step
  for step in preflight bootstrap secrets images egress; do ROOT="$TMP/repo" "${inst[@]}" "$step"; done
  # migrations Job: re-run on every `up` (new code may add migrations); the Job spec is immutable
  run kubectl -n "$NS" delete job review-api-migrate --ignore-not-found
  ROOT="$TMP/repo" "${inst[@]}" deploy
  run kubectl -n "$NS" wait --for=condition=complete job/review-api-migrate --timeout=300s
  # :dev was re-pushed: pull it again (imagePullPolicy Always)
  run kubectl -n "$NS" rollout restart deployment/review-api deployment/gateway deployment/frontend
  ROOT="$TMP/repo" "${inst[@]}" verify

  [ "$ARGOCD" = 1 ] && argocd
  urls
}

argocd() {
  log "Argo CD chart $ARGOCD_CHART_VERSION (pinned in aiscan-install.sh) + infra/argocd/local root app (syncs main)"
  [ -n "$ARGOCD_CHART_VERSION" ] || die "cannot read ARGOCD_CHART_VERSION_PINNED from the installer"
  run helm repo add argo https://argoproj.github.io/argo-helm --force-update
  run helm upgrade --install argocd argo/argo-cd -n argocd --create-namespace --version "$ARGOCD_CHART_VERSION" \
    --set 'configs.params.server\.insecure=true' --wait --timeout 10m
  if [ -n "${GITHUB_REPO_TOKEN:-}" ]; then
    local xtrace=0; [[ $- == *x* ]] && xtrace=1
    set +x   # never trace the token (same guard as ci/deploy/open-pr.sh)
    echo "+ kubectl -n argocd create secret generic repo-ai-scan-repos [keys: type url username password] | apply; label secret-type=repository"
    if ! dry; then
      ( umask 077; printf 'type=git\nurl=%s\nusername=x-access-token\npassword=%s\n' "$REPO_URL" "$GITHUB_REPO_TOKEN" > "$TMP/repo.env" )
      kubectl -n argocd create secret generic repo-ai-scan-repos --from-env-file="$TMP/repo.env" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
      kubectl -n argocd label --overwrite secret repo-ai-scan-repos argocd.argoproj.io/secret-type=repository >/dev/null
      rm -f "$TMP/repo.env"
    fi
    if [ "$xtrace" = 1 ]; then set -x; fi
  else
    echo "  WARNING: GITHUB_REPO_TOKEN unset: Argo CD cannot read the private repo (apps stay Unknown/ComparisonError)" >&2
  fi
  run kubectl apply -n argocd -f "$ROOT/infra/argocd/local/root-app.yaml"
}

urls() {
  cat <<MSG

  UI:       https://$HOST   (self-signed certificate: accept the browser warning)
            GitHub OAuth App callback: https://$HOST/auth/github/callback
  Argo CD:  kubectl -n argocd port-forward svc/argocd-server 8081:80   ->  http://localhost:8081  (user admin)
            password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
            Argo CD deploys GitHub main: local changes reach it only after they are merged.
  kubectl:  k3d kubeconfig merge $CLUSTER --kubeconfig-switch-context   (context k3d-$CLUSTER)
MSG
}

down() {
  prereqs k3d
  if [ "$YES" != 1 ] && ! dry; then
    [ -t 0 ] || die "down deletes cluster $CLUSTER and registry $REG_NAME (all data): re-run with --yes"
    local a; read -r -p "Delete k3d cluster $CLUSTER and registry $REG_NAME with ALL data? Type 'yes': " a
    [ "$a" = yes ] || die "aborted"
  fi
  run k3d cluster delete "$CLUSTER"
  run k3d registry delete "$REG_NAME"
}

status() {
  prereqs k3d kubectl
  run k3d cluster list
  run k3d registry list
  dry || k3d cluster list -o json 2>/dev/null | grep -q "\"name\": *\"$CLUSTER\"" || { echo "  cluster $CLUSTER not found"; return 0; }
  use_cluster
  run kubectl -n "$NS" get pods,jobs,pvc
  run kubectl -n argocd get applications --ignore-not-found
  urls
}

case "$CMD" in up) up ;; down) down ;; status) status ;; esac
dry && echo "(dry-run: nothing was changed)"
exit 0
