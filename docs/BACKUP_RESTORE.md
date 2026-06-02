# Backup + Restore Runbook

Automated backups for Dash: PostgreSQL via `pg_dump`, knowledge volume via `tar`, all uploaded to S3 with retention sweep.

## Schedule

- **Daily 03:00 UTC**: full DB + knowledge volume → S3
- **Retention**: 30 daily (configurable via `BACKUP_RETENTION_DAILY`)
- **Storage class**: `STANDARD_IA` (cheaper, fine for backups)

Cron sources:
- Kubernetes: `k8s/70-backup-db-cronjob.yaml`
- Helm: `helm/dash/templates/backup-cronjobs.yaml` (values key `backup.cronJobs`)

The `learning-*` CronJobs (decay, daily learn, canary) keep their existing schedules; backup runs at `0 3 * * *`, learning daily at `0 4 * * *`.

## Required env / secrets

| Variable                | Where               | Notes                              |
|-------------------------|---------------------|-------------------------------------|
| `DB_HOST`               | configmap           | usually `dash-pgbouncer`            |
| `DB_USER`               | configmap           |                                     |
| `DB_DATABASE`           | configmap           |                                     |
| `DB_PASS`               | secret              |                                     |
| `BACKUP_S3_BUCKET`      | configmap           | e.g. `s3://dash-backups`            |
| `BACKUP_ENV`            | inline              | `prod` / `staging` / `dev`          |
| `BACKUP_TYPES`          | inline              | `db,knowledge` (default)            |
| `BACKUP_RETENTION_DAILY`| inline              | default 30                          |
| `KNOWLEDGE_DIR`         | inline              | default `/app/knowledge`            |
| `AWS_ACCESS_KEY_ID`     | secret              | or use IAM Role for SA              |
| `AWS_SECRET_ACCESS_KEY` | secret              | or use IAM Role for SA              |
| `AWS_REGION`            | configmap optional  |                                     |

## Apply migration

```bash
docker exec -i dash-db psql -U ai -d ai < db/migrations/007_backup_runs.sql
```

## Deploying scripts to k8s

The CronJob mounts `/scripts` from a ConfigMap built from the local files:

```bash
kubectl create configmap dash-backup-scripts -n dash \
  --from-file=scripts/backup.sh \
  --from-file=scripts/restore.sh \
  --dry-run=client -o yaml | kubectl apply -f -
```

Re-run after editing `scripts/*.sh`.

## Manual backup

```bash
# Inside cluster:
kubectl exec -n dash deploy/dash-api -- /scripts/backup.sh

# Locally (uses your AWS creds + DB env):
DB_HOST=localhost DB_USER=ai DB_PASS=ai DB_DATABASE=ai \
  BACKUP_S3_BUCKET=s3://dash-backups BACKUP_ENV=dev \
  ./scripts/backup.sh
```

## Manual restore

```bash
# Latest DB:
DB_HOST=... ./scripts/restore.sh db latest

# Specific file:
./scripts/restore.sh db dash_db_2026-05-06T03-00-00Z.sql.gz

# Both DB + knowledge:
./scripts/restore.sh all latest
```

The `db` restore uses `--clean --if-exists` (drops + recreates objects). The script sleeps 5s before destructive ops so you can Ctrl-C.

## Quarterly DR drill

1. Spin up staging cluster.
2. Run `restore.sh all latest` against staging DB + knowledge PVC.
3. Boot `dash-api`. Hit `/health` → expect 200, `last_backup_at` recent.
4. Smoke test: log in, open a project, run a chat query.
5. File outcome notes in `docs/OPS_TICKETS.md`.

## RPO / RTO

- **RPO**: 24h (daily backup; sub-hour requires WAL streaming — out of scope for this iteration)
- **RTO**: 1h (S3 download + `psql` restore for typical tenant)

## Audit trail

```sql
SELECT env, types, ts, success, error
  FROM public.dash_backup_runs
  ORDER BY ts DESC
  LIMIT 30;
```

## Health check

```bash
curl https://<host>/health
# {... "last_backup_at": "2026-05-06T03:00:12+00:00"}
```

Alert if `last_backup_at` is older than 25h or null.

Suggested Prometheus expression (if Prom is wired in):

```
time() - on() (max(dash_last_backup_unix)) > 90000  # 25h
```

## Restore from corrupted state

1. Stop writers: scale `dash-api` to 0 (`kubectl scale deploy/dash-api --replicas=0`).
2. Snapshot current state (paranoia): run `backup.sh` once with a `BACKUP_ENV=preserve-<incident>` prefix.
3. `restore.sh db <known_good_filename>`.
4. `restore.sh knowledge <known_good_filename>` only if knowledge corruption suspected.
5. Bring API back: `kubectl scale deploy/dash-api --replicas=3`.
6. Verify `/health`, recent chats, training runs.
7. Post-mortem in `docs/OPS_TICKETS.md`.

## Cross-region copy (DR)

Optional: configure S3 cross-region replication on `dash-backups` bucket, e.g. `us-east-1 → eu-west-1`. CRR is async; expect a few minutes lag. Failover region runs the same `restore.sh`, pointed at the replicated bucket.

## Cost notes

- Compressed pg_dump for a typical tenant: ~50–500 MB
- Knowledge tar: ~1–10 GB depending on uploaded docs
- 30 daily copies in `STANDARD_IA`: pennies to a few dollars per tenant per month
- LIST + GET ops during retention sweep: negligible
