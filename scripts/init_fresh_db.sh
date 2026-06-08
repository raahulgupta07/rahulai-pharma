#!/usr/bin/env bash
#
# init_fresh_db.sh — seed a FRESH CityPharma database with the complete,
# known-good baseline schema (db/baseline/schema.sql) + migration tracking
# rows (db/baseline/migrations_seed.sql).
#
# WHY THIS EXISTS
#   The app's base tables are created lazily by many runtime code paths and
#   the SQL migrations are additive ALTERs layered on top — so a brand-new
#   database only converges to a full schema after several restarts, throwing
#   a wall of "relation ... does not exist" warnings in the meantime. This
#   script loads the entire schema in one shot so a fresh cloud install comes
#   up clean on the FIRST boot, with nothing left for the runtime to converge.
#
# USAGE
#   bash scripts/init_fresh_db.sh            # load into an empty DB (refuses if not empty)
#   bash scripts/init_fresh_db.sh --reset    # DROP + recreate the DB first, then load
#                                            # (use to recover a half-initialized DB;
#                                            #  destroys all data — only on a fresh deploy)
#
# ENV (override as needed)
#   DB_CONTAINER  postgres container name      (default: cp-db)
#   DB_USER       postgres role                (default: ai)
#   DB_DATABASE   database name                (default: ai)
#   API_SERVICE   compose service to stop on --reset (default: dash-api)
#   COMPOSE_FILE  compose file                 (default: compose.yaml)
#
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-cp-db}"
DB_USER="${DB_USER:-ai}"
DB_DATABASE="${DB_DATABASE:-ai}"
API_SERVICE="${API_SERVICE:-dash-api}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
RESET=0
[ "${1:-}" = "--reset" ] && RESET=1

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCHEMA="$HERE/db/baseline/schema.sql"
SEED="$HERE/db/baseline/migrations_seed.sql"

for f in "$SCHEMA" "$SEED"; do
  [ -f "$f" ] || { echo "✗ missing baseline file: $f" >&2; exit 1; }
done

psql_db() { docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$1" "${@:2}"; }

echo "→ target: container=$DB_CONTAINER db=$DB_DATABASE user=$DB_USER reset=$RESET"

if [ "$RESET" = "1" ]; then
  echo "→ --reset: stopping $API_SERVICE so the DB has no active connections…"
  docker compose -f "$COMPOSE_FILE" stop "$API_SERVICE" >/dev/null 2>&1 || true
  echo "→ dropping + recreating database $DB_DATABASE (ALL DATA LOST)…"
  psql_db postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$DB_DATABASE\" WITH (FORCE);" >/dev/null
  psql_db postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$DB_DATABASE\";" >/dev/null
else
  # Guard: refuse to load over a database that already has the core schema.
  exists="$(psql_db "$DB_DATABASE" -t -A -c "SELECT to_regclass('public.dash_projects') IS NOT NULL" 2>/dev/null || echo error)"
  if [ "$exists" = "t" ]; then
    echo "✗ refusing: public.dash_projects already exists — DB is not fresh." >&2
    echo "  To rebuild from scratch (destroys data): bash scripts/init_fresh_db.sh --reset" >&2
    exit 1
  fi
fi

echo "→ loading baseline schema (db/baseline/schema.sql)…"
psql_db "$DB_DATABASE" -q -v ON_ERROR_STOP=1 < "$SCHEMA"

echo "→ seeding migration tracking rows (runner will skip already-applied)…"
psql_db "$DB_DATABASE" -q -v ON_ERROR_STOP=1 < "$SEED"

tbls="$(psql_db "$DB_DATABASE" -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('public','dash','citypharma')")"
migs="$(psql_db "$DB_DATABASE" -t -A -c "SELECT count(*) FROM public.dash_migrations")"
echo "✓ baseline loaded: $tbls tables, $migs migrations marked applied."

if [ "$RESET" = "1" ]; then
  echo "→ starting $API_SERVICE…"
  docker compose -f "$COMPOSE_FILE" up -d "$API_SERVICE" >/dev/null
  echo "✓ done. Watch: docker logs -f $(docker compose -f "$COMPOSE_FILE" ps -q "$API_SERVICE" 2>/dev/null | cut -c1-12 || echo cp-api)"
else
  echo "✓ done. Now: docker compose -f $COMPOSE_FILE up -d"
fi
