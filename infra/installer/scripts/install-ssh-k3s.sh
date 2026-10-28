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
# 3. TODO: copy registries.yaml, fetch kubeconfig, set node name in reports PV, apply deploy/*/overlays/k3s
echo "TODO: deploy overlays (k3s)"
