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
out=$(mktemp --suffix=.yaml)
if kustomize build deploy/jenkins/manifests > "$out"; then
  kubeconform -strict -summary -schema-location default "$out" | sed 's|^|deploy/jenkins/manifests: |' || rc=1
else echo "FAIL kustomize build deploy/jenkins/manifests"; rc=1; fi
rm -f "$out"
for f in deploy/argocd/root-app.yaml deploy/argocd/apps/*.yaml infra/registry/*.yaml infra/argocd/appproject-*.yaml; do
  kubeconform -strict -ignore-missing-schemas -schema-location default -schema-location "$CATALOG" "$f" || rc=1
done
exit $rc
