# Full Data-Upload Pipeline Test + Article↔Stock Mismatch Handling — Plan (no code yet)

> Real data has **catalog≠stock by design** (different ERP processes). 100% match is NOT expected.
> Goal: upload real files → both tables ingest → shop_flat reflects the TRUTH of the mismatch →
> tools answer with one of three honest states, never a false "out of stock" and never a silent drop.

## The core finding (why this matters)
`scripts/build_shop_flat.py:139` builds shop_flat by looping over **stock rows only**
(`for (art_key,site),(qty,cost) in agg.items()`). Consequence:

| Reality | Today's shop_flat | Right answer |
|---|---|---|
| in catalog + in stock (`both`) | row, linked=true | ✅ correct |
| **in catalog, NO stock (Scenario 1 — COMMON, 4600>3800)** | **NO ROW — article invisible** | exists, "not stocked / 0 stock" |
| stock, NO catalog (Scenario 2 — rare) | row, linked=false, brand NULL | exists as stock, "catalog info missing" |
| in neither | (nothing) | **"Not Found"** |

So today shop_flat = `stock LEFT JOIN catalog`. Scenario 1 articles vanish → the agent
can't distinguish "we don't carry it" from "we carry it, currently 0 stock". That breaks the
exact behavior you described (return info if found, else "Not Found").

## Fix = make shop_flat a FULL OUTER JOIN with an explicit presence column

### Schema change to `citypharma.shop_flat`
Add `link_status text` ∈ {`both`, `catalog_only`, `stock_only`}. Keep `linked` boolean for
back-compat (`linked = link_status IN ('both')`... actually `linked` already = "stock row matched a
catalog row"; map `both`→linked=true, `stock_only`→false, `catalog_only`→true-but-no-stock).

PK is `(art_key, site_code)` NOT NULL. A `catalog_only` article has no stock row → no site_code.
**Decision needed (Q1 below):** how to represent the no-site catalog row.

### Build rewrite (`build_shop_flat.py:136-148`)
1. Keep stock agg loop → emits `both` (matched) + `stock_only` (unmatched) per (art_key, site). As today.
2. **NEW:** after the stock loop, iterate `catalog` keys that produced ZERO stock rows →
   emit one `catalog_only` row each (stock_qty=0, is_in_stock=false, brand/generic present,
   site_code = sentinel per Q1).
3. Return dict gains `catalog_only`, `stock_only`, `both` counts (today only linked/unlinked).

### Tool changes (3-state + "Not Found")
- `stock_check` / `find_nearby_stock` (`pharma_shop_tool.py`, `find_nearby_stock.py`): when a brand/generic
  match resolves only to `catalog_only` rows → reply "**In catalog, not currently stocked (0 on hand)**",
  NOT "out of stock everywhere" and NOT silent-miss. When NO row at all → "**Not Found**".
- `find_substitutes` / `alternatives_for_indication` (`pharma_graph_tool.py`): already return `[]` on no
  match — wire the empty case to an explicit "Not Found" string so the agent says it plainly.
- `stock_only` rows (brand NULL) are still ILIKE-invisible by name. Add an **article_code path**: if the
  user query is a bare code, look up by `art_key` directly → can surface `stock_only` rows + flag
  "catalog details missing for this code".
- One shared helper `resolve_article(query)` returning `{state: both|catalog_only|stock_only|not_found, ...}`
  so all four tools answer consistently. (Single source of truth for the "Not Found" wording.)

### Data-quality surfacing (so you SEE the mismatch after every upload)
Extend `dash/learning/data_quality_scanner.py` with two issue types (counts off shop_flat):
- `catalog_gap` (was Scenario 1): `COUNT(*) link_status='catalog_only'` — MEDIUM, "N articles have no stock
  record (frozen/advance-entry/supplier-out) — expected, shown as not-stocked".
- `orphan_stock` (Scenario 2): `COUNT(*) link_status='stock_only'` — MEDIUM, "N stock rows have no catalog
  entry (delayed catalog update) — code searchable, catalog details missing".
- Keep existing `sci_notation_id` (the 1E+12 corruption check) — separate problem (corruption ≠ legit gap).
  Plan must NOT conflate a legit gap with corruption.

## The actual TEST protocol (run with your real files)
Phase order; each step has a pass-gate. All read-only verification via API/SQL, no hot-copy.

1. **Pre-flight** — `git status` clean baseline; note current shop_flat counts
   (`SELECT count(*), count(*) FILTER (WHERE linked) FROM citypharma.shop_flat`).
2. **Upload catalog (article) file** → `POST /api/upload` → assert: table created,
   `dash_table_metadata` row, col_hash stamped, expected row count (~4600).
3. **Upload balance-stock file** → assert table + metadata (~3800). Confirm fingerprint write
   fires the shop_flat rebuild hook (`upload.py:964`).
4. **Inspect mismatch** — `SELECT link_status, count(*) FROM shop_flat GROUP BY 1`. Expect
   `both` + `catalog_only`(Scenario 1) + `stock_only`(Scenario 2). Cross-check against raw:
   `catalog_count`, `stock_count`, `intersection` by normalized `art_key`. Numbers must reconcile
   (catalog_only = catalog − intersection; stock_only = stock − intersection).
5. **Corruption check** — DQ scanner over both files: is `article_code` Text or collapsed 1E+12?
   If sci-notation → the join is fake-0, fix at SOURCE (re-export as Text). Don't proceed to tool
   tests on corrupt keys (false mismatch).
6. **Tool truth tests** — 6 scripted questions, one per state:
   - known both → real qty;  known catalog_only → "in catalog, 0 stock";
   - known stock_only by code → "stock found, catalog missing";
   - unknown article → "Not Found";  substitute of catalog_only;  nearby of a 0-stock item.
   Each answer asserted EXACT (no false "out of stock", no hallucinated qty).
7. **Train** → trigger training, confirm shop_flat rebuilt, Q&A twins gen, no orphan tables.
8. **Regression** — re-run 6 tool tests post-train + a few analytics counts (totals reconcile).

## Open decisions (need your call before I build)
- **Q1 — how to store a `catalog_only` row** (no stock → no site_code):
  (a) sentinel `site_code='__none__'` single row per article [simplest, PK-safe];
  (b) one row per (article × every known site) with qty=0 [huge row blow-up — reject];
  (c) separate small table `catalog_unstocked` joined at query time [cleaner, more code].
  → Recommend **(a)**.
- **Q2 — "Not Found" wording**: exact string the agent must return (EN + Burmese mirror?).
- **Q3 — scope**: do store-locked keys see `catalog_only` for the WHOLE chain, or only items their
  store ever carried? (affects masking.)
- **Q4 — do you have the two real files staged now**, or do I write the test harness first and you
  drop files in after?

## Guardrails / non-negotiables
- Source-data corruption (1E+12) stays a SEPARATE, loud issue — never silently treated as a gap.
- Deploy = image REBUILD (`docker compose build dash-api` + force-recreate), no `docker cp`.
- `DELETE FROM dash.dash_daemon_leader` before force-recreate.
- Migration for the new `link_status` column + fold into baseline seed for cold installs.
- No false "out of stock": catalog_only ≠ out-of-stock-everywhere; they read differently.
- New table source = real upload, never fabricated rows.
```

ASCII — three states one pipeline:

  upload(article) ─┐
                   ├─▶ _norm(article_code) both sides ─▶ FULL OUTER ─▶ shop_flat.link_status
  upload(stock) ───┘                                      │
                                                          ├─ both         → qty
        resolve_article(query) ─▶ match link_status ──────┼─ catalog_only → "in catalog, 0 stock"
                                                          ├─ stock_only   → "stock found, catalog missing"
                                                          └─ none         → "Not Found"
```
