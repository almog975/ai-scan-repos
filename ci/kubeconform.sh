#!/usr/bin/env bash
# Render every Kustomize overlay and validate the output with kubeconform (strict; CRDs via the datree catalog,
# skipped when no schema exists). Raw patch files are not validated on their own: only built overlays count.
set -euo pipefail
cd "$(dirname "$0")/.."
CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
rc=0
for o in deploy/*/overlays/*/ infra/*/overlays/*/; do
  out=$(mktemp --suffix=.yaml)
  if ! kustomize build "$o" > "$out"; then echo "FAIL kustomize build $o"; rc=1; continue; fi
  if kubeconform -strict -summary -ignore-missing-schemas -schema-location default -schema-location "$CATALOG" "$out" | sed "s|^|$o: |"; then :; else rc=1; fi
  rm -f "$out"
done
# Monitoring CRDs (ServiceMonitor, PrometheusRule) must really be validated: no -ignore-missing-schemas here.
out=$(mktemp --suffix=.yaml)
kustomize build deploy/platform/overlays/k3s | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d.get("apiVersion", "").startswith("monitoring.coreos.com/")]
assert {d["kind"] for d in docs} >= {"ServiceMonitor", "PrometheusRule"}, "monitoring CRs missing"
yaml.safe_dump_all(docs, sys.stdout)' > "$out" || rc=1
kubeconform -strict -summary -schema-location "$CATALOG" "$out" | sed 's|^|monitoring CRDs: |' || rc=1
rm -f "$out"
out=$(mktemp --suffix=.yaml)
if kustomize build deploy/jenkins/manifests > "$out"; then
  kubeconform -strict -summary -schema-location default "$out" | sed 's|^|deploy/jenkins/manifests: |' || rc=1
else echo "FAIL kustomize build deploy/jenkins/manifests"; rc=1; fi
rm -f "$out"
for f in deploy/argocd/root-app.yaml deploy/argocd/apps/*.yaml infra/registry/*.yaml infra/argocd/appproject-*.yaml; do
  kubeconform -strict -ignore-missing-schemas -schema-location default -schema-location "$CATALOG" "$f" || rc=1
done
exit $rc
