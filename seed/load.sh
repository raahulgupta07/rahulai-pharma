#!/usr/bin/env bash
# Seed the CityPharma single agent: project schema + 111k rows + brain + persona
# + training Q&A. Idempotent — no-op if the locked project already exists.
#
# Run once after `docker compose up -d`:  bash seed/load.sh
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-cp-db}"
DB_USER="${DB_USER:-ai}"
DB_NAME="${DB_DATABASE:-ai}"
SLUG="${LOCKED_PROJECT_SLUG:-proj_demo_citypharma}"
HERE="$(cd "$(dirname "$0")" && pwd)"

psql() { docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" "$@"; }

# Wait for DB to accept connections (up to ~60s).
for i in $(seq 1 30); do
  if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" >/dev/null 2>&1; then break; fi
  echo "waiting for $DB_CONTAINER ($i)…"; sleep 2
done

exists="$(psql -tA -c "SELECT 1 FROM public.dash_projects WHERE slug='${SLUG}'" 2>/dev/null || true)"
if [ "$exists" = "1" ]; then
  echo "✓ ${SLUG} already seeded — nothing to do."
  exit 0
fi

echo "▸ loading project schema + data (111k rows)…"
psql -v ON_ERROR_STOP=1 < "$HERE/10_project_schema.sql"

echo "▸ loading metadata (project row + brain + persona + training)…"
# dash_projects first (FK target for the rest); user_id=1 = demo super-admin.
# Map by CSV header columns so schema drift (extra default cols) is tolerated.
# Per-table failures warn + continue — never abort the whole seed.
set +e
for T in dash_projects dash_personas dash_company_brain dash_training_qa \
         dash_table_metadata dash_business_rules_db dash_memories; do
  f="$HERE/data/${T}.csv"
  [ -s "$f" ] || { echo "  skip ${T} (empty)"; continue; }
  rows=$(( $(wc -l < "$f") - 1 ))
  cols="$(head -1 "$f")"
  if cat "$f" | psql -v ON_ERROR_STOP=1 -c "COPY public.${T} (${cols}) FROM STDIN CSV HEADER" >/dev/null 2>&1; then
    echo "  ✓ ${T}: ${rows} rows"
  else
    echo "  ⚠ ${T}: load failed (schema drift?) — skipped"
  fi
done
set -e

echo "✓ CityPharma seeded."
