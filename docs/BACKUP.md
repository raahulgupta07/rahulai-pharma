# Backup & Restore

Nightly `pg_dump` runs at 03:00 UTC inside the `dash-backup` compose service.
Dumps land in the `backup_data` named volume mounted at `/var/backups/dash`
as `dash_YYYYMMDD.sql.gz`. 7-day retention by default
(`BACKUP_RETENTION_DAYS` env).

After every successful dump, the daemon updates
`public.dash_system_status.last_backup_at` so `/health` exposes
`last_backup_at` and operators can alert on backup staleness.

## List backups

```bash
docker exec dash-backup ls -lh /var/backups/dash
```

## Manual backup (out-of-band)

```bash
docker exec -e BACKUP_RUN_ONCE=1 dash-backup \
    sh -c "apk add --no-cache python3 >/dev/null && python3 /opt/backup/backup_daemon.py"
```

## Restore

1. Copy the dump out of the volume to the host:

   ```bash
   docker cp dash-backup:/var/backups/dash/dash_YYYYMMDD.sql.gz ./
   ```

2. Stop the API so nothing writes during restore:

   ```bash
   docker compose stop dash-api dash-ml dash-pgbouncer
   ```

3. Drop and recreate the database, then load the dump (DESTRUCTIVE):

   ```bash
   gunzip -c dash_YYYYMMDD.sql.gz | \
       docker exec -i dash-db psql -U ${DB_USER:-ai} -d ${DB_DATABASE:-ai}
   ```

   For a fresh DB, first:
   ```bash
   docker exec -it dash-db psql -U ai -d postgres -c \
       "DROP DATABASE ai; CREATE DATABASE ai OWNER ai;"
   ```

4. Restart services:

   ```bash
   docker compose start dash-pgbouncer dash-api dash-ml
   ```

5. Confirm `/health` shows the expected `last_backup_at` and the
   migration runner reports the schema is current.

## Tuning

| Env var | Default | Meaning |
|---|---|---|
| `BACKUP_DIR` | `/var/backups/dash` | path inside `dash-backup` container |
| `BACKUP_RETENTION_DAYS` | `7` | older dumps are pruned daily |
| `BACKUP_RUN_ONCE` | unset | when `1` runs once and exits (cron-from-host pattern) |

## Caveats

- `pg_dump` connects directly to `dash-db`, not PgBouncer (transaction-mode
  pooling can't serve `pg_dump`'s long-running session).
- Dumps are logical (`--format=plain`). Restores are O(rows) — fine up to
  tens of GB; switch to `pg_basebackup` for larger fleets.
- The daemon does not yet upload to S3/GCS. Mount the `backup_data` volume
  to a host directory that's already snapshotted, or add an offsite rsync.
- Restore wipes the destination database. Always smoke-test against a
  staging stack before running in prod.
