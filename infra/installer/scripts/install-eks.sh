#!/usr/bin/env bash
# Stub: terraform EKS, then deploy eks overlays.
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
cd "$(dirname "$0")/../terraform/eks"
echo "+ terraform init && terraform apply -var region=${AWS_REGION:-eu-central-1} -var cluster_name=${CLUSTER_NAME:-ai-scan}"
echo "TODO: aws eks update-kubeconfig, install Traefik + EFS CSI"
echo "+ kubectl apply -k $ROOT/infra/bootstrap/overlays/eks   # before Argo CD; set fs-REPLACE_ME first"
[ "${DRY_RUN:-1}" = 1 ] || kubectl apply -k "$ROOT/infra/bootstrap/overlays/eks"
echo "TODO: install Argo CD (infra/argocd/README.md), then apply deploy/*/overlays/eks via Argo CD"
