#!/usr/bin/env bash
# Stub: terraform EKS, then deploy eks overlays.
set -euo pipefail
cd "$(dirname "$0")/../terraform/eks"
echo "+ terraform init && terraform apply -var region=${AWS_REGION:-eu-central-1} -var cluster_name=${CLUSTER_NAME:-ai-scan}"
echo "TODO: aws eks update-kubeconfig, install Traefik + EFS CSI, apply deploy/*/overlays/eks"
