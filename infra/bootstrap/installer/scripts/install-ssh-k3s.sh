#!/usr/bin/env bash
# Stub: prepare a remote host, install K3s, and deploy the k3s overlays.
set -euo pipefail
: "${SSH_HOST:?SSH_HOST required}"; SSH_USER="${SSH_USER:-ubuntu}"
REPORTS_DIR=/srv/ai-scan/reports
run() { echo "+ ssh $SSH_USER@$SSH_HOST $*"; [ "${DRY_RUN:-1}" = 1 ] || ssh -o BatchMode=yes "$SSH_USER@$SSH_HOST" "$@"; }

# 1. Host prep: reports dir as a noexec,nosuid,nodev bind mount (see docs/storage-plan.md)
run "sudo mkdir -p $REPORTS_DIR && \
  (grep -q ' $REPORTS_DIR ' /etc/fstab || echo '$REPORTS_DIR $REPORTS_DIR none bind,noexec,nosuid,nodev 0 0' | sudo tee -a /etc/fstab) && \
  (findmnt $REPORTS_DIR >/dev/null || sudo mount $REPORTS_DIR) && \
  sudo mount -o remount,bind,noexec,nosuid,nodev $REPORTS_DIR && \
  sudo chown 10001:10001 $REPORTS_DIR && sudo chmod 750 $REPORTS_DIR && findmnt -no OPTIONS $REPORTS_DIR"
# 2. K3s (bundled Traefik kept)
run "command -v k3s >/dev/null || curl -sfL https://get.k3s.io | sh -"
# 3. Cluster-scoped bootstrap (namespaces, reports PV + StorageClass) BEFORE Argo CD.
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../../.." && pwd)}"
: "${NODE_NAME:=$SSH_HOST}"
echo "+ kubectl apply -k infra/bootstrap/cluster/overlays/k3s (node $NODE_NAME)"
if [ "${DRY_RUN:-1}" != 1 ]; then
  kubectl kustomize "$ROOT/infra/bootstrap/cluster/overlays/k3s" | sed "s/REPLACE_NODE_NAME/$NODE_NAME/" | kubectl apply -f -
fi
# 4. Registry (htpasswd) + Jenkins/Argo CD IngressRoutes (secrets: docs/registry-auth-runbook.md, docs/tls-runbook.md)
echo "+ kubectl apply -f infra/bootstrap/registry/registry.yaml && kubectl apply -k infra/bootstrap/ingress/overlays/k3s"
[ "${DRY_RUN:-1}" = 1 ] || { kubectl apply -f "$ROOT/infra/bootstrap/registry/registry.yaml"; kubectl apply -k "$ROOT/infra/bootstrap/ingress/overlays/k3s"; }
# 5. TODO: copy registries.yaml, fetch kubeconfig
# 6. Optional: agent image (infra/bootstrap/installer/build-jenkins-agent.sh). 7. Argo CD + AppProjects (2a ai-scan, 2b ai-scan-ci) + root app:
"$ROOT/infra/bootstrap/installer/scripts/install-argocd.sh"
