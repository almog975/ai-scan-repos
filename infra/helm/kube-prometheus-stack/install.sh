#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${KPS_VERSION:-65.1.1}"   # pin; bump deliberately
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo update prometheus-community >/dev/null
kubectl get ns monitoring >/dev/null 2>&1 || kubectl create ns monitoring
kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1 || {
  echo "Create secret first: kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-user=admin --from-literal=admin-password=..." >&2; exit 1; }
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --version "$VERSION" -f values.yaml ${GRAFANA_HOST:+--set "grafana.ingress.hosts[0]=$GRAFANA_HOST"} --wait
