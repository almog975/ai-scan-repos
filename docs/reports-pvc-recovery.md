# Reports PVC recovery across node restarts

> **Status: NOT VERIFIED YET.** The procedure below has not been run: K3S-SRV is not reachable from the
> build box. Run `infra/scripts/verify-reports-pvc-restart.sh` on/against K3S-SRV and record the result here.

## K3s: static local PV `ai-scan-reports-pv` (StorageClass `reports-local`)
Defined in `infra/bootstrap/overlays/k3s/reports-pv.yaml` (applied by the installer with `kubectl apply -k infra/bootstrap/overlays/k3s`, not by Argo CD; the PVC in `deploy/platform/overlays/k3s` pins it via `volumeName`): a `local:` volume at `/srv/ai-scan/reports`
(not `hostPath`, but same idea: a directory on the node's disk), pinned by `nodeAffinity` to one node,
`persistentVolumeReclaimPolicy: Retain` (already set; StorageClass also `reclaimPolicy: Retain`).

Why it survives `systemctl restart k3s` or a reboot:
1. **Data lives on the host disk**, outside any container/pod lifecycle.
2. **The mount is in `/etc/fstab`** (`noexec,nosuid,nodev`, set up by `install-ssh-k3s.sh`), so it is
   re-mounted on boot before k3s starts. (`mountOptions` on a local PV is not enforced by the kubelet for an
   existing dir; the fstab mount is what provides noexec.)
3. **PV/PVC binding is API state** stored in k3s' datastore (sqlite `/var/lib/rancher/k3s/server/db` or
   embedded etcd), so after restart the PVC `reports` is still `Bound` to the same PV; nodeAffinity schedules
   consumers back to the same node.

### Caveats
- `Retain` is mandatory: deleting the PVC must not wipe reports. After a PVC delete, the PV goes `Released`
  and needs `kubectl patch pv ai-scan-reports-pv -p '{"spec":{"claimRef":null}}'` to rebind.
- **Node loss = data loss.** Single node, single disk, no replication. Pods needing `/reports` stay
  `Pending` while the node is down.
- **Backups required**: e.g. nightly `restic`/`rsync` of `/srv/ai-scan/reports` off-node, plus k3s datastore
  snapshots (`k3s etcd-snapshot save` or copy of the sqlite db).
- If the fstab line is missing, the dir still exists but without noexec — the script checks this.
- `REPLACE_NODE_NAME` must be filled in before the PV can bind.

### Manual verification
```bash
NS=ai-scan-repos
kubectl -n $NS get pvc reports; kubectl get pv ai-scan-reports-pv      # expect Bound
# 1. write marker
kubectl -n $NS run pvc-write --rm -i --restart=Never --image=busybox:1.36 --overrides='{"spec":{"containers":[{"name":"c","image":"busybox:1.36","command":["sh","-c","date > /reports/.restart-marker; cat /reports/.restart-marker"],"volumeMounts":[{"name":"r","mountPath":"/reports"}]}],"volumes":[{"name":"r","persistentVolumeClaim":{"claimName":"reports"}}]}}'
# 2. restart (on K3S-SRV)
sudo systemctl restart k3s        # or: sudo reboot
# 3. wait for node
kubectl wait --for=condition=Ready node/<node> --timeout=600s
findmnt -no OPTIONS /srv/ai-scan/reports                              # on node: contains noexec
# 4. read marker + check binding
kubectl -n $NS run pvc-read --rm -i --restart=Never --image=busybox:1.36 --overrides='{"spec":{"containers":[{"name":"c","image":"busybox:1.36","command":["sh","-c","cat /reports/.restart-marker"],"volumeMounts":[{"name":"r","mountPath":"/reports"}]}],"volumes":[{"name":"r","persistentVolumeClaim":{"claimName":"reports"}}]}}'
kubectl -n $NS get pvc reports -o jsonpath='{.status.phase}'           # Bound
```
Automated: `MODE=k3s-restart NODE=<node> infra/scripts/verify-reports-pvc-restart.sh` (or `MODE=reboot`).

## EKS: EFS (`efs-sc`, RWX)
Data lives in EFS, independent of any node; node restarts/replacements just re-mount it (any AZ). Node loss
does not lose data. Use `Retain` on the StorageClass/PV and AWS Backup for the file system. Same marker test
applies, replacing the restart step with `kubectl drain` + terminating the node.
