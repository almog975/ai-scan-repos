#!/usr/bin/env bash
# Stub: install onto the current kube context (local K3s / k3d / Docker Desktop).
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
OVERLAY=local
command -v kubectl >/dev/null || { echo "kubectl required" >&2; exit 1; }
echo "+ kubectl apply -k $ROOT/infra/bootstrap/overlays/$OVERLAY"
[ "${DRY_RUN:-1}" = 1 ] || kubectl apply -k "$ROOT/infra/bootstrap/overlays/$OVERLAY"
echo "+ kubectl apply -f $ROOT/infra/registry/registry.yaml && kubectl apply -k $ROOT/infra/ingress/overlays/$OVERLAY   # secrets: docs/registry-auth-runbook.md, docs/tls-runbook.md"
[ "${DRY_RUN:-1}" = 1 ] || { kubectl apply -f "$ROOT/infra/registry/registry.yaml"; kubectl apply -k "$ROOT/infra/ingress/overlays/$OVERLAY"; }
for c in platform postgres scanner-worker review-api gateway frontend; do
  echo "+ kubectl apply -k $ROOT/deploy/$c/overlays/$OVERLAY"
  [ "${DRY_RUN:-1}" = 1 ] || kubectl apply -k "$ROOT/deploy/$c/overlays/$OVERLAY"
done
echo "TODO: secrets (docs/secrets-runbook.md), kube-prometheus-stack, registry"
