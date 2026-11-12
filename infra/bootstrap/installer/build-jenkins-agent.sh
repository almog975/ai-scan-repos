#!/usr/bin/env bash
# OPTIONAL installer step: build + push the Jenkins agent "tools" image from ci/agent/Dockerfile as
#   <REGISTRY>/ai-scan/jenkins-agent:<git-sha>
# then replace CHANGE_ME_AGENT_SHA in infra/jenkins/values.yaml (SED=1, default) or just print the tag to commit.
# Uses docker, else nerdctl. Log in first with the push user:
#   echo '<PUSH_PASSWORD>' | docker login <PUSH_REGISTRY> -u <PUSH_USER> --password-stdin
# The values.yaml change must be committed and pushed for Argo CD to pick it up.
set -euo pipefail
ROOT="${ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
# Runs on the installer host (outside the cluster). In-cluster pushes (Jenkins agents) use registry.registry:5000
# directly.
# REGISTRY = name in the image ref (what pods/kubelet use); PUSH_REGISTRY = where this host can reach it.
# k3s: registry.registry:5000 via the NodePort 127.0.0.1:30500 (run on a node; docker treats 127.0.0.0/8 as insecure).
REGISTRY="${REGISTRY:-registry.registry:5000}"
PUSH_REGISTRY="${PUSH_REGISTRY:-127.0.0.1:30500}"
SHA="${GIT_SHA:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
IMG="$REGISTRY/ai-scan/jenkins-agent:$SHA"
VALUES="$ROOT/infra/jenkins/values.yaml"
if command -v docker >/dev/null; then CLI=docker
elif command -v nerdctl >/dev/null; then CLI=nerdctl
else echo "need docker or nerdctl" >&2; exit 1; fi
[ -f "$ROOT/ci/agent/Dockerfile" ] || { echo "ci/agent/Dockerfile not found" >&2; exit 1; }
run() { echo "+ $*"; [ "${DRY_RUN:-0}" = 1 ] || "$@"; }
PUSH_IMG="$PUSH_REGISTRY/ai-scan/jenkins-agent:$SHA"
run "$CLI" build -t "$IMG" -t "$PUSH_IMG" -f "$ROOT/ci/agent/Dockerfile" "$ROOT/ci/agent"
if [ "$CLI" = nerdctl ]; then run nerdctl push --insecure-registry "$PUSH_IMG"; else run "$CLI" push "$PUSH_IMG"; fi
if [ "${SED:-1}" = 1 ] && grep -q CHANGE_ME_AGENT_SHA "$VALUES" 2>/dev/null; then
  # replace the whole ref (host too): values.yaml may still carry the old nip.io host
  run sed -i "s#image: [^ ]*/ai-scan/jenkins-agent:CHANGE_ME_AGENT_SHA#image: $IMG#" "$VALUES"
  echo "Updated $VALUES -> commit it."
else
  echo "Set in $VALUES: image: $IMG"
fi
