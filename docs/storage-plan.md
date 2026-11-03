# Storage plan

| PVC | Size | local | k3s | eks |
|---|---|---|---|---|
| `postgres-data` | 5Gi RWO | cluster default (Docker Desktop: hostpath, k3d: local-path) | local-path | gp3 (EBS CSI) |
| `reports` (mounted `/reports`) | 5Gi | cluster default RWO | static PV `reports-local` RWO, `/srv/ai-scan/reports` | EFS `efs-sc` RWX |
| clone dir `/work` | emptyDir sizeLimit 2Gi | same | same | same |

`reports` must be `noexec,nosuid,nodev`.

* **local**: local-path ignores `mountOptions`, so noexec is **not enforced** (dev only).
  Mitigation: pods run as uid 10001 with readOnlyRootFilesystem, drop ALL caps and no privilege escalation; nothing is ever executed from /reports.
* **k3s**: a static `local` PV (`infra/bootstrap/overlays/k3s/reports-pv.yaml`, applied by the installer, bound via PVC `volumeName`) on a host
  directory that is itself mounted noexec. Host prep, which `infra/installer/scripts/install-ssh-k3s.sh` does:
  ```bash
  mkdir -p /srv/ai-scan/reports
  # either a dedicated disk/LV:  /dev/vg0/reports /srv/ai-scan/reports ext4 noexec,nosuid,nodev 0 2
  # or a bind mount:
  echo '/srv/ai-scan/reports /srv/ai-scan/reports none bind,noexec,nosuid,nodev 0 0' >> /etc/fstab
  mount /srv/ai-scan/reports && mount -o remount,bind,noexec,nosuid,nodev /srv/ai-scan/reports
  chown 10001:10001 /srv/ai-scan/reports && chmod 750 /srv/ai-scan/reports
  findmnt /srv/ai-scan/reports   # verify the options
  ```
  Replace `REPLACE_NODE_NAME` in the PV's nodeAffinity. RWO with a single node means the
  scanner Jobs and review-api must schedule on that node, which the nodeAffinity enforces.
* **eks**: EFS CSI StorageClass with `mountOptions: [noexec, nosuid, nodev]`, access point uid/gid 10001.

## Retention (CronJob `reports-cleanup`)
`deploy/platform/base/reports-cleanup.yaml` runs daily at 03:00 Asia/Jerusalem and deletes top-level report
directories on the `reports` PVC older than `REPORT_RETENTION_DAYS` (ConfigMap `reports-retention`, default 30).
It runs as uid/gid 10001 with no SA token and a deny-all NetworkPolicy (no egress). Once a report is pruned,
review-api returns **410 Gone** for it (the review row still exists, the artifact doesn't), not 404.
