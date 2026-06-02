# Cross-Source Federation

Join data across multiple sources WITHIN ONE project. Hard agent isolation
prevents reading data from other projects.

## Quick example

```sql
SELECT a.customer_id, a.region, b.lifetime_value
FROM fabric_42.orders a
JOIN postgres_local.crm_customers b
  ON a.customer_id = b.id
WHERE a.region = 'NA' AND b.tier = 'gold'
```

Analyst calls `federated_query(sql)`. Engine fans out subqueries to
each source in parallel, merges results in DuckDB.

## Allowed patterns

| Pattern                              | Supported | Notes |
|--------------------------------------|-----------|-------|
| Equi-join across sources             | Yes       | INNER + LEFT OUTER |
| WHERE pushdown per source            | Yes       | filters routed to source |
| GROUP BY in final merge              | Yes       | done in DuckDB |
| ORDER BY                             | Yes       | applied post-merge |
| LIMIT                                | Yes       | applied post-merge, capped 50K |
| Sub-queries                          | No        | Phase 2 |
| Window functions                     | No        | Phase 2 |
| DDL / DML                            | No        | always blocked |
| 3+ source chain joins                | Yes       | topological order |
| Cross-project (other project's data) | No        | NEVER allowed |

## Source prefix syntax

`<provider_id>.<table_name>`

Provider IDs come from connected sources:
- SQL sources: `fabric_42`, `postgres_local`, `mysql_remote_3`
- File sources (PPTX/PDF/etc.): `file_<doc_id>`
- Source-tied profile tables: `src<source_id>`

List all available sources via Settings → SOURCES tab.

## Architecture

```
ANALYST
   │ federated_query(sql)
   ▼
PARSER (sqlglot)
   │ identify source prefixes + extract AST
   ▼
RESOLVER
   │ map prefix → provider in CURRENT PROJECT only
   │ check agent_scope + RBAC + degraded
   ▼
SPLITTER
   │ build per-source subqueries
   │ push down WHERE filters per source
   │ extract JOIN keys
   ▼
EXECUTOR (asyncio.gather)
   │ parallel exec per source w/ 30s timeout + 10K row cap
   ▼
DIALECT TRANSLATOR
   │ Postgres ↔ T-SQL ↔ MySQL via sqlglot.transpile
   ▼
MERGE (DuckDB primary, pandas fallback)
   │ register per-source DataFrames
   │ run final JOIN
   │ apply final WHERE/ORDER/LIMIT
   ▼
RESULT (CSV, 8KB cap, FEDERATED badge)
```

## Dialect translation table

| From → To  | LIMIT/TOP | Date | Cast | Quote | Notes |
|------------|-----------|------|------|-------|-------|
| pg → tsql  | LIMIT N → SELECT TOP N | NOW() → GETDATE() | x::T → CAST(x AS T) | "id" → [id] | INTERVAL needs manual |
| tsql → pg  | TOP N → LIMIT N suffix | GETDATE() → NOW() | already explicit | [id] → "id" | ISNULL → COALESCE |
| pg → mysql | LIMIT same | NOW() same | x::T → CAST | "id" → \`id\` | ILIKE → LIKE warn |
| mysql → pg | LIMIT same | NOW() same | already explicit | \`id\` → "id" | |

## Isolation guarantee

```
Project A's Analyst
  ├── sees   Project A's connectors + files
  ├── joins  WITHIN Project A only
  └── BLOCKED from Project B's data (registry never returns)

Project B's Analyst
  └── (mirror — only sees Project B)

ENFORCEMENT POINTS
  1. Provider registry keyed by (project_slug, provider_id)
     list_for_project(slug) only returns slug's providers
  2. Tool factory only emits tools for current project
     Other project's query_<id> literally not in Analyst's tool list
  3. Federation resolver
     resolve(prefixes, project_slug) checks each prefix against
     registry.list_for_project(project_slug) only
  4. PostgreSQL schema isolation
     SET LOCAL search_path = proj_<slug> per transaction
  5. RBAC at endpoint
     check_project_permission(user, slug) before any access
```

Even super-admin cannot bridge projects via federation. Cross-project
sharing is a separate (not yet built) feature requiring explicit opt-in.

## Performance tips

```
1. Push down filters
   GOOD:  WHERE a.region='NA' AND b.qty>100   (filters per source)
   BAD:   WHERE a.x + b.y > 50                (post-merge, slow)

2. Pre-aggregate at source
   GOOD:  SELECT SUM(amt) FROM fabric.t1 + JOIN to small dim
   BAD:   row-level join across 1M rows

3. Use covering JOINs (small × big)
   Specify smaller source first in FROM

4. Avoid SELECT *
   Project only columns you need

5. Add LIMIT when exploratory
   Federation hard-caps at 50K rows; explicit LIMIT helps

6. Watch row caps
   Per-source 10K row cap. If you hit it (truncated badge), filter more.

7. Prefer DuckDB engine
   Set federation_default_engine = duckdb in admin settings.
```

## Configuration (admin)

| Setting | Default | Scope | Description |
|---------|---------|-------|-------------|
| enable_federation_join | true | both | Allow cross-source JOINs |
| max_cross_source_rows | 50000 | both | Final merged result cap |
| federation_timeout_s | 60 | global | Total query timeout |
| federation_default_engine | duckdb | global | Merge engine: duckdb / pandas |

## Cost model

```
Federated query cost = base_cost × 2  (federation weighting)
  applied to dash_audit_log.cost_usd

Daily cap (per project) includes federation cost
  → set higher cap if doing lots of cross-source work

View spend: Settings → SELF-LEARN tab → COST card
View by-source breakdown: Settings → FEDERATION tab
```

## Circuit breaker

After 3 consecutive failures (timeout / OOM / merge error):
- circuit opens for 5 min
- new federated queries return "CIRCUIT OPEN" message
- regular query_<id> calls still work

After cooldown:
- circuit half-opens (next call attempts; success closes, failure re-opens)

Manual reset: Settings → FEDERATION tab → RESET CIRCUIT button.

## Self-correction

federated_query auto-retries up to 3 times w/ corrective adjustments:
1. Zero rows → relax last AND clause in WHERE
2. Exec error mentioning column → drop that column from SELECT
3. Merge error → try alternative join key from semantic_union
4. Translate error → fall back to canonical (Postgres) syntax

Correction log returned w/ result for transparency.

## File source prefix

PPTX/PDF/DOCX/XLSX-extracted tables addressable as:
- `file_<doc_id>.<table_name>` (doc-extracted, e.g. `file_q3_report.slide_3_table_1`)
- `src<source_id>.<table_name>` (source-tied profile tables)

List via /api/connectors/sources/{id}/test-federation or programmatic
`dash.providers.federation.file_source.discover(slug)`.

Backed by parquet → csv → json files in `knowledge/{slug}/`.
SQL subset: SELECT/WHERE/GROUP BY/ORDER BY/LIMIT.
NO JOINs (federation engine handles multi-source joins).

## Troubleshooting

### "FEDERATION RESOLVE ERROR: unknown source 'x'"
Source not in this project. Check `list_for_project(slug)` output.
List of known prefixes appears in error message.

### "scope mismatch: researcher_only"
Source is Researcher-scoped. Analyst can't see it. Change source's
scope to `project` or `analyst_only` in Settings → SOURCES.

### "FEDERATION CIRCUIT OPEN"
Recent failures opened circuit. Wait 5 min OR manually reset.

### "FEDERATION DISABLED: admin setting"
Super-admin disabled federation globally OR project-scoped.
Check Command Center → ADMIN SETTINGS → SELF-LEARNING (or per-project).

### "no per-source results"
All subqueries failed. Check Settings → FEDERATION → RECENT FAILURES.
Common: source degraded, dialect translation broke, timeout.

### Slow query
- Check per-source latency in Settings → FEDERATION
- Push down more filters
- Add explicit LIMIT
- Pre-aggregate before joining

### Cross-source query I want to run is rejected
File a feature request — federation is intra-project by design.
Alternative: copy data into a single project, OR use central pool
for shared definitions (NOT shared values).

## Cross-references

- Provider abstraction: `dash/dash/providers/`
- Federation modules: `dash/dash/providers/federation/`
- Tool wiring: `dash/dash/tools/federated_query.py`
- Admin settings: Command Center → ADMIN SETTINGS
- Architecture: `ARCHITECTURE.md`
- Security model: `SECURITY.md`
