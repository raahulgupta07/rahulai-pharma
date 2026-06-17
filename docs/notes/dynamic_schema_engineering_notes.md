# Dynamic / Self-Adapting Schema — Engineering Notes

Shipped in **v1.51.0** (2026-06-17). Companion to the plan in
[`docs/plans/dynamic_schema.md`](../plans/dynamic_schema.md). This file is the
durable "what was built + why + landmines" record.

**Goal:** any change in uploaded data (new rows, add/remove/rename/retype
columns) does NOT break ingest / `shop_flat` / chat — it ADAPTS and is recorded;
ambiguous renames go to a one-click review gate. Flag `ADAPTIVE_SCHEMA`
(compose `dash-api` env; ON by default, `0` = legacy quarantine).

## Trigger incident
The articles table showed **0 rows / 21 cols / "Trained OK" / 0% health**. Root
cause: the file parsed to 0 data rows; `promote_file` wrote an empty table via
`to_sql`, and `ingest_promote` counted an empty `create` as "loaded" → trained
0%. Retrain can't add rows; re-upload re-parses to 0 → stuck. Fix (the
**empty-load guards**, separate from the adaptive feature): `promote_file`
refuses a 0-row load (`action:"empty"`), `ingest_promote` skips an empty df, and
the loaded-tally counts `rows_loaded > 0` only.

## Phases / files
- **P0 audit + safety** — migration `db/migrations/194_dynamic_schema.sql`:
  `public.dash_schema_events` (audit) + `public.dash_column_map` (logical→physical
  registry, `UNIQUE(project_slug, table_logical, logical_name)`). Helpers
  `dash/ingest/schema_events.py` (`log_schema_event`, `snapshot_table_bak`
  → `{tbl}__bak`).
- **P1 adaptive ingest** — `app/upload.py:ingest_promote` drift branch behind
  `ADAPTIVE_SCHEMA` (default OFF = byte-identical legacy quarantine). ON/`force`:
  snapshot `__bak`, widen retyped cols to TEXT (`loader.widen_columns_to_text`),
  `log_schema_event('adapt')`, evolve contract, load — added cols auto-added by
  `ensure_columns`, removed cols NULL-fill on append.
- **P2 registry + resolver** — `dash/ingest/colmap.py`: `resolve` (DB active →
  `DEFAULT_MAP` → identity), `resolve_many`, `set_map`, `list_maps`,
  `seed_defaults` (+ P4 `propose_remaps` / `decide_map` / `_logical_for_physical`).
  Seeded for `citypharma`: catalog `{article_code, brand→brand_name,
  generic→generic_name, composition, category, indication}`, stock `{article_code,
  site_code, stock_qty, cost→weighted_cost_price}`.
- **P3 `shop_flat` from registry** — `scripts/build_shop_flat.py` SELECTs use
  `colmap.resolve(...)` instead of hardcoded names; table discovery via resolved
  fingerprints (literal `CATALOG_COLS`/`STOCK_COLS` fallback). **No-empty guard**:
  if a required resolved column is missing from the source, abort the rebuild,
  keep the last-good `shop_flat`, log `rebuild{skipped}`. `shop_flat` OUTPUT names
  stay stable → tools / trained Q&A unchanged.
- **P4 rename gate** — name-similarity match: ≥0.85 auto-map (`active`), else
  `pending`. Endpoints `GET /ingest/{p}/colmap`, `POST /ingest/{p}/colmap/decide`
  (editor; confirm/reject/map → rebuild `shop_flat` + retrain). UI
  `frontend/src/lib/admin/ColumnMapReview.svelte` at **Command Center → Data →
  Column Mapping** (+ Schema Changes feed).
- **P5 auto-retrain + feed** — `ingest_promote` sets `_any_adapt` →
  `_notify_admins('schema', …)` + retrain on adapt; `decide` confirm → rebuild +
  retrain; `GET /ingest/{p}/schema-events`.
- **P6 harness** — `scripts/verify_dynamic_schema.py` (runs in-container,
  throwaway slug, no live-data mutation) — **12/12 PASS**.

## Landmines
- **`get_sql_engine()` BLOCKS public-schema writes** (guard
  `db/session.py:_guard_public_schema`, raises *"Cannot write to the public
  schema. Use the dash schema"*). All `public.dash_*` **writes** must use
  **`get_write_engine()`** (unguarded, `search_path = public,dash`). Caught by the
  harness — colmap / schema_events writes silently failed until switched. Reads
  via `get_sql_engine` are fine.
- Login route = `/api/auth/login` (auth router prefix `/api/auth`), NOT
  `/api/login`. All `/api/ingest` endpoints are behind global auth (401 without a
  Bearer token from `dash_tokens`).
- Migration dir highest was 193 → used 194. `dash_*` tables live in the `public`
  schema.
- Apply SQL to the live DB via stdin: `docker exec -i cp-db psql -U ai -d ai <
  file.sql` (NOT multiple `-c` statements in one call — they run as one txn).

## Full data wipe + self-rebootstrap (2026-06-17)
Procedure used to blank the box for new data (no backup):
1. DROP all `citypharma` tables (loop `pg_tables`).
2. TRUNCATE all `public.dash_%` `RESTART IDENTITY CASCADE` (incl users / projects
   / tokens).
3. DROP per-user `user_N` schemas.
4. AGE reset: `LOAD 'age'; SELECT drop_graph('citypharma_kg', true); SELECT
   create_graph('citypharma_kg');`
5. `docker exec cp-redis redis-cli FLUSHALL`.
6. **Restart `cp-api` TWICE** — 1st reseeds the super-admin
   (`_create_default_admin`, `app/auth.py`), 2nd reseeds the locked project
   (`ensure_locked_project`, `app/projects.py` — it needs the admin owner to exist
   FIRST, so a single restart leaves `dash_projects` empty).
7. Re-seed the registry: `docker exec cp-api python -c "from dash.ingest.colmap
   import seed_defaults; seed_defaults('citypharma')"`.

**Login after a user wipe** = `SUPER_ADMIN` / `SUPER_ADMIN_PASS` from env
(`_create_default_admin` seeds from env — any prior manually-set password is
gone). To change it: set `SUPER_ADMIN_PASS` + `SUPER_ADMIN_RESET_PASS=1` and
restart.
