# Dynamic / Self-Adapting Schema — Phased Plan

**Goal:** Any change in the uploaded data (new rows, new/removed/renamed columns,
type changes) should NOT break ingestion, the `shop_flat` semantic layer, training,
or chat. Changes are *adapted* and *recorded*, never silently dropped; truly
ambiguous changes (renames) surface for 1-click review instead of breaking.

**Non-goals / honest limits:**
- If a column the agent genuinely needs disappears, no system can invent it —
  we detect + warn + fall back, not pretend.
- This does NOT fix parse-to-0-rows failures (separate bug; empty-load guard
  already added in `loader.py` / `ingest_promote`).
- "Never breaks" target ≈ 90%: additive + self-healing + never-destructive, with
  a review gate for ambiguous renames.

**Today's rigidity (what breaks):**
- Contract drift → `quarantine` (file blocked) — `dash/ingest/contract.py`,
  `app/upload.py:ingest_promote`.
- `scripts/build_shop_flat.py` SELECTs hardcoded source columns
  (`article_code, brand_name, generic_name, composition, category`,
  `site_code, stock_qty, weighted_cost_price`) → rename a header → view build fails.
- `dash/tools/table_sync.py` `CATALOG_COLS` / `STOCK_COLS` fingerprint = fixed.
- Tools/Q&A reference `shop_flat`'s stable names (OK — that layer is already stable).

---

## Phase 0 — Safety net + audit (0.5 day)  [flag: off → on]
**Goal:** never lose data; record every schema change.
- DONE: empty-load guard (`loader.py promote_file`, `ingest_promote`, loaded-tally).
- New migration: `dash_schema_events(project, table, event, detail jsonb, ts)` —
  log added/removed/renamed/type_changed/empty per ingest.
- New migration: `dash_column_map` (created here, populated Phase 2):
  `(project, table_logical, logical_name, physical_col, confidence, source, status, ts)`.
- Pre-mutation snapshot: before any ALTER/replace, stamp `{table}__bak` (reuse the
  existing S3-sync backup helper) so any adapt is reversible.
- Flag `ADAPTIVE_SCHEMA` (default OFF this phase).
**Acceptance:** events table fills on a normal upload; backup row created; no behavior change yet.
**Risk:** none (additive). **Rollback:** drop tables, flag stays off.

## Phase 1 — Adaptive ingest: drift → adapt, not quarantine (1.5 days)  [flag-gated]
**Goal:** column/data changes load instead of being blocked.
- `dash/ingest/contract.py`: `check_against_contract` returns a structured diff
  `{added:[], removed:[], renamed:[], type_changed:[]}` (already partly there).
- `app/upload.py:ingest_promote`: when `ADAPTIVE_SCHEMA` on and verdict=drift:
  - added cols → `ensure_columns` ALTER ADD (already exists) + log event.
  - removed cols → keep column in table, NULL-fill new rows (no DROP) + log.
  - type change → store TEXT (COPY is all-text already), cast at read + log.
  - renamed → defer to Phase 4 matcher; until then treat as added+removed + log.
  - evolve contract to new shape (`infer_contract`/`save_contract`) automatically.
  - NEVER quarantine on drift when flag on; quarantine still applies to empty/corrupt.
- Keep destructive guards: empty parse, row-count cliff (`REPLACE_MIN_ROW_PCT`).
**Acceptance (adversarial):** upload variants of one dataset — (a) +1 col, (b) -1 col,
(c) reordered cols, (d) text→number, (e) extra rows — all load, 0 quarantine, events logged,
old data intact.
**Risk:** runaway column growth from typo headers → mitigate with Phase 4 matcher + review.
**Rollback:** flag off → revert to quarantine.

## Phase 2 — Logical column registry + resolver (1 day)
**Goal:** decouple "meaning" from raw header text.
- New module `dash/ingest/colmap.py`:
  - `resolve(project, table, logical_name) -> physical_col`
  - `set_map(...)`, `list_maps(...)`, seeded from current canonical names.
- Seed registry from today's truth: logical `article_code/brand/generic/composition/
  category/site_code/stock_qty/cost` → current physical columns (confidence 1.0, source 'seed').
- `dash/tools/table_sync.py`: `CATALOG_COLS`/`STOCK_COLS` become *logical* sets resolved
  via the registry (fall back to current literals if unmapped).
**Acceptance:** `resolve()` returns correct physical col for every logical name on current data;
table_sync still finds the right tables.
**Risk:** seed mismatch → verify against live schema before enabling.
**Rollback:** resolver falls back to hardcoded literals (no-op).

## Phase 3 — Rebuild `shop_flat` from the registry (1 day)
**Goal:** rename a raw header → chat keeps working (the real win).
- `scripts/build_shop_flat.py`: replace the hardcoded SELECT column names with
  `colmap.resolve(...)` lookups; build the SELECT dynamically. Output `shop_flat`
  schema/names stay STABLE (tools + Q&A unchanged).
- Auto-rebuild `shop_flat` after any Phase-1 adapt event on a catalog/stock table
  (hook into the post-ingest path that already calls `build_shop_flat()` in upload.py).
**Acceptance:** rename `brand_name`→`brand_en` in the catalog file, upload → registry
remaps (auto if obvious, else review) → `shop_flat` rebuilds → "top products" / counts
still answer correctly. No tool/Q&A edits.
**Risk:** ambiguous rename before Phase 4 lands → guard: if a required logical name
can't resolve, keep last good `shop_flat` + raise a clear alert (don't build empty).
**Rollback:** revert build script to literals.

## Phase 4 — Rename detection + review gate + UI (2 days)
**Goal:** auto-map obvious renames; surface ambiguous ones for 1-click confirm.
- Matcher in `colmap.py`: score new physical col vs known logical by
  (a) normalized-name similarity, (b) value profile (dtype, cardinality, sample overlap).
  High score → auto-map (status `auto`); low/ambiguous → status `pending`.
- Endpoint `POST /ingest/{project}/colmap/{decide}` — confirm/reject a pending map.
- UI review panel (Data Source / Workspace admin): list pending maps
  "`brand_en` looks like `brand` (0.82) — [Confirm] [It's new] [Map to…]".
- Reuse existing `resolve-drift` mapping UI plumbing where possible
  (`app/upload.py:ingest_resolve_drift` already takes a `{new_col: existing}` mapping).
**Acceptance:** obvious rename auto-maps (no prompt); ambiguous rename shows one review
card; confirm → registry + `shop_flat` update; reject → treated as new column.
**Risk:** false auto-map → cap auto-map at high confidence only; everything else pending.
**Rollback:** disable matcher → all changes pending review (safe).

## Phase 5 — Auto-retrain + observability (0.5 day)
**Goal:** metadata/Q&A stay correct after a schema change; user can see what changed.
- On a committed adapt/remap event, trigger the existing `retrain_project` (debounced)
  to refresh column metadata + training Q&A.
- Schema-change feed: surface `dash_schema_events` in the admin feed + a notification
  ("articles_list: +1 col `pack_size`, `brand_name`→`brand_en` (confirmed)").
**Acceptance:** after a column change + retrain, chat metadata reflects new schema;
event visible in feed.
**Risk:** retrain storms on rapid uploads → debounce (already have per-table retrain timeout).
**Rollback:** disable auto-retrain hook (manual retrain still works).

## Phase 6 — Test harness + rollout + AWS deploy (1 day)
- Adversarial fixture set: one base dataset + 8 mutated variants (add/remove/rename/
  reorder/type/empty/dup-col/unicode-header). Script asserts: loads, no data loss,
  `shop_flat` rebuilds, benchmark chat answers still correct.
- Staged rollout: flag `ADAPTIVE_SCHEMA` ON in a copy first; run harness; then prod.
- Build image, release (vX.Y.0), deploy local :8011, verify; then deploy AWS box.
**Acceptance:** full harness green; benchmark answers unchanged; AWS running new image.

---

### Order & effort
0 → 1 → 2 → 3 are the spine (the "data + most column changes don't break it" win, ~4 days).
4 adds safe renames (~2 days). 5–6 polish + ship (~1.5 days). Total ≈ 7.5 days.

### Gate after each phase
Each phase is flag-gated and independently shippable. Stop after Phase 3 if renames
are rare; add Phase 4 when rename pain is real.
