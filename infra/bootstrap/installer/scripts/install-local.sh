#!/usr/bin/env bash
# Stub: install onto the current kube context (local K3s / k3d / Docker Desktop).
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
OVERLAY=local
command -v kubectl >/dev/null || { echo "kubectl required" >&2; exit 1; }
echo "+ kubectl apply -k $ROOT/infra/bootstrap/cluster/overlays/$OVERLAY"
[ "${DRY_RUN:-1}" = 1 ] || kubectl apply -k "$ROOT/infra/bootstrap/cluster/overlays/$OVERLAY"
echo "+ kubectl apply -f $ROOT/infra/bootstrap/registry/registry.yaml && kubectl apply -k $ROOT/infra/bootstrap/ingress/overlays/$OVERLAY"
[ "${DRY_RUN:-1}" = 1 ] || { kubectl apply -f "$ROOT/infra/bootstrap/registry/registry.yaml"; kubectl apply -k "$ROOT/infra/bootstrap/ingress/overlays/$OVERLAY"; }
for c in platform postgres scanner-worker review-api gateway frontend; do
  echo "+ kubectl apply -k $ROOT/infra/k8s/$c/overlays/$OVERLAY"
  [ "${DRY_RUN:-1}" = 1 ] || kubectl apply -k "$ROOT/infra/k8s/$c/overlays/$OVERLAY"
done
[ "${WITH_ARGOCD:-0}" = 1 ] && "$ROOT/infra/bootstrap/installer/scripts/install-argocd.sh"   # optional GitOps on local
echo "TODO: secrets, kube-prometheus-stack, registry"
