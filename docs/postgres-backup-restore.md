# Postgres backup & restore

**Status: NOT VERIFIED on a cluster yet** (written in Phase 9; run the restore drill below once on K3s before relying on it).

## What runs
- CronJob `postgres-backup` (ns `ai-scan-repos`, `deploy/postgres/base/backup-cronjob.yaml`), daily 02:30 Asia/Jerusalem,
  `concurrencyPolicy: Forbid`.
- `pg_dump --format=custom` of `$POSTGRES_DB` into PVC `postgres-backups` (10Gi; `local-path` on local/k3s, `gp3` on eks)
  as `<db>-<UTC timestamp>.dump`. Written to a `.partial` file, checked with `pg_restore --list`, then renamed.
- Retention: ConfigMap `postgres-backup-config` key `BACKUP_RETENTION_DAYS` (default 14). Older `*.dump` files are deleted.
- Runs as uid/gid 70 (postgres in `postgres:16-alpine`), read-only root FS, all caps dropped, seccomp RuntimeDefault,
  no SA token. NetworkPolicy `postgres-backup`: no ingress, egress only to `app=postgres:5432` and kube-dns.
- Credentials: existing Secret `db-secrets` (keys `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) mounted as files.
  No new secret; no values in git (see `docs/secrets-runbook.md`).

## Known limitation: no off-node copy
On K3s this is a **single node**: the `postgres-backups` PVC (local-path) lives on the same node and disk as
`postgres-data`. Backups protect against logical damage (bad migration, accidental deletes) but **not** against losing
the node or its disk. There is no automatic off-node copy today; copy dumps off manually (`kubectl cp`, rsync to another
host) if needed. On EKS, store backups off the instance: put `postgres-backups` on EFS (`efs-sc`) or add an upload
step to S3 (IRSA-scoped bucket) instead of relying on the gp3 volume in the same AZ.

## Run a backup now
```bash
kubectl -n ai-scan-repos create job --from=cronjob/postgres-backup postgres-backup-manual-$(date +%s)
kubectl -n ai-scan-repos logs -f job/<that job>
```

## List / copy dumps
```bash
kubectl -n ai-scan-repos run pgb-shell --rm -it --restart=Never --image=postgres:16-alpine \
  --overrides='{"spec":{"securityContext":{"runAsUser":70,"runAsGroup":70,"runAsNonRoot":true,"seccompProfile":{"type":"RuntimeDefault"}},
  "containers":[{"name":"s","image":"postgres:16-alpine","command":["sh"],"stdin":true,"tty":true,
  "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}},
  "volumeMounts":[{"name":"b","mountPath":"/backups"}]}],
  "volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"postgres-backups"}}]}}'
# inside: ls -lh /backups
```
(RWO: on multi-node clusters the pod must land on the node holding the volume.)

## Restore
1. Stop writers: `kubectl -n ai-scan-repos scale deploy/review-api deploy/gateway --replicas=0` and suspend the CronJob
   (`kubectl -n ai-scan-repos patch cronjob postgres-backup -p '{"spec":{"suspend":true}}'`).
2. Start a restore pod like the shell above, labelled `app=postgres-backup` (so the NetworkPolicies let it reach
   postgres), with Secret `db-secrets` mounted at `/db`.
3. Inside:
   ```sh
   export PGHOST=postgres PGUSER="$(cat /db/POSTGRES_USER)" PGPASSWORD="$(cat /db/POSTGRES_PASSWORD)" DB="$(cat /db/POSTGRES_DB)"
   pg_restore --list /backups/<file>.dump | head          # sanity
   pg_restore --clean --if-exists --no-owner -d "$DB" /backups/<file>.dump
   ```
4. Scale review-api/gateway back up, un-suspend the CronJob. Alembic version is part of the dump; if the image is newer,
   the `review-api-migrate` Job upgrades on the next sync.

## Restore drill (do once, then after Postgres major upgrades)
Restore the latest dump into a scratch DB (`createdb restore_test; pg_restore -d restore_test ...`), compare row counts of
the main tables with production, drop `restore_test`.
