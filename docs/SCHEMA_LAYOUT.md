# Schema Layout (`public` vs `dash`)

**Issue #28 reference.** Dash uses two PostgreSQL schemas — `public` and `dash` —
plus per-tenant `user_*` / `<project_slug>` schemas. This doc spells out which
tables live where and *why*, so contributors don't add a `dash_*` table in the
wrong schema and trigger silent search-path drift.

> No schema migration is planned. This is documentation only. If you find a
> table in the "wrong" schema, run `scripts/audit_schema_split.py` to confirm
> it's intentional before moving anything.

---

## TL;DR canonical home schema per table category

| Category | Home schema | Why |
|---|---|---|
| System (auth, projects, audit) | `public` | First migration shipped before `dash` schema existed; auth tables are touched by middleware on every request and live in the default search path. |
| Brain (`dash_company_brain`, `dash_brain_*`) | `public` | Created by the very early Brain migrations before the `dash` schema convention was codified. Engineer write-guard explicitly blocks public-schema DDL **except** for brain rows, which are written via the API tier. |
| Learning v1/v2 (memories, hypotheses, dossiers, scratchpad) | `dash` | Agent-managed working memory. Engineer owns this schema; tables are recreated by `db/migrations/00*_self_learning*.sql`. |
| Vectors (`dash_vectors`, `dash_vector_audit`) | `dash` | Created in `028_vectors.sql` with HNSW + GIN indexes. Per-tenant RLS policies are attached to the `dash`-schema copy. |
| Knowledge Graph (`dash_knowledge_triples`) | **BOTH** | `public.dash_knowledge_triples` is the chat-time SPO writer (owned by `dash/tools/knowledge_graph.py`). `dash.dash_knowledge_triples` is the bi-temporal copy bootstrapped by Dream Reflection (migration 069). They are independent — do not merge. |
| Dream Reflection / Skill Library / Bi-Temporal | `dash` | Migrations 066-069 deliberately scope to `dash`. Bi-temporal ALTERs on the brain use a `DO` block that targets whichever schema currently owns the brain (usually `public`). |
| Ontology Workbench (`dash_template_*`, `dash_autonomous_workflows`, `dash_ontology_*`) | `public` | Cross-tenant catalog rows; never per-project; share with auth tables in `public`. |
| Connectors (`dash_data_sources`, `dash_tokens`) | `public` | Tokens live next to auth for compatibility with the AuthMiddleware lookups. |
| Provider scratch (per-source registry) | `dash` | Engineer-owned. |
| Per-tenant data | `user_<id>` / `<project_slug>` | Each project gets its own PostgreSQL schema; tenant-isolated, never qualified with `dash` or `public`. |

---

## Why two schemas?

1. **Read-only enforcement.** The Engineer's connection sets
   `search_path = dash, public` and a `_guard_public_schema` listener blocks
   CREATE/ALTER/DROP/INSERT/UPDATE/DELETE/TRUNCATE targeting the `public` schema.
   That guard makes the split a **safety boundary**, not just a naming
   convention. Putting agent-writable tables in `public` would silently bypass
   the guard.

2. **Migration ordering.** The earliest migrations (auth, projects, brain) ran
   before `dash` existed. Re-homing them now would break every install. The
   `dash` schema came in with `001_provider_layer.sql` and has been the home
   for agent-managed tables ever since.

3. **Per-tenant isolation.** Tenant projects get their own schema
   (`<project_slug>`); RLS + search-path scoping live there. Neither `dash`
   nor `public` is per-tenant — they're shared.

---

## Common pitfalls (caught in the wild)

- **`dash_knowledge_triples` ambiguity.** Two copies exist. New code should
  default to `public.dash_knowledge_triples` (used by chat-time KG writer).
  Bi-temporal queries go through the `dash` copy. Always schema-qualify.
- **`dash_company_brain` lives in `public`.** Migration 067's bi-temporal
  ALTERs auto-detect the brain's schema via `information_schema.tables` and
  apply changes wherever it lives. Don't hard-code `dash.dash_company_brain`.
- **Engineer can't write to `public`.** If a tool needs to write to a `public`
  table, use the application-tier engine (`get_sql_engine` *not* the engineer's
  read-only one), or relax the guard for that single statement via a server-
  side function.
- **Agno framework tables.** Use the default schema (Agno picks `public` from
  the PgBouncer-rewritten search path). Don't try to re-home them.

---

## Running the audit

```bash
python scripts/audit_schema_split.py
```

It walks every `*.sql` in `db/migrations/` and reports:

- Tables that appear in **both** `public.*` and `dash.*` (split-brain risk)
- Tables that appear unqualified (resolves whichever schema is first on the
  search path — usually a footgun)
- Cross-migration conflicts (same table created in two different schemas)

Exits non-zero only if a *new* conflict is introduced.
