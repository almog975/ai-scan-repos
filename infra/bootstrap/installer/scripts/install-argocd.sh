#!/usr/bin/env bash
# Install Argo CD (pinned; multi-source Applications need >= 2.6) and bootstrap the app-of-apps.
# Order: 1 Argo CD, 1b argocd-cm health patch, 2a AppProject ai-scan, 2b AppProject ai-scan-ci, 3 root app.
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-10.11.6}"   # argo/argo-cd chart -> Argo CD v3.5.6
run() { echo "+ $*"; [ "${DRY_RUN:-1}" = 1 ] || "$@"; }
run helm repo add argo https://argoproj.github.io/argo-helm
run helm upgrade --install argocd argo/argo-cd -n argocd --version "$ARGOCD_CHART_VERSION" \
  -f "$ROOT/infra/argocd/values.yaml" --wait   # server.insecure + Phase 9 resources
run kubectl -n argocd patch cm argocd-cm --type merge --patch-file "$ROOT/infra/argocd/argocd-cm-health-patch.yaml"
echo "# repo credentials (if private)"
run kubectl apply -f "$ROOT/infra/argocd/appproject-ai-scan.yaml"       # 2a
run kubectl apply -f "$ROOT/infra/argocd/appproject-ai-scan-ci.yaml"    # 2b
run kubectl apply -n argocd -f "$ROOT/infra/argocd/root-app.yaml"      # 3
