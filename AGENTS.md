# Agent Inventory

> All 37 agents in Dash (post 2026-05-26 — 4 verticals added, Data Scientist removed). Pair with `ARCHITECTURE.md` (Layer 4) and `PATTERNS.md`
> (recipes 1-7). Each entry: role, trigger, file, key tools.
>
> Coding rules for human/AI contributors live in the lower half of this doc
> ("Coding rules" section). Read both halves before editing.

## Counts

| Bucket | Count |
|--------|-------|
| Core chat team | 4 |
| Data Scientist | 1 |
| Specialists | 10 |
| Background (LLM-driven) | 7 |
| Background (non-LLM) | 4 |
| Upload team | 5 |
| Visualizer | 1 |
| Smart Router | 1 |
| Learning Cycle Orchestrator | 1 |
| **Total runtime agents** | **30** (LLM) + 4 non-LLM helpers |

## Core Chat Team (4)

### Leader

- **Role**: orchestrator, persona injector, router, result reviewer.
- **File**: `dash/agents/__init__.py` + team factory `dash/team.py`.
- **Triggers**: every chat. Wraps Analyst/Engineer/Researcher/DS in an Agno team.
- **Modes**: FAST (direct SQL) / DEEP (think + analyze) — auto-selected by complexity.
- **Stuck-agent detection**:
  - zero rows from Analyst → Engineer `introspect_schema`
  - ML keyword in Analyst output → re-route to Data Scientist
  - same error 2× → try a different agent
- **Multi-agent fan-out**: if question references data AND documents
  (`and`, `versus`, `compared to`, `report vs actual`), calls BOTH Analyst + Researcher
  and synthesizes.

### Analyst

- **Role**: SQL queries, 11 analysis types, forecasting (LLM fallback), auto-visualization.
- **File**: `dash/agents/analyst.py`.
- **Tools** (31+ total):
  - Per-source: `query_<id>`, `describe_<id>`, `sample_<id>`, `profile_<id>` (emitted by
    `dash/providers/tool_factory.py` per registered provider)
  - 11 analysis tools (`dash/tools/analysis_types.py`): `diagnostic_analysis`,
    `comparator_analysis`, `trend_analysis`, `predictive_analysis`, `prescriptive_analysis`,
    `anomaly_analysis`, `root_cause_analysis`, `pareto_analysis`, `scenario_analysis`,
    `benchmark_analysis`, `descriptive_analysis`
  - `context_loader.load_context(topic)` (10 topics: formulas, aliases, thresholds, patterns,
    domain, quality, relationships, documents, corrections, org)
  - `search_all` (`dash/tools/semantic_search.py`) — 3-tier Cohere rerank + keyword fallback
  - `introspect_schema`, `save_query`, `update_knowledge`, `auto_visualize`
- **Context budget**: 50K chars (~16K tokens), weighted truncation.
- **ML-keyword rejection**: stops on `predict|forecast|anomaly|drivers|cluster|classify|decompose`
  and returns "route to Data Scientist" instead of wasting 3 SQL retries.
- **Self-correction loop**: 3 retries (zero-rows → JOIN diagnosis; error → schema introspect;
  suspicious numbers → COUNT cross-check). Saves learning on exhaust.

### Engineer

- **Role**: views, computed data, dashboard creation, schema operations, table merge.
- **File**: `dash/agents/engineer.py`.
- **Tools**:
  - `create_dashboard` (`dash/tools/dashboard.py`) — programmatic 6-8 widget output,
    real `user_id` threaded for ownership
  - `introspect_schema`, `save_query`, view creation tools
- **Triggers**: `create dashboard`, `make a view`, `merge tables`, post-upload merge phase.

### Researcher

- **Role**: document RAG specialist, multi-signal retrieval.
- **File**: `dash/agents/researcher.py`.
- **Retrieval signals**: semantic (PgVector), keyword (entity aliases), entity (KG),
  cross-reference across documents.
- **File-source tools**: when SharePoint / OneDrive / Google Drive providers are registered,
  Researcher gets `search_<id>`, `fetch_<id>`, `list_folder_<id>` per source. See
  `dash/providers/{sharepoint,onedrive,gdrive}_tools.py`.
- **Grounded facts**: checks `grounded_facts.json` (LangExtract output) first.
- **Triggers**: document keywords, doc-only projects, multi-agent fan-out.

## Data Scientist (1)

- **Role**: ML experiments. Project-aware instructions inject table shapes, past
  experiment outcomes (R² scores), active trained models — no cold start.
- **File**: `dash/agents/data_scientist.py`.
- **Instructions**: `build_data_scientist_instructions(slug)` in `dash/instructions.py`.
- **Tools** (6, was 7 before predict + llm_predict merged):
  1. `predict` — auto-fallback to LLM (DEEP_MODEL, "high" thinking) when no trained model
  2. `feature_importance` — SHAP TreeExplainer + GridSearchCV (18 param combos)
  3. `detect_anomalies_ml` — auto-creates `CREATE VIEW {table}_anomalies`
  4. `classify` — F1 / Precision / Recall / Confusion / CV F1
  5. `cluster` — Silhouette + Calinski-Harabasz
  6. `decompose` — statsmodels seasonal_decompose
- **First call**: `discover_tables` (renamed from `introspect_schema` for less-SQL-feel).
- **Fallback chain**: on tool failure, explains WHY in business language and suggests
  Analyst SQL alternative — never returns raw Python errors.

## Specialists (10)

Triggered by Leader on keywords. All registered as team members in `dash/team.py`.

| # | Agent | Trigger keywords | File |
|---|-------|------------------|------|
| 1 | Comparator | `compare`, `vs`, `versus`, `between` | `dash/agents/__init__.py` |
| 2 | Diagnostician | `why`, `root cause`, `diagnose`, `caused` | same |
| 3 | Narrator | `tell me a story`, `summarize`, `narrative` | same |
| 4 | Validator | `validate`, `check`, `verify`, `is this correct` | same |
| 5 | Planner | `plan`, `roadmap`, `next steps`, `strategy` | same |
| 6 | Trend Analyst | `trend`, `over time`, `growth`, `direction` | same |
| 7 | Pareto Analyst | `top 20%`, `pareto`, `80/15/5`, `concentration` | same |
| 8 | Anomaly Detector | `anomaly`, `outlier`, `unusual`, `spike` | same |
| 9 | Benchmarker | `benchmark`, `industry average`, `vs peers` | same |
| 10 | Prescriptor | `what should`, `recommend`, `action`, `prescribe` | same |

Specialists are surfaced in Settings → AGENTS tab with active/standby status badges.

## Background (7 LLM-driven + 4 non-LLM = 11 total)

All run after every chat via `asyncio.create_task` — fire-and-forget, never block streaming.

| # | Agent | Writes to | LLM? |
|---|-------|-----------|------|
| 1 | Judge | `dash_quality_scores` | yes |
| 2 | Rule Suggester | `dash_suggested_rules` | yes |
| 3 | Proactive Insights | `dash_proactive_insights` | yes |
| 4 | Query Plan Extractor | `dash_query_plans` | yes |
| 5 | Meta Learner | `dash_meta_learnings` | yes |
| 6 | Auto Evolver (every 20 chats) | `dash_evolved_instructions` | yes (DEEP_MODEL) |
| 7 | Chat Triple Extractor | `dash_knowledge_triples` | yes |
| 8 | Auto-Memory Promoter | `dash_memories` (`source='auto_learned'`) | no (rule-based) |
| 9 | User Preference Tracker | `dash_user_preferences` | no (counter merge) |
| 10 | Episodic Memory Extractor | `dash_memories` (`source='episodic'`) | no (regex) |
| 11 | Follow-up Suggester | frontend (returned in chat response) | yes (LITE_MODEL) |

Background agents that throw log + die — never propagate.

## Upload Team (5)

`dash/agents/{conductor,parser,scanner,vision_agent,inspector}.py`. Tools live in
`dash/tools/upload_tools.py` (20 total, 4 categories: parser/scanner/vision/inspector).

| Agent | Role | Files |
|-------|------|-------|
| **Conductor** | Upload orchestrator. Sees all files, plans, assigns, retries | `conductor.py` |
| **Parser** | Excel/CSV/JSON: header detect, unpivot months, split multi-table sheets | `parser.py` |
| **Scanner** | PDF/PPTX/DOCX/TXT: text + table extraction, OCR (Tesseract), Vision for charts | `scanner.py` |
| **Vision** | JPG/PNG: Tesseract first, Vision LLM fallback for charts/diagrams | `vision_agent.py` |
| **Inspector** | Quality gate: profile cols, dupes, health score, retry trigger | `inspector.py` |

Flow: Conductor → Parser/Scanner/Vision per file → Engineer merge phase →
Inspector validate → if pass DELETE originals, else KEEP → Engineer relationships.

## Visualizer (1)

- **File**: `dash/tools/visualizer.py`.
- **Registered as tool** `auto_visualize` on Analyst.
- **Detection**: rules engine first ($0, instant), LLM fallback. 8 chart types:
  bar, line, pie, grouped_bar, scatter, kpi, histogram, heatmap.
- **Output**: complete ECharts config JSON.

## Smart Router (1)

- **File**: `dash/agents/router.py` + `dash/tools/router_tools.py`.
- **Role**: 2-tier routing for the cross-project Dash Agent (`/chat`, no slug bound).
- **Tier 1**: instant keyword scoring, 7 signals (agent name, table name, column name,
  persona, session continuity, user history, brain alias). $0, ~0ms.
- **Tier 2**: Router Agent with 4 tools. Triggered when top-2 scores are within 2 points
  ("tie detection"). LITE_MODEL, < 1.5s, ~$0.001.
- **Router tools**:
  1. `inspect_catalog` — pre-built project catalog, 0ms
  2. `inspect_project_detail(slug)` — Codex-enriched metadata
  3. `search_brain(terms)` — Company Brain glossary/aliases/org lookup
  4. `check_session_context` — session continuity (last project, last topic)
- **Session slug** saved after routing for follow-up turns.

## Learning Cycle Orchestrator (1)

- **File**: `dash/learning/cycle.py` (`LearningCycle` class).
- **Role**: chains all 17 learning modules into one async run.
- **Modules orchestrated** (`dash/learning/`):
  - `goals.py` — load `learning_goals.md` per project
  - `curiosity.py` — generate N=20 questions
  - `researcher.py` + `external_data.py` + `web_search.py` — 7-tier parallel research
  - `hypothesis.py` — form hypotheses from dossier
  - `verifier.py` — verify, compute confidence delta
  - `consolidator.py` — promote to `dash_memories`
  - `forgetting.py` — daily decay job
  - `promotion.py` — central / project-N promotion
  - `digest.py` — today's discoveries digest
  - `agent_iq.py` — composite IQ snapshot
  - `cost_guard.py` — per-project daily cost cap
  - `lineage.py` — `parent_hypothesis_id` tree
  - `scheduler.py` — cron entry point (called by `/api/learning/cycle/{slug}`)
  - `base.py` — `CycleResult`, `VerificationStatus`, `TrainEvent` dataclasses
- **Output**: async iterator yielding `TrainEvent`-shaped dicts for SSE.
- **Constraints**: `PER_QUESTION_TIMEOUT_S=120s`, dry-run mode for Sunday canary
  (force-disables LLM, max 5 questions, $0).

## Dream Reflection minions (5)

Background minions added in 2026-05-17 session. All run via the existing
`dash_minions` queue (not the `asyncio.create_task` fire-and-forget pattern of
the chat background agents). Distinct from kpt curiosity loop — these reflect
on internal session traces rather than exploring external hypotheses. See
`docs/DREAM_CYCLE.md` for deep-dive.

| Kind | Trigger | Role | Cost |
|------|---------|------|------|
| `reflect_sessions` | nightly cron 02:30 UTC | Pull last 50 sessions, LITE compaction → DEEP synthesis → PII scrub → persist findings → auto-promote ≥0.85 confidence → bi-temporal reconcile → skill library promote → reflection tree + wiki digest (Tier 3) | ~$0.13/proj |
| `dream_lite` | between-turn (poignancy threshold OR N-step OR idle debounce) | LITE persona update (Letta MemoryBlock) + anticipated-query precompute queue (Letta sleep-time compute) (Tier 2) | ~$0.005 |
| `poignancy_capture` | recovery batch (backup capture if hot-path hook missed turns) | Rule-based 1–10 poignancy score → `dash_episode_buffer` (Tier 1) | $0 |
| `precompute_queries` | hourly cron :15 | Execute pending precompute cache SQL (sleep-time compute); cache w/ 4h TTL → Layer 16 inject | ~$0 (SQL only) |
| `ab_revert_check` | daily cron 04:00 UTC | A/B test promoted anti-patterns / skills / insights after 7d observation; revert on judge-score regression (`score_after < score_before - delta`) | $0 (no LLM) |

Handlers in `dash/learning/dream_*.py`. Tables: `dash_dream_runs`,
`dash_dream_findings`, `dash_dream_insights`, `dash_anti_patterns`,
`dash_skill_library`, `dash_dream_digests`, `dash_dream_personas`,
`dash_dream_reflection_tree`, `dash_episode_buffer`, `dash_dream_lite_runs`,
`dash_dream_precompute_cache`, `dash_ab_revert_runs`, `dash_ab_revert_events`.

Optional disable: `DREAM_REFLECTION_DISABLED=1` env kills all 5 minions + cron.

## Future agents (planned)

- **MCP provider** — expose any MCP server as an in-team agent. Stub in `PATTERNS.md` §15.
- **Web fetch tool** — live URL → answer, used by Researcher for time-sensitive questions.
- **Slide Agent v3** — currently endpoint-only (`/api/export/slides-agent`); planned promotion
  to first-class team member with persona-aware theme picking.

---

# Coding rules

> Read this before editing code. Hard rules — break and the build / runtime breaks.

## Repository layout

```
app/                  FastAPI routes (auth, projects, upload, learning, brain, connectors,
                      sharepoint, gdrive, schedules, dashboards, scores, suggested_rules,
                      rules, export)
dash/
  agents/             Agno agents (analyst, engineer, researcher, router, data_scientist +
                      conductor/parser/scanner/vision_agent/inspector)
  context/            semantic_model + business_rules
  learning/           17 modules — kpt autoresearch loop
  providers/          BaseProvider + 7 subclasses + registry + tool_factory + trainer
  tools/              30+ tools (build, dashboard, introspect, save_query, judge,
                      proactive_insights, query_plan_extractor, meta_learning, auto_evolve,
                      knowledge_graph, visualizer, analysis_types, context_loader,
                      router_tools, semantic_search, upload_tools)
  team.py             Team factory (persona injection)
  settings.py         Shared config + training_llm_call + training_vision_call
  instructions.py     Dynamic instructions builder
db/
  session.py          Engine cache, embedder, get_active_embedding_model
  models.py           SQLAlchemy models for 35+ dash_* tables
ml_worker/            Separate container for heavy ML jobs (1GB cap)
frontend/src/         SvelteKit 5 SPA (brutalist CLI aesthetic)
evals/                run.py, smoke.py, improve.py + cases/
helm/dash/            17 templates + values.yaml + values-prod.yaml + values-dev.yaml
k8s/                  24 raw manifests, ordered by numeric prefix
knowledge/{slug}/     Per-project artifacts (now also per-source: source_<id>/...)
branding/<tenant>/    White-label overlay
scripts/              Maintenance scripts
docs/                 Deep-dive guides (SLACK_CONNECT, IMPROVE_DASH, TEST_QUESTIONS)
```

## Connection layer (load-bearing)

All DB connections route through PgBouncer in transaction mode.

- `DB_HOST=dash-pgbouncer` always. Never `dash-db` direct, never `localhost`.
- `poolclass=NullPool` on every `create_engine()` — PgBouncer owns pooling.
- Set session vars (`search_path`, `read_only`) via `SET LOCAL` inside transactions, in a
  SQLAlchemy `begin` event listener. Never via connection options (PgBouncer drops them).
- Bootstrap engines (schema creation) use `NullPool` and `.dispose()` immediately.
- Per-project engines cached with TTL eviction (1hr, max 200).
- `dispose()` engines in `finally` blocks (see `ml_models.py`, also enforced in providers).

Violate any of the above → connection exhaustion under load.

## Auth + tenancy

- Project schema = `proj_{slug_sanitized}`. Use `_sanitize_schema_name()` before any DDL.
- All endpoints take a project slug. Use `check_project_permission(slug, role)`. Never trust
  frontend-supplied user_id.
- Token cache thread-safe with `threading.Lock`. TTL eviction.
- 3 roles: viewer / editor / admin. Frontend hides via `canEdit`/`canAdmin`; backend enforces.

## Models

3-model trio, env-configurable:

| Var | Default | Use case |
|-----|---------|----------|
| `CHAT_MODEL` | `google/gemini-3-flash-preview` | chat agents, SQL, vision, Q&A, dashboard |
| `DEEP_MODEL` | `openai/gpt-5.4-mini` | deep analysis, relationships, domain knowledge, auto-evolve, Excel structure, ml_prediction |
| `LITE_MODEL` | `google/gemini-3.1-flash-lite-preview` | scoring, routing, extraction, meta-learning |
| `EMBEDDING_MODEL` | `google/gemini-embedding-2-preview` | cascade: Gemini → OpenAI large → OpenAI small → Cohere v4 |

Never hardcode model strings. Pull from `dash/settings.py` `TRAINING_CONFIGS` or env.
Direct OpenRouter calls forbidden outside `settings.py`.

## SQL safety

- Parameterized queries everywhere. No string concat.
- LLM SQL passes through `_ai_review_and_fix_table()` sandbox: blocks `DROP/ALTER/TRUNCATE`,
  allows `UPDATE/DELETE` only on target table, rolls back if >50% rows affected.
- Read-only enforcement via `SET LOCAL transaction_read_only = on` on the Analyst path.
- View creation validation against allowlist of column patterns.
- PostgreSQL reserved word escaping (use `quote_ident`).

## Provider rules

- Concrete providers `register_provider_class("name", Cls)` at import time.
- `BaseProvider.setup()` failures must not raise — set `degraded=True` and log.
- Provider `engine_ro` and `engine_rw` both `NullPool`. Dispose on registry replace.
- Per-source tools emitted in `dash/providers/tool_factory.py` — name pattern
  `{op}_{source_id}` (e.g. `query_27`, `search_14`).
- Token-based providers (gdrive/sharepoint/onedrive) keep credentials in `dash_tokens`,
  not `dash_data_sources.config` (that field is for SQL connection strings only).

## Background work

Fire-and-forget via `asyncio.create_task`. Never block streaming. If a background agent
throws, it logs and dies — never propagates.

## ML Worker

- 1GB RAM cap.
- 5-min `SIGALRM` per job. On timeout: mark `failed`, log, exit.
- `LIMIT 100,000` on every `SELECT *`.
- `engine.dispose()` in `finally` blocks.
- `compose.yaml` depends on `dash-pgbouncer` (not `dash-db` direct).

## Render-tag vocabulary

Agents emit inline; frontend renders. Mirror render path in BOTH chat pages — do not diverge.

| Tag | Render |
|-----|--------|
| `[KPI:value\|label\|change]` | big number card with delta colour |
| `[CONFIDENCE:HIGH\|MEDIUM\|LOW]` | progress bar (green / orange / red) |
| `[IMPACT:pct\|recovered\|total]` | bordered card with progress |
| `[RELATED:question]` | clickable suggestion button |
| `[CHART:title]` | chart caption hint |
| `[CLARIFY:opt1\|opt2]` | clickable option cards |
| `[ROUTING:agent_name]` | routing badge |
| `[DASHBOARD:id]` | side panel auto-opens |
| `[REF:table:row]` | citation chip |
| `[UP:+5%]` `[DOWN:-2%]` `[FLAT:0]` | trend badges |

## Test commands

```bash
python -m evals.smoke      # smoke
python -m evals.run        # full evals
python -m evals.improve    # self-improvement loop
./stress_test.sh           # 200 concurrent users
```

## Never-do list

- Never set `DB_HOST=localhost`.
- Never run `docker compose down -v` in production (deletes volumes).
- Never disable PgBouncer.
- Never hardcode model strings.
- Never call OpenRouter outside `settings.py`.
- Never read full file into memory (streaming chunks only).
- Never bypass `check_project_permission`.
- Never log secrets / tokens / passwords.
- Never `git push --force` to main.
- Never hardcode customer/tenant name in code or default docs (use `/branding/<tenant>/`).

## Schema rules

Two PostgreSQL schemas — `public` and `dash`. Putting a table in the wrong
schema bypasses the Engineer write-guard and triggers silent search-path drift.
Full reference: `docs/SCHEMA_LAYOUT.md`. Audit: `python scripts/audit_schema_split.py`.

Canonical home schema per `dash_*` table category:

| Category | Home schema |
|---|---|
| System (auth, projects, audit, tokens) | `public` |
| Brain (`dash_company_brain`, `dash_brain_*`) | `public` |
| Learning v1/v2 (memories, hypotheses, dossiers, scratchpad) | `dash` |
| Vectors (`dash_vectors`, `dash_vector_audit`) | `dash` |
| Knowledge Graph (`dash_knowledge_triples`) | **BOTH** — chat-time writer in `public`; bi-temporal copy in `dash`. Always schema-qualify. |
| Dream Reflection / Skill Library / Bi-Temporal | `dash` |
| Ontology Workbench (`dash_template_*`, `dash_autonomous_workflows`, `dash_ontology_*`) | `public` |
| Connectors (`dash_data_sources`) | `public` |
| Provider scratch | `dash` |
| Per-tenant project data | `user_<id>` / `<project_slug>` |

Rules:

- **Always schema-qualify `dash_*` tables.** Never rely on search_path.
- **Engineer can't write to `public`.** Use the app-tier engine for writes to brain / ontology / system tables.
- **`dash_company_brain` lives in `public`.** Migration 067 detects the schema via `information_schema.tables` and applies bi-temporal ALTERs in-place; don't hard-code `dash.dash_company_brain`.
- **`dash_knowledge_triples` has two copies.** They are independent. New code → `public.dash_knowledge_triples` unless you specifically want the bi-temporal `dash` copy.
- Before adding any new `dash_*` table or migration, run the audit script. If it reports a new split or CREATE conflict, update `docs/SCHEMA_LAYOUT.md` and the `KNOWN_INTENTIONAL_SPLITS` allowlist in the script before merging.

## When stuck

1. `CLAUDE.md` — recent changes, behavior log
2. `ARCHITECTURE.md` — layers, data flow
3. `PATTERNS.md` — reusable recipes
4. `SECURITY.md` — threat model
5. Search code for similar pattern, copy + adapt.
