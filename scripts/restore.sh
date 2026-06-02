#!/bin/bash
# Dash restore script — pull backup from S3 and restore.
# Usage:
#   ./restore.sh <db|knowledge|all> [backup_filename]
#   ./restore.sh db latest        # restores latest DB backup
#   ./restore.sh db dash_db_2026-05-06T03-00-00Z.sql.gz
#   ./restore.sh all latest       # restores DB + knowledge
#
# WARNING: --clean drops existing tables. Always backup current state first.

set -euo pipefail

: "${DB_HOST:?DB_HOST required}"
: "${DB_USER:?DB_USER required}"
: "${DB_PASS:?DB_PASS required}"
: "${DB_DATABASE:?DB_DATABASE required}"
: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET required}"
: "${BACKUP_ENV:=prod}"
: "${KNOWLEDGE_DIR:=/app/knowledge}"

TYPE="${1:-}"
FILENAME="${2:-latest}"

if [[ -z "$TYPE" ]]; then
    echo "Usage: $0 <db|knowledge|all> [filename|latest]"
    exit 1
fi

WORK_DIR="/tmp/dash_restore_$$"
mkdir -p "$WORK_DIR"
trap "rm -rf '$WORK_DIR'" EXIT

log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

restore_db() {
    local fn="$1"
    if [[ "$fn" == "latest" ]]; then
        fn=$(aws s3 ls "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/db/" \
            | sort | tail -n1 | awk '{print $4}')
    fi
    log "Restoring DB from $fn..."
    aws s3 cp "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/db/$fn" "$WORK_DIR/db.sql.gz"
    log "WARNING: Will drop + recreate schema in $DB_DATABASE in 5 seconds..."
    sleep 5
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_DATABASE" \
        -c "SET statement_timeout = 0; SET lock_timeout = 0;"
    gunzip -c "$WORK_DIR/db.sql.gz" | \
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_DATABASE"
    log "DB restored"
}

restore_knowledge() {
    local fn="$1"
    if [[ "$fn" == "latest" ]]; then
        fn=$(aws s3 ls "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/knowledge/" \
            | sort | tail -n1 | awk '{print $4}')
    fi
    log "Restoring knowledge from $fn..."
    aws s3 cp "${BACKUP_S3_BUCKET}/${BACKUP_ENV}/knowledge/$fn" "$WORK_DIR/kn.tar.gz"
    log "WARNING: Will overwrite $KNOWLEDGE_DIR in 5 seconds..."
    sleep 5
    mkdir -p "$KNOWLEDGE_DIR"
    tar xzf "$WORK_DIR/kn.tar.gz" -C "$(dirname $KNOWLEDGE_DIR)"
    log "Knowledge restored"
}

case "$TYPE" in
    db)        restore_db "$FILENAME" ;;
    knowledge) restore_knowledge "$FILENAME" ;;
    all)
        restore_db "$FILENAME"
        restore_knowledge "$FILENAME"
        ;;
    *)
        echo "Unknown type: $TYPE"
        exit 1
        ;;
esac

log "Restore complete"
