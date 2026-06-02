#!/bin/bash
# Dash backup script — pg_dump + tar knowledge + S3 upload
# Run via K8S CronJob OR cron OR manual.
#
# Required env:
#   DB_HOST, DB_USER, DB_PASS, DB_DATABASE
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION (or IAM role)
#   BACKUP_S3_BUCKET (e.g. s3://dash-backups)
#   BACKUP_ENV (prod, staging, dev — for path prefix)
#   KNOWLEDGE_DIR (default /app/knowledge)
#
# Optional env:
#   BACKUP_RETENTION_DAILY (default 30)
#   BACKUP_TYPES (default "db,knowledge"; comma-sep)
#   BACKUP_PREFIX (path prefix; default "")

set -euo pipefail

# Defaults
: "${DB_HOST:?DB_HOST required}"
: "${DB_USER:?DB_USER required}"
: "${DB_PASS:?DB_PASS required}"
: "${DB_DATABASE:?DB_DATABASE required}"
: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET required (e.g. s3://dash-backups)}"
: "${BACKUP_ENV:=prod}"
: "${KNOWLEDGE_DIR:=/app/knowledge}"
: "${BACKUP_TYPES:=db,knowledge}"
: "${BACKUP_RETENTION_DAILY:=30}"

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
WORK_DIR="/tmp/dash_backup_${TS}"
mkdir -p "$WORK_DIR"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2; }

# DB backup
if [[ ",${BACKUP_TYPES}," == *",db,"* ]]; then
    log "DB backup starting..."
    DB_FILE="dash_db_${TS}.sql.gz"
    PGPASSWORD="$DB_PASS" pg_dump \
        -h "$DB_HOST" -U "$DB_USER" -d "$DB_DATABASE" \
        --no-owner --no-acl --clean --if-exists \
        | gzip -9 > "$WORK_DIR/$DB_FILE"
    DB_SIZE=$(stat -c '%s' "$WORK_DIR/$DB_FILE" 2>/dev/null || stat -f '%z' "$WORK_DIR/$DB_FILE")
    log "DB backup ${DB_FILE} (${DB_SIZE} bytes)"

    aws s3 cp "$WORK_DIR/$DB_FILE" \
        "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/db/${DB_FILE}" \
        --storage-class STANDARD_IA
    log "DB uploaded to S3"
fi

# Knowledge volume backup
if [[ ",${BACKUP_TYPES}," == *",knowledge,"* ]]; then
    if [[ -d "$KNOWLEDGE_DIR" ]]; then
        log "Knowledge backup starting..."
        KN_FILE="dash_knowledge_${TS}.tar.gz"
        tar czf "$WORK_DIR/$KN_FILE" -C "$(dirname $KNOWLEDGE_DIR)" \
            "$(basename $KNOWLEDGE_DIR)"
        KN_SIZE=$(stat -c '%s' "$WORK_DIR/$KN_FILE" 2>/dev/null || stat -f '%z' "$WORK_DIR/$KN_FILE")
        log "Knowledge backup ${KN_FILE} (${KN_SIZE} bytes)"

        aws s3 cp "$WORK_DIR/$KN_FILE" \
            "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/knowledge/${KN_FILE}" \
            --storage-class STANDARD_IA
        log "Knowledge uploaded to S3"
    else
        log "Knowledge dir not found at $KNOWLEDGE_DIR, skipping"
    fi
fi

# Update last_backup_at marker (in DB)
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_DATABASE" \
    -c "INSERT INTO public.dash_backup_runs (env, types, ts, success) VALUES ('$BACKUP_ENV', '$BACKUP_TYPES', NOW(), TRUE) ON CONFLICT DO NOTHING" \
    2>/dev/null || log "warning: backup marker update failed"

# Retention: list S3 keys older than N days, delete if beyond retention
log "Retention sweep (keep last $BACKUP_RETENTION_DAILY days)..."
CUTOFF_DATE=$(date -u -d "-${BACKUP_RETENTION_DAILY} days" +%Y-%m-%d 2>/dev/null \
              || date -u -v-${BACKUP_RETENTION_DAILY}d +%Y-%m-%d)

aws s3 ls "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/" --recursive 2>/dev/null \
    | awk -v cutoff="$CUTOFF_DATE" '$1 < cutoff {print $4}' \
    | while read -r key; do
        if [[ -n "$key" ]]; then
            log "Pruning: $key"
            aws s3 rm "${BACKUP_S3_BUCKET}/$key" --quiet || true
        fi
    done

log "Backup complete: ${TS}"
exit 0
