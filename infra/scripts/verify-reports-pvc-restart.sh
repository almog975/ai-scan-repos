#!/usr/bin/env bash
# Verify the k3s reports PVC survives a node/k3s restart. NOT YET RUN (K3S-SRV unreachable from the build box).
# Usage: MODE=k3s-restart|reboot NODE=<node> ./verify-reports-pvc-restart.sh   (run where kubectl + ssh to NODE work)
set -euo pipefail
NS=ai-scan-repos; PVC=reports; MODE=${MODE:-k3s-restart}; NODE=${NODE:?set NODE}
MARKER="restart-check-$(date +%s)"
pod() { # $1=name $2=shell cmd
  kubectl -n "$NS" run "$1" --rm -i --restart=Never --image=busybox:1.36 --overrides='{
   "spec":{"securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},
   "containers":[{"name":"c","image":"busybox:1.36","command":["sh","-c","'"$2"'"],
   "volumeMounts":[{"name":"r","mountPath":"/reports"}]}],
   "volumes":[{"name":"r","persistentVolumeClaim":{"claimName":"'"$PVC"'"}}]}}'
}
echo "== before"; kubectl -n "$NS" get pvc "$PVC"; kubectl get pv ai-scan-reports-pv
pod pvc-write "echo $MARKER > /reports/.restart-marker && cat /reports/.restart-marker"
echo "== restarting ($MODE) on $NODE"
if [ "$MODE" = reboot ]; then ssh "$NODE" 'sudo reboot' || true; sleep 30
else ssh "$NODE" 'sudo systemctl restart k3s'; fi
until ssh -o ConnectTimeout=5 "$NODE" true 2>/dev/null; do sleep 5; done
kubectl wait --for=condition=Ready "node/$NODE" --timeout=600s
ssh "$NODE" 'findmnt -no OPTIONS /srv/ai-scan/reports' | grep -q noexec || { echo "FAIL: noexec mount missing"; exit 1; }
[ "$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.status.phase}')" = Bound ] || { echo "FAIL: PVC not Bound"; exit 1; }
GOT=$(pod pvc-read "cat /reports/.restart-marker" | head -1)
[ "$GOT" = "$MARKER" ] && echo "PASS: marker $MARKER survived" || { echo "FAIL: got '$GOT'"; exit 1; }
pod pvc-clean "rm -f /reports/.restart-marker" >/dev/null
