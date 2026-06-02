# Design Patterns

> kpt autoresearch + Scout provider abstraction + Dash-original patterns.
> Pair with `ARCHITECTURE.md` (layers) and `AGENTS.md` (rules + agent inventory).

## kpt autoresearch patterns (12 implemented)

kpt's "Software 1.0 → 2.0 → 3.0" autoresearch loop, mapped to Dash's
`dash/learning/` modules.

| # | Pattern | Implementation in Dash |
|---|---------|------------------------|
| 1 | **Time-budgeted experiments** | `PER_QUESTION_TIMEOUT_S = 120s` in `cycle.py`. Per-question hard cap via `asyncio.wait_for`. |
| 2 | **Single-file diff scope** | A hypothesis statement = one declarative line. `HypothesisEngine.form_from_dossier()` produces atomic claims. |
| 3 | **`program.md` instructions** | Per-project goals file at `knowledge/{slug}/learning_goals.md`. Loaded by `learning/goals.py` at cycle start. |
| 4 | **Keep / discard heuristic** | Confidence delta on verify. `Verifier.verify(h)` returns delta; below threshold → discard, above → keep + consolidate. |
| 5 | **Single eval metric** | `agent_iq` composite score (`learning/agent_iq.py`). One number per cycle. Plotted as sparkline. |
| 6 | **Parallel experiments** | `asyncio.gather` in `ResearcherLoop.research_async` — 7 tiers run in parallel, triangulation count seeds confidence. |
| 7 | **Visual progress** | SVG sparkline + 5-component strip rendered in Settings → LEARNING tab. Pulls from `dash_self_learning_runs`. |
| 8 | **Diff-as-experiment** | `parent_hypothesis_id` tree in `dash_hypotheses`. Every hypothesis traceable to ancestor. `learning/lineage.py` walks the tree. |
| 9 | **Branch + prune** | 3-variant fork in `CuriosityEngine.generate()` — same root question, three reframings; promotion prunes losers. |
| 10 | **Run-then-review log** | Today's discoveries digest (`learning/digest.py`) — daily summary of what was learned, posted to brain + UI. |
| 11 | **Deterministic baseline (dry-run)** | Sunday canary CronJob: `dry_run=True` → forces `llm_call_fn=None`, max 5 questions, $0. Compares to last week's baseline. |
| 12 | **Resource-fair comparison** | Per-project daily cost cap via `learning/cost_guard.py`. Caps tokens + USD per slug per day. |

## Scout-inspired patterns (provider abstraction)

Borrowed from Scout RAG's multi-source design. All in `dash/providers/`.

1. **`DatabaseContextProvider` → `BaseProvider` abstraction**
   `dash/providers/base.py`. Every source (local schema, remote DB, file connector)
   implements the same interface: `setup()`, `teardown()`, `engine_ro`, `engine_rw`,
   `agent_scope`, `dialect`, `schema_blob`, `degraded`, `instructions_overlay`.

2. **Read / Write engine split**
   `engine_ro` (read-only) and `engine_rw` (read-write) per provider. Analyst path only
   uses `engine_ro`. Engineer / training path uses `engine_rw`. NullPool on both.

3. **Two-engine kernel-level enforcement**
   `engine_ro` sets `SET TRANSACTION READ ONLY` (or `SET LOCAL transaction_read_only = on`)
   inside the SQLAlchemy `begin` event. Cannot be bypassed by LLM-generated SQL.

4. **Layered defense**
   Regex sandbox (blocks DDL/dangerous DML) + DB enforcement (read-only txn) + RBAC
   (`check_project_permission`). Three layers, any one of which catches a bad query.

5. **Provider lifecycle async**
   `setup()` + `teardown()` are `async`. Setup failures don't raise — provider marked
   `degraded = True` + `last_error` set + logged. Chat session still starts.

6. **Schema-on-demand**
   `dash/providers/trainer.py` creates training artifacts per source under
   `knowledge/{slug}/source_<id>/...`. Codex enrichment, dimensions, doc structure all
   per-source.

7. **Pluggable factory**
   `register_provider_class("postgres_remote", PostgresRemoteProvider)` at module import.
   Registry resolves via `dash_data_sources.provider_class` column. Adding a new connector
   = drop a file in `dash/providers/`, no core changes.

## Dash-original patterns

- **13 context layers per chat** — see `ARCHITECTURE.md` Layer 7. Built by
  `dash/instructions.py:build_analyst_instructions(slug, user_id)`.
- **Hybrid central + per-project pool** — central learning cycle (`project_slug=None`)
  promotes globally; per-project cycles promote to that project's memories only.
- **Per-agent provider scope** — `agent_scope` field on each provider:
  - `project` (or `shared`) — visible to whole team
  - `analyst_only` — Analyst sees, Researcher does not
  - `researcher_only` — Researcher sees (typical for SharePoint/GDrive/OneDrive)
- **7-tier research fan-out** — `ResearcherLoop` runs 7 retrieval tiers in parallel
  (semantic KB, keyword KB, KG, brain, grounded facts, web, external data). Triangulation
  count = how many tiers agree → confidence seed.
- **Forgetting curve + reinforcement** — `learning/forgetting.py` daily decay job.
  Memories decay unless reinforced (referenced in chat, verified, voted up). Decayed
  memories archive, not delete.
- **White-label branding via `/branding/<tenant>/`** — frontend boot reads tenant from
  domain → loads `/branding/<tenant>/{logo.svg,colors.json,copy.json}` overlay. Default
  Dash branding is one tenant under this scheme; no tenant name hardcoded in code.

## Anti-patterns (avoid)

| Anti-pattern | Why bad | What to do instead |
|--------------|---------|--------------------|
| Hardcoding customer / tenant name in code or default docs | Breaks white-label, leaks customer in OSS / screenshots | Use `/branding/<tenant>/` overlay; reference "Tenant" or `${TENANT}` in docs |
| One ontology / glossary for all tenants | Tenants have conflicting meanings for same term | 3-scope Brain (global / project / personal); per-project glossary overrides global |
| Sequential research (`for tier in tiers: tier.run()`) | Wastes 7× wall-clock | `await asyncio.gather(*[tier.run() for tier in tiers])` |
| Bare-name PII detection (`"email"` in column name) | False positives, false negatives | Qualified columns: `(table, column)` tuple, type-aware (TEXT vs UUID) |
| In-process scheduler under HPA | Each pod fires the cron N× | K8S CronJob (one cluster-wide trigger) → POST to `/api/learning/cycle/{slug}` |
| Direct `dash-db` connection | Bypasses PgBouncer, exhausts Postgres conns | `DB_HOST=dash-pgbouncer` always; NullPool on every engine |
| Hardcoding model strings | Breaks `CHAT_MODEL` / `DEEP_MODEL` / `LITE_MODEL` env override | Pull from `dash/settings.py` `TRAINING_CONFIGS` task config |
| Direct OpenRouter calls outside `settings.py` | Skips reasoning_effort + max_tokens + retry/fallback | Always go through `training_llm_call(task_name, prompt)` |
| Loading full file into memory | OOM on large uploads | 1MB streaming chunks, `nrows=` cap on Excel, ghost-row detection |
| Reading raw `dash_data_sources.config` without scope filter | Cross-tenant leak | Always filter `WHERE project_slug = :slug AND status='active'` |
| Setting `search_path` via connection options | PgBouncer drops it silently | `SET LOCAL search_path TO ...` inside `begin` event |
| Amending a published commit | Lost work, broken hooks | Always create a new commit (`git commit`, never `--amend` after push) |
| `git add -A` blindly | Accidentally commits `.env` / credentials | Stage by file: `git add app/foo.py app/bar.py` |
| Bypassing `check_project_permission` for "internal" endpoints | RBAC escape | Decorator on every endpoint, no exceptions |
| Background agent that raises | Crashes streaming response | Wrap every background call in try/except, log + die |

## Recipes (pointers, not code dumps)

For each recipe: **what it does**, **where it lives**, **what to copy**.

### R1 — Per-project NullPool engine with PgBouncer-safe search_path

- **Where**: `db/session.py:_engine_for_project(slug)`
- **Copy**: `create_engine(url, poolclass=NullPool, connect_args={"options": ""})`
  + `event.listens_for(eng, "begin")` callback that runs
  `SET LOCAL search_path TO {quote_ident(schema)}`.
- **Why**: PgBouncer drops `options`. `SET LOCAL` lives only inside the txn.
  TTL eviction caps memory at 200 cached engines.

### R2 — SSE streaming chat endpoint

- **Where**: `app/projects.py:chat`
- **Copy**: `StreamingResponse(gen(), media_type="text/event-stream")` plus
  `asyncio.create_task(run_background_agents(...))` *after* the stream completes.
- **Why**: Background fan-out must never block the user-facing stream. Frontend reads
  via `EventSource` + `ReadableStream` for chunked tokens.

### R3 — Brain context injection (13-layer stack)

- **Where**: `dash/instructions.py:build_analyst_instructions(slug, user_id)`
- **Copy**: assemble the 13 sections, then `_truncate_weighted(parts, max_chars=50_000)`
  with priority order: instructions > semantic model > learnings > examples.
- **Why**: 50K chars ≈ 16K tokens. Logs which sections were truncated so you can tune
  weights when the agent ignores key context.

### R4 — Self-correction loop (Analyst)

- **Where**: `dash/agents/analyst.py:execute_with_self_correction`
- **Copy**: `for attempt in range(1, max_attempts+1)` with three branches —
  zero-rows → `diagnose_zero_rows`, error → `introspect_schema`, suspicious →
  `_count_query` cross-check. On exhaust: `save_learning(sql, "exhausted retries")`.
- **Why**: Every diagnosis becomes a future memory. Limit 3 attempts.

### R5 — ML-keyword rejection in Analyst

- **Where**: Analyst instructions block in `dash/instructions.py`
- **Copy**: hard list `{predict, forecast, anomaly, drivers, cluster, segment, classify,
  decompose, "what will", "how much will"}` with explicit "DO NOT write SQL — return
  'route to Data Scientist'" rule.
- **Why**: Saves 3 SQL retries per misrouted ML question. Also fed into Leader's stuck
  detection.

### R6 — Background agent fan-out

- **Where**: post-chat hook in `app/projects.py:run_background_agents`
- **Copy**: `for coro in tasks: try: await coro except: logger.exception(...)`.
  Never propagate exceptions.
- **Why**: Quality scoring, rule mining, KG triple extraction, etc. all run after the
  user has their answer. One failing agent must not crash the others.

### R7 — ML worker job submission

- **Where**: `app/learning.py:queue_ml_job` writes to `dash_ml_jobs`. Worker
  `ml_worker/main.py` polls every 5s, picks one row, sets `status='running'`, runs,
  updates `done` / `failed`.
- **Copy**: SIGALRM 5min cap, `LIMIT 100,000` on every `SELECT *`, dispose engine in
  `finally`.

### R8 — Connector token storage

- **Where**: `app/sharepoint.py:save_sharepoint_tokens`, mirror in `app/gdrive.py`.
- **Copy**: base64 encode → `INSERT ... ON CONFLICT (project_slug, source_type,
  user_email) DO UPDATE`. Cascade fallback: per-user → workspace → fail.
- **Why**: Tokens stored in `dash_data_sources` for SQL connectors, separate
  `dash_tokens` for OAuth flows. Keep them separate; encryption-at-rest planned.

### R9 — Atomic JSON write

- **Where**: utility used for `grounded_facts.json`, `doc_meta/{file}.json`,
  `dimensions/{table}.json`.
- **Copy**: `tempfile.mkstemp(dir=...)` → write → `os.replace(tmp, path)`. On error
  `os.unlink(tmp)`.
- **Why**: Concurrent uploads must never produce a half-written file.

### R10 — Contextual chunk enrichment (Anthropic pattern)

- **Where**: `app/upload.py:_contextual_enrich_chunks`
- **Copy**: batch 10 chunks per LLM call, prepend 1-2 sentence prefix per chunk,
  cap at 200 chunks (20 batches). Embed enriched, not raw.
- **Why**: 49% retrieval improvement on Anthropic benchmark. Cap prevents runaway
  cost on 500-page PDFs.

### R11 — Auto-evolve every 20 chats

- **Where**: `dash/tools/auto_evolve.py:maybe_run_auto_evolve`
- **Copy**: `if chat_count % 20 != 0 or chat_count == 0: return` guard, then
  `call_llm(prompt, task="auto_evolve")` (uses DEEP_MODEL), version = `chat_count // 20`,
  save to `dash_evolved_instructions`.
- **Why**: Cheap, occasional rewrite of supplementary instructions from accumulated
  learnings. Versioned with reasoning so you can roll back.

### R12 — ECharts auto-detect chart type

- **Where**: `frontend/src/lib/chart-detect.ts` + `dash/tools/visualizer.py`
- **Copy**: rules ladder — 0 numeric → table; 1 row + 1 numeric → kpi; date col → line;
  ≤8 rows + 1 numeric → pie; 1 numeric → bar; ≥2 numeric → scatter.
- **Why**: $0, instant. LLM only invoked when rules fall through.

### R13 — Render-tag rendering (frontend)

- **Where**: `frontend/src/lib/render-tags.ts` + chat pages.
- **Copy**: regex-extract every tag (`[KPI:...]`, `[CONFIDENCE:...]`, etc.), then strip
  the same regex from the markdown body so tags don't render twice.
- **Why**: Same tag vocab in `AGENTS.md`. New tag → register in BOTH chat pages.

### R14 — MCP server exposure (planned)

- **Where**: future endpoint in `app/main.py:/mcp`.
- **Copy**: `tools/list` returns `dash.list_projects`, `dash.run_query`, `dash.search_brain`;
  `tools/call` dispatches to internal helpers under existing RBAC.
- **Why**: Lets Scout / Cursor / Claude call Dash tools as MCP. Stub only — see ROADMAP.

### R15 — Schema slug sanitization

- **Where**: `_sanitize_schema_name(slug)` in `app/projects.py`.
- **Copy**: `_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")`, raise on miss, return
  `f"proj_{slug.replace('-', '_')}"`.
- **Why**: Prevents path traversal AND SQL injection in DDL. Call before any disk path
  or `CREATE SCHEMA` statement.

### R16 — LLM SQL sandbox

- **Where**: `_ai_review_and_fix_table` in `app/upload.py`.
- **Copy**: forbidden = `{DROP, ALTER, TRUNCATE}` regex; UPDATE/DELETE only on target
  table; rollback if `affected_rows > 0.5 * total_rows`.
- **Why**: Three independent layers (regex + target-table check + row-count rollback).
  Any one catches a destructive LLM-generated query.

### R17 — Knowledge Graph SPO write

- **Where**: `dash/tools/knowledge_graph.py:save_triple`.
- **Copy**: `INSERT ... ON CONFLICT (project_slug, subject, predicate, object) DO UPDATE
  SET confidence = GREATEST(EXCLUDED.confidence, dash_knowledge_triples.confidence)`.
  Standardize entities first via fuzzy + LLM.
- **Why**: Idempotent, monotonic confidence. Repeat extractions strengthen, never weaken.

### R18 — Source tracking columns on uploaded tables

- **Where**: `app/upload.py` per-file post-write.
- **Copy**: `ALTER TABLE proj_{slug}.{table} ADD COLUMN _source_file TEXT, ADD COLUMN
  _source_sheet TEXT;` populated during upload.
- **Why**: Lineage queries (`SELECT _source_file, COUNT(*) FROM ... GROUP BY _source_file`)
  + selective re-train on a single source file.

### R19 — Provider registration at import

- **Where**: bottom of every concrete provider in `dash/providers/`.
- **Copy**: `register_provider_class("postgres_remote", PostgresRemoteProvider)` at module
  level. Registry resolves via `dash_data_sources.provider_class` column.
- **Why**: New connector = drop a file. No core changes, no circular imports.

### R20 — kpt time-budgeted experiment

- **Where**: `dash/learning/cycle.py:_process_question`.
- **Copy**: `await asyncio.wait_for(process(qobj), timeout=PER_QUESTION_TIMEOUT_S)`.
  On timeout: log + skip, never crash the cycle.
- **Why**: One slow question can't starve the other 19. 120s cap matches kpt budget.

## Dream Reflection patterns (R21-R25)

Three-tier self-improving agent memory. See `docs/DREAM_CYCLE.md` for deep-dive
and `ARCHITECTURE.md` Layer 4.5 for system overview.

### R21 — Dream Reflection cycle (session-replay)

- **Where**: `dash/learning/dream_reflection.py` (764 LOC) + minion `reflect_sessions` (nightly cron 02:30 UTC).
- **Copy**: 9-step pipeline — budget check (`cost_guard`) → session pull (last 50) → LITE compaction → DEEP synthesis (`finding_type` ∈ decision_rule/anti_pattern/user_persona_delta/workflow_candidate/skill_patch_candidate/curiosity_seed/knowledge_gap) → PII scrub → persist `dash_dream_runs` + `dash_dream_findings` → auto-promote ≥0.85 confidence → bi-temporal reconcile (Graphiti invalidate stale brain + KG) → skill library promote (Voyager) + reflection tree (Generative Agents) + wiki digest (Devin).
- **Why**: Catches patterns no single session sees. Closes loop between past failures and Layer 14 anti-pattern injection. Distinct from kpt curiosity (Layer 5) — reflects internally rather than exploring externally. ~$0.13/proj/night.

### R22 — Bi-temporal fact invalidation (Graphiti pattern)

- **Where**: `dash/learning/bi_temporal.py` (494 LOC); migration 067 adds 4 cols to `dash_company_brain` + `dash_knowledge_triples` via schema-detect DO block (works whether brain lives in `dash`, `public`, or `ai`).
- **Copy**: 4-col schema (`valid_at`, `invalid_at`, `expired_at`, `superseded_by`). Never DELETE — UPDATE `expired_at=now()` on contradiction, link to new row via `superseded_by`. Reads filter `WHERE expired_at IS NULL` by default. Index `idx_brain_active_<schema>` is partial on `WHERE expired_at IS NULL`.
- **Why**: Time-travel queries possible (e.g. "what did the brain say last Tuesday?"). World-state changes preserved as audit trail. Reconciliation cost is migration-time only — runtime reads are unchanged.

### R23 — Voyager-style skill library

- **Where**: `dash/learning/skill_library.py` (586 LOC); table `dash_skill_library` (migration 067).
- **Copy**: Promote proven query patterns (≥3 uses + judge ≥4) → parameterize literals (`WHERE region='APAC' AND year=2025` → `WHERE region={region} AND year={year}`) → store as `dash_skill_library` row w/ NL description + 1536-dim embedding (nullable until embed daemon runs). Retrieve via cosine sim at chat-time, top-5 by `success_count DESC, avg_judge_score DESC`. Inject as Context Layer 15.
- **Why**: Compounding capability over time. Beats raw query_patterns because parameterized (reusable) + NL-retrievable (semantic lookup, not exact-match).

### R24 — ExpeL vote-weighted insight pool

- **Where**: `dash/learning/dream_reflection.py` auto-promote of `decision_rule` findings → `dash_dream_insights`; endpoints under `/dream/insights/*`.
- **Copy**: ADD insights w/ `sha256(insight_text)` hash dedup (`uq_dream_insights_project_hash`). On re-occurrence: UPDATE `upvotes++`. Downvote endpoint auto-deprecates when `downvotes >= upvotes + 5` (`status='deprecated'`). Bounded growth via vote eviction (capped 200/project).
- **Why**: Drift-resistant alternative to free-form prompt rewrite. Measurable confidence over time (upvotes - downvotes = signal). Mem0 4-op schema (ADD/UPDATE/UPVOTE/DOWNVOTE/EDIT) realized in 1 endpoint each.

### R25 — Sleep-time compute anticipated-query cache

- **Where**: `dash/learning/dream_precompute.py` (475 LOC) + minion `precompute_queries` (hourly cron :15). Table `dash_dream_precompute_cache` (migration 068).
- **Copy**: Tier 2 dream-lite predicts top-3 next questions per user/session via LITE_MODEL. Queue → `precompute_queries` minion executes SQL → cache `result_json` + `result_summary` w/ `ttl_until=now()+4h`. Hash key = `sha256(normalized question)`. Layer 16 surfaces cached `result_summary` into next prompt; chat-time match increments `hit_count`.
- **Why**: 5× test-time reduction at iso-accuracy per Letta paper. Direct cache hit at chat-time = sub-second response, $0. Misses fall through to normal SQL path — never blocks.

## Frontend defensive patterns (R26+)

### R26 — Array operations on backend-sourced data MUST be guarded with `Array.isArray()`

- **Where**: any Svelte file iterating / joining / filtering JSONB-sourced fields (codex content, `tables` arrays, LLM-extracted lists)
- **Rule**: replace `(x || []).join(...)`, `(x || []).includes(...)`, `(x || []).filter(...)`, `x?.length` with explicit `Array.isArray(x) && x.length && x.method(...)`
- **Why**: backend may return field as **string** (LLM JSON drift), **object map** (older schema), or **null**. `"foo".length` is truthy (7), `({} || [])` returns `{}` not `[]`. Silent type drift → `.join is not a function` → Svelte aborts hydration mid-render → `$effect` for URL hash never runs → rail highlight + URL desync.
- **Fields known to need guards** (from Cockpit merge debugging 2026-05-18): `data.content.{primary_keys, foreign_keys, usage_patterns, alternate_tables, relationships}`, `queryPlans[].tables`, `insights[].tables`, `trainingRuns[].tables`. Any new field sourced from LLM-generated JSONB.
- **Triage**: console error `*.{join,includes,filter} is not a function` → grep field name in frontend → wrap call site with `Array.isArray()` guard. Don't fix backend type drift — frontend guard is the safety net.
- **Related**: Issue #29 in `CLAUDE.md`, README Troubleshooting "Tab click breaks page".

## Related docs

- `ARCHITECTURE.md` — system layers + data flow (Dream = Layer 4.5)
- `AGENTS.md` — agent inventory + coding rules (Dream minions section)
- `SECURITY.md` — auth, sandboxing, RLS
- `TESTING.md` — eval harness
- `CLAUDE.md` — recent changes, behavior log (Session 2026-05-17)
- `docs/DREAM_CYCLE.md` — Dream Reflection deep-dive (this session)
- `docs/IMPROVE_DASH.md` — self-improvement loop deep dive
- `docs/SLACK_CONNECT.md` — Slack-specific recipe
