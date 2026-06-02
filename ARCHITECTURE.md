# Dash Architecture

> System architecture, data flow, deployment topology.
> Audience: engineers + AI agents working on the codebase.
> Pair with: `AGENTS.md` (rules), `PATTERNS.md` (recipes), `CLAUDE.md` (recent changes).

## High-level system

```
                              Internet
                                 │
                                 ▼
                         ┌───────────────┐
                         │    Caddy 2    │  auto-SSL · HSTS · 512M cap
                         │  (or Ingress) │  X-Frame · nosniff · XSS
                         └───────┬───────┘
                                 │
                                 ▼
                         ┌───────────────┐
                         │   FastAPI     │  Uvicorn · 8 workers default
                         │  dash-api     │  36+ endpoints · RBAC
                         │ (HPA 3-10)    │  AuthMiddleware + SlowAPI
                         └───────┬───────┘
                                 │
                                 ▼
                         ┌───────────────┐
                         │  PgBouncer    │  txn pool · scram-sha-256
                         │ dash-pgbouncer│  3000 client / 200 db / 80 default pool
                         └───────┬───────┘
                                 │
                                 ▼
                ┌────────────────────────────────┐
                │  PostgreSQL 18 + pgvector      │
                │  dash-db                        │
                │  300 max_conn · 1G shared_buf   │
                │  35+ dash_* tables              │
                │  N proj_{slug} schemas          │
                └────────────────────────────────┘
                                 ▲
                                 │ (also through pgbouncer)
        ┌────────────────────────┴────────────────────────┐
        │                                                  │
┌───────┴───────┐                              ┌──────────┴──────────┐
│  ML Worker    │                              │   K8S CronJobs       │
│  dash-ml      │                              │  daily-learning      │
│  1G cap       │                              │  sunday-canary (dry) │
│  polls        │                              │  daily-decay         │
│  dash_ml_jobs │                              │  → POST /learning/   │
│  SIGALRM 5min │                              │     cycle/{slug}     │
│  LIMIT 100K   │                              └──────────────────────┘
└───────────────┘
```

Four runtime containers in Compose: `dash-api`, `dash-pgbouncer`, `dash-db`, `dash-ml` (+ `caddy`).
On K8S the same four become Deployments / StatefulSet, and three CronJobs are added.

## Components

### Layer 1: Edge

- **Caddy 2** — auto-SSL via Let's Encrypt, HSTS, `X-Frame-Options`, `X-Content-Type-Options: nosniff`,
  XSS header, 512M memory cap, 250MB request body, 300s read/write timeout.
- **Optional Nginx Ingress + cert-manager** — used in K8S deployments instead of Caddy. See
  `helm/dash/templates/ingress.yaml`.

### Layer 2: API

- **FastAPI app** (`app/main.py`) — Uvicorn, 8 workers default (`WORKERS` env var).
- **36+ endpoints**, all RBAC-enforced via `check_project_permission(slug, role)`.
- **AuthMiddleware** — token-cookie auth, skip-paths for OAuth callbacks, public endpoints,
  static assets.
- **Rate limiter** — SlowAPI, default `500/minute`, configurable via `RATE_LIMIT` env.
- **3 roles**: viewer (chat only), editor (upload + train), admin (all).

### Layer 3: Provider Registry

`dash/providers/` — every data source (local schema, remote DB, file connector) is a `BaseProvider`
instance. Per-project + per-source, deduped by `(project_slug, provider_id)`, thread-safe.

```
ProviderRegistry  (singleton, threading.Lock)
  └── (slug, id) → BaseProvider
                     ├── engine_ro           NullPool, read-only
                     ├── engine_rw           NullPool, read-write
                     ├── agent_scope         project | analyst_only | researcher_only | shared
                     ├── dialect             postgresql | mysql | mssql | none
                     ├── schema_blob         table list, columns, types
                     ├── degraded            bool
                     └── instructions_overlay  per-source prompt fragment
```

7 concrete subclasses (`dash/providers/*.py`):

| Class | Source type | Engine | Agent scope default |
|-------|-------------|--------|---------------------|
| `postgres_local` | local `proj_{slug}` schema | NullPool | shared |
| `postgres_remote` | external Postgres via `dash_data_sources` | NullPool | project |
| `mysql_remote` | external MySQL | NullPool | project |
| `fabric` | Microsoft Fabric / SQL Server TDS | NullPool | project |
| `sharepoint` | Graph API + MSAL OAuth | none | researcher_only |
| `onedrive` | Graph API | none | researcher_only |
| `gdrive` | Drive API v3 | none | researcher_only |

Setup failures don't bubble — provider is marked `degraded` and the chat session still starts.

### Layer 4: Agent Team

30 agents total. Full inventory in `AGENTS.md`. Topology:

```
            User question
                 │
                 ▼
         ┌──────────────┐
         │ Smart Router │  Tier 1: keyword score (7 signals, $0)
         │  (2-tier)    │  Tier 2: Router Agent w/ 4 tools (LITE_MODEL, ~$0.001)
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │    Leader    │  FAST / DEEP mode, persona injected
         │  orchestrator│  stuck-agent detection, multi-agent fan-out
         └──────┬───────┘
                │
   ┌────────────┼─────────────────┬─────────────────┐
   ▼            ▼                 ▼                 ▼
Analyst     Engineer          Researcher      Data Scientist
31+ tools   views/dashboards  doc RAG         6 ML tools
50K ctx     create_dashboard  multi-signal    project-aware
            tool              retrieval
   │
   ├── 10 specialists (Comparator, Diagnostician, Narrator, Validator,
   │   Planner, Trend, Pareto, Anomaly, Benchmarker, Prescriptor)
   │
   └── Visualizer (auto_visualize tool, 8 chart types)

After response stream completes, fire-forget:
   11 background agents (Judge, Rule Suggester, Proactive Insights,
   Query Plan Extractor, Meta Learner, Auto Evolver, Chat Triple Extractor,
   Auto-Memory, User Pref, Episodic, Follow-up)

Upload pipeline (separate trigger):
   Conductor → Parser + Scanner + Vision + Inspector → Engineer
```

Closed-loop self-correction (Analyst): up to 3 retries, schema introspect on error,
JOIN diagnosis on zero-rows, COUNT cross-check on suspicious numbers, save learning on exhaust.

### Layer 4.5: Dream Reflection Subsystem

Three-tier self-improving agent memory system. Distinct from kpt curiosity loop
(Layer 5) — that explores external hypotheses; this reflects on internal session
traces. Inspired by Letta sleep-time compute, Mem0 4-op schema, Graphiti
bi-temporal, ExpeL vote pool, Voyager skill library, Generative Agents reflection
tree, Devin wiki digest, HippoRAG retrieval.

```
┌──────────────────────────────────────────────────────────────────┐
│  TIER 1 — per-turn poignancy capture (rule-based, $0)            │
│    chat hot-path → dash_episode_buffer (rolling LRU 1000/proj)   │
│         │                                                          │
│         ▼ (poignancy ≥ threshold OR N-step OR idle debounce)     │
│  TIER 2 — between-turn dream-lite (LITE_MODEL, ~$0.005/cycle)    │
│    dash/learning/dream_lite.py                                    │
│      ├─ persona update      → dash_dream_personas                 │
│      └─ precompute queue    → precompute_queries minion           │
│                                  ↓                                 │
│                          dash_dream_precompute_cache (TTL 4h)    │
│                                  ↓                                 │
│                          Context Layer 16 (sub-second cache hit) │
│                                                                    │
│  TIER 3 — nightly cron 02:30 UTC (DEEP_MODEL, ~$0.13/proj)       │
│    K8s CronJob dream-reflect-nightly                              │
│      → POST /dream/cycle-all                                      │
│      → dash/learning/dream_reflection.py (9-step pipeline)        │
│         1. budget check (cost_guard)                              │
│         2. session pull (last 50)                                 │
│         3. LITE compaction                                        │
│         4. DEEP synthesis → findings                              │
│         5. PII scrub                                              │
│         6. persist → dash_dream_runs + dash_dream_findings        │
│         7. auto-promote ≥0.85 → dash_dream_insights (ExpeL pool) │
│                              + dash_anti_patterns (Layer 14)     │
│         8. bi-temporal reconcile (Graphiti)                       │
│            → invalidate stale brain + KG triples                 │
│         9. skill library promote (Voyager) → Layer 15             │
│            + reflection tree (Generative Agents, depth 1+2)      │
│            + wiki digest (Devin) → dash_dream_digests             │
│                                                                    │
│  A/B REVERT — daily 04:00 UTC ($0, no LLM)                       │
│    dash/learning/dream_ab_revert.py                               │
│      → rescore promoted items after 7d observation                │
│      → revert if score_after < score_before - delta              │
│      → dash_ab_revert_runs + dash_ab_revert_events                │
└──────────────────────────────────────────────────────────────────┘
```

| Tier | Trigger | LLM | Cost | Primary output |
|------|---------|-----|------|----------------|
| 1 | per-turn (chat hot-path) | none | $0 | `dash_episode_buffer` |
| 2 | between-turn (debounced) | LITE_MODEL | ~$0.005 | `dash_dream_personas` + precompute queue |
| 3 | nightly 02:30 UTC | DEEP_MODEL | ~$0.13/proj | findings, insights, anti-patterns, skills, digest, reflection tree |
| A/B | daily 04:00 UTC | none | $0 | revert audit |

Modules (all in `dash/learning/`):
`dream_reflection.py` (P1, 764 LOC), `reflection_tree.py` (P2, 378 LOC),
`dream_digest.py` (P2, 550 LOC), `bi_temporal.py` (P3, 494 LOC),
`skill_library.py` (P3, 586 LOC), `dream_poignancy.py` (P4, 531 LOC),
`dream_lite.py` (P4, 483 LOC), `dream_precompute.py` (P4, 475 LOC),
`dream_ab_revert.py` (P5, 651 LOC).

Tables (migrations 066–069):
`dash_dream_runs`, `dash_dream_findings`, `dash_dream_insights`,
`dash_anti_patterns`, `dash_skill_library`, `dash_dream_digests`,
`dash_dream_personas`, `dash_dream_reflection_tree`, `dash_episode_buffer`,
`dash_dream_lite_runs`, `dash_dream_precompute_cache`, `dash_ab_revert_runs`,
`dash_ab_revert_events`. Plus bi-temporal columns
(`valid_at`/`invalid_at`/`expired_at`/`superseded_by`) on `dash_company_brain` +
`dash_knowledge_triples` (Graphiti pattern: never delete, only invalidate).

Feeds Context Layers 14 (anti-patterns), 15 (proven skills), 16 (precompute
cache hints) → see Layer 7 below. Surfaces in Settings → SELF-LEARN → 🌙
DREAMING (11 sub-views). 30+ endpoints under `/api/projects/{slug}/dream/*`.

Deep-dive: `docs/DREAM_CYCLE.md`.

### Layer 5: Learning Subsystem

kpt autoresearch loop. `dash/learning/` — 17 modules.

```
┌──────────────────────────────────────────────────────────┐
│ K8S CronJob (daily) → POST /api/learning/cycle/{slug}    │
│                                                           │
│  CuriosityEngine.generate(N=20)                          │
│         │                                                 │
│         ▼                                                 │
│  ResearcherLoop.research_async(q)   ◄─ 7 parallel tiers  │
│         │       (asyncio.gather, triangulation count)    │
│         ▼                                                 │
│  HypothesisEngine.form_from_dossier()                    │
│         │                                                 │
│         ▼                                                 │
│  Verifier.verify(h)         ◄─ confidence delta           │
│         │                                                 │
│         ▼                                                 │
│  Consolidator.consolidate() ◄─ promote to memories        │
│         │                                                 │
│         ▼                                                 │
│  forgetting.daily_decay_job()                            │
│         │                                                 │
│         ▼                                                 │
│  promotion (central cycle only, or every Nth project)    │
│         │                                                 │
│         ▼                                                 │
│  digest + agent_iq snapshot                              │
└──────────────────────────────────────────────────────────┘
```

Constraints:
- `PER_QUESTION_TIMEOUT_S = 120s` (kpt time budget per experiment).
- Per-project daily cost cap (CostGuard module).
- Triangulation count seeds confidence; more agreeing tiers → higher promotion priority.
- Hybrid pool: central learning cycle (`project_slug=None`) + per-project cycles.
- Sunday canary cycle runs `dry_run=True` → no LLM, deterministic baseline, $0.

Streams `TrainEvent`-shaped dicts via async generator for SSE progress.

### Layer 6: ML Worker

`ml_worker/main.py` — separate Docker container, 1GB RAM cap.

```
dash_ml_jobs                  ml_worker (poll every 5s)
┌──────────────┐              ┌────────────────────────┐
│ id           │              │ pick one row           │
│ project_slug │   ◄─────►    │ status='running'       │
│ model_type   │              │ run model              │
│ params jsonb │              │ SIGALRM 5min           │
│ status       │              │ LIMIT 100,000          │
│ result       │              │ status='done'/'failed' │
└──────────────┘              │ engine.dispose finally │
                              └────────────────────────┘
```

6 ML tools (Data Scientist agent calls them):
1. `predict` — auto-fallback to LLM when no trained model exists
2. `feature_importance` — SHAP TreeExplainer + GridSearchCV (18 param combos)
3. `detect_anomalies_ml` — auto-creates `CREATE VIEW {table}_anomalies`
4. `classify` — F1 / Precision / Recall / Confusion / CV F1
5. `cluster` — Silhouette + Calinski-Harabasz
6. `decompose` — statsmodels seasonal_decompose

SHAP per-row values for top-5 rows saved in experiment `result_data.shap_values`.
Scheduled retrain daemon: every 24h, all active models.

### Layer 7: Knowledge Layer

16 context layers per chat (extends OpenAI in-house data agent architecture
with Dream Reflection layers 14–16):

```
1.  Table Usage + proven query patterns       dash_query_patterns
2.  Human Annotations (override LLM)          dash_annotations
3.  Codex-Enriched Knowledge                  enrichment pipeline (purpose/grain/PK/FK)
4.  Institutional Knowledge                   PgVector hybrid search
5.  Memory (3 scopes)                         dash_memories (personal/project/global)
6.  Runtime Context                           live introspect_schema
7.  Grounded Facts                            grounded_facts.json (LangExtract)
8.  Table Usage (rerun, post-narrowing)       dash_query_patterns
9.  Human Annotations (rerun)                 dash_annotations
10. Self-Correction Strategies                dash_meta_learnings
11. Evolved Instructions (versioned)          dash_evolved_instructions
12. Knowledge Graph (entity → table map)      dash_knowledge_triples
13. Company Brain (3-scope)                   dash_company_brain
14. Anti-Patterns (Dream Reflection)          dash_anti_patterns (top-10 active)
15. Proven Skills (Voyager skill library)     dash_skill_library (top-5 active)
16. Precompute Cache Hints (sleep-time)       dash_dream_precompute_cache (TTL 4h)
```

Total budget 50K chars (~16K tokens). Weighted truncation:
instructions > semantic model > learnings > examples. Logs when sections truncated.

- **Codex enrichment** — purpose/grain/PK/FK/usage-patterns/freshness per table, multiple LLM calls
  during training, injected into Analyst's semantic model.
- **Knowledge Graph** — SPO triples, source_uri tagged, entity standardized via fuzzy + LLM,
  community detection (BFS), continuous learning via `extract_chat_triples()` after every chat.
- **Company Brain** — 3-layer (global / project / personal). Glossary, formulas, aliases, patterns,
  org structure, thresholds, calendar.
- **Memories** — source-scoped (`auto_learned`, `episodic`, `agent`, `user`, `consolidated`,
  `langextract`, `transferred`, `mined`), decay-managed by forgetting module.
- **LangExtract** — grounded facts with character positions, KPIs/metrics/decisions/risks.

### Layer 8: Cross-Source Federation
- Parser (sqlglot) → resolver (intra-project only) → splitter →
  parallel executor → merge (DuckDB / pandas)
- Hard tenant isolation: registry/scope/RBAC checks
- Circuit breaker (3 failures, 5 min cooldown)
- Self-correction (3 retry strategies)
- File source executor for PPTX/PDF/XLSX tables
- See `docs/FEDERATION.md` for full reference

## Data flow

### Chat query path

```
User
  │ POST /{slug}/chat (SSE)
  ▼
AuthMiddleware → check_project_permission
  │
  ▼
Smart Router (keyword tier; if tied, Router Agent)
  │
  ▼
Leader (persona + 13 ctx layers + multi-agent rules)
  │
  ├──► Analyst — search_all → SQL → self-correct loop → auto_visualize
  │      │         │            │       │
  │      │         ▼            ▼       ▼
  │      │     PgVector+KG   provider  visualizer (rules + LLM fallback)
  │      │     +Brain+Facts  engine_ro
  │      │
  ├──► Engineer — create_dashboard / introspect / save_query
  ├──► Researcher — multi-signal retrieval (semantic+keyword+entity+cross-ref)
  └──► Data Scientist — discover_tables → 6 ML tools → ml_worker
  │
  ▼
Guards: PII auto-detect + mask, audit log row
  │
  ▼
Format: KPI/CONFIDENCE/IMPACT/RELATED tags, inline charts
  │
  ▼
SSE stream → user
  │
  └─► (after stream) asyncio.create_task → 11 background agents
```

### Self-learning cycle path

```
K8S CronJob (3:00 UTC)
  │ POST /api/learning/cycle/{slug}
  ▼
LearningCycle.run()  (async iterator, yields TrainEvent dicts)
  │
  ├─ CuriosityEngine.generate(N=20)         curiosity.py
  ├─ for each q (asyncio.gather, 120s cap):
  │    ├─ ResearcherLoop.research_async    researcher.py + external_data.py + web_search.py
  │    │    └─ 7 parallel tiers, triangulation_count seeds confidence
  │    ├─ HypothesisEngine.form_from_dossier  hypothesis.py
  │    ├─ Verifier.verify                  verifier.py
  │    └─ Consolidator.consolidate         consolidator.py (writes dash_memories)
  │
  ├─ forgetting.daily_decay_job            forgetting.py
  ├─ promotion (central or every Nth)      promotion.py
  ├─ digest + agent_iq snapshot            digest.py + agent_iq.py
  └─ persist run row → dash_self_learning_runs

Cost-capped: cost_guard.py per project per day.
Time-capped: PER_QUESTION_TIMEOUT_S = 120s (kpt budget).
```

### Training pipeline path

`POST /train` triggers 14 steps (data) or 18 steps (doc-only). Steps tracked in
`dash_training_runs` with format `step_name|table_name|index|total`:

```
1.  catalog              SQL profile all columns (zero RAM)
2.  profile              MIN/MAX/AVG/percentiles
3.  dim catalog          SELECT DISTINCT < 500 unique → dimensions/{table}.json
4.  hierarchy            parent/child mapping
5.  sample               3 start + 3 mid + 3 end + outliers + nulls
6.  codex enrich         purpose/grain/PK/FK/usage/freshness  (LLM)
7.  Q&A verify           generate Q&A, execute SQL, save verified
8.  relationships        cross-table joins (LLM + verify by overlap)
9.  persona              project persona from data shape
10. domain knowledge     glossary/calc/value-maps/KPI/quality/neg-examples (6 sub-steps)
11. KG triples           SPO extraction, entity standardize, community detect
12. LangExtract          grounded facts w/ char positions
13. drift baseline       schema + value-distribution snapshot
14. watermark register   register provider with registry, emit per-source tools
```

## Database schema (35+ tables)

| Group | Tables |
|-------|--------|
| **System** | `dash_users`, `dash_tokens`, `dash_projects`, `dash_project_shares`, `dash_chat_sessions` |
| **Content** | `dash_dashboards`, `dash_schedules`, `dash_quality_scores`, `dash_suggested_rules`, `dash_audit_log`, `dash_notifications`, `dash_presentations` |
| **Self-Learning v1** | `dash_memories`, `dash_feedback`, `dash_annotations`, `dash_evals`, `dash_query_patterns`, `dash_workflows_db`, `dash_training_runs`, `dash_relationships`, `dash_training_qa` |
| **Self-Evolution** | `dash_proactive_insights`, `dash_user_preferences`, `dash_query_plans`, `dash_evolved_instructions`, `dash_meta_learnings`, `dash_eval_history`, `dash_eval_runs` |
| **Persistence** | `dash_table_metadata`, `dash_business_rules_db`, `dash_rules_db`, `dash_personas`, `dash_documents`, `dash_drift_alerts` |
| **Connectors** | `dash_data_sources` (provider rows w/ provider_class, dialect, mode, agent_scope, config jsonb) |
| **Knowledge Graph** | `dash_knowledge_triples` (SPO + source_type + source_id + confidence) |
| **Brain** | `dash_company_brain` (3-scope: global / project / personal), `dash_brain_access_log` |
| **ML** | `dash_ml_models`, `dash_ml_jobs`, `dash_ml_experiments` |
| **Self-Learning v2** | `dash_self_learning_runs`, `dash_hypotheses`, `dash_dossiers`, `dash_curiosity_questions`, `dash_promotion_log` |

## Per-source isolation

```
project_slug "fund3"
  │
  ├── provider id=0  postgres_local      proj_fund3        scope=shared
  │     ├── engine_ro / engine_rw
  │     ├── schema_blob
  │     └── knowledge/fund3/source_0/
  │           ├── dimensions/{table}.json
  │           ├── doc_structure/{name}.json
  │           ├── doc_meta/{file}.json
  │           ├── docs_raw/
  │           └── grounded_facts.json
  │
  ├── provider id=14 postgres_remote     remote sales DB    scope=analyst_only
  │     ├── instructions_overlay        "use SET LOCAL transaction_read_only=on"
  │     └── knowledge/fund3/source_14/
  │
  ├── provider id=27 sharepoint         "Fund III Reports" scope=researcher_only
  │     ├── tools: search_27, fetch_27, list_folder_27
  │     └── knowledge/fund3/source_27/
  │
  └── provider id=33 fabric             warehouse           scope=project
        └── knowledge/fund3/source_33/
```

Each provider gets its own dialect, agent_scope, schema_blob, instructions overlay,
and training artifacts. Tools emitted per-source (e.g. `query_27`, `describe_27`,
`sample_27`, `search_27`, `fetch_27`).

## Multi-tenant boundary

```
Tenant A                   Tenant B
  │                          │
  ├── proj_alpha schema      ├── proj_gamma schema
  ├── proj_beta  schema      ├── proj_delta schema
  │   ├── memories           │   ├── memories
  │   ├── KG triples         │   ├── KG triples
  │   ├── brain (proj+pers)  │   ├── brain (proj+pers)
  │   └── providers + scope  │   └── providers + scope
  │                          │
  └── /branding/tenantA/     └── /branding/tenantB/
        logo, colors, copy         logo, colors, copy

   Shared across tenants (opt-in):
     - global Company Brain entries (project_slug=NULL)
     - central learning cycle pool
```

Per-project Postgres schema. Per-source memory/KG/brain. Central pool opt-in via
`run_promotion=True` on central cycle. White-label branding lives at
`/branding/<tenant>/` and is overlay-applied at frontend boot.

## Deployment topology

### Docker Compose (`compose.yaml`)

5 services — 4 Dash + 1 reverse proxy:

| Service | Image | Memory cap | Purpose |
|---------|-------|------------|---------|
| `dash-db` | `pgvector/pgvector:pg18-trixie` | 4G | Postgres 18 + pgvector |
| `dash-pgbouncer` | `edoburu/pgbouncer` | 512M | Txn pool, scram-sha-256 |
| `dash-api` | local build | 8G | FastAPI, 8 workers |
| `dash-ml` | local build | 1G | ML worker, polls jobs table |
| `caddy` | `caddy:2-alpine` | 512M | Reverse proxy + auto-SSL |

PgBouncer settings: `MAX_CLIENT_CONN=3000`, `DEFAULT_POOL_SIZE=80`,
`MAX_DB_CONNECTIONS=200`, `POOL_MODE=transaction`,
`IGNORE_STARTUP_PARAMETERS: extra_float_digits,options`,
`SERVER_RESET_QUERY: DISCARD ALL`.

### Kubernetes (`k8s/`)

24 raw manifests, ordered by numeric prefix:

```
00-namespace.yaml
01-configmap.yaml             02-secret.yaml
10-db-pvc · db-service · db-statefulset
20-pgbouncer-deploy · pgbouncer-svc
30-api-deploy · api-svc · api-hpa · knowledge-pvc
40-ml-worker-deploy
60-caddy-configmap · caddy-deploy · caddy-pvc · caddy-svc · ingress
70-decay-cronjob · learning-cronjob · learning-canary-cronjob
80-networkpolicy
90-rbac · serviceaccount
```

3 CronJobs:
- `learning-cronjob` daily — full LLM cycle
- `learning-canary-cronjob` Sunday — `dry_run=True`, $0 baseline
- `decay-cronjob` daily — forgetting module

### Helm (`helm/dash/`)

17 templates parametrized via `values.yaml` + `values-prod.yaml` + `values-dev.yaml`:

```
_helpers.tpl
namespace.yaml         configmap.yaml        secret.yaml
db-pvc · db-service · db-statefulset
pgbouncer.yaml
api.yaml (Deployment + Service + HPA)        knowledge-pvc.yaml
ml-worker.yaml
caddy.yaml             ingress.yaml
learning-cronjobs.yaml (3 CronJobs in one template)
networkpolicy.yaml
rbac.yaml              serviceaccount.yaml
```

Default replicas: api=3, mlWorker=1, caddy=2.
Autoscaling: min=3, max=10, target CPU=70%, target memory=75%.
Storage: db=20Gi RWO, knowledge=50Gi RWX (must be RWX-capable class), caddy=5Gi RWO.

## Security model

- **Auth**: scram-sha-256 throughout (Postgres `password_encryption=scram-sha-256`,
  PgBouncer `AUTH_TYPE=scram-sha-256`).
- **Pooling**: NullPool on every `create_engine()` — PgBouncer owns pooling.
- **Timeouts**: Postgres `statement_timeout=120s`, `idle_in_transaction_session_timeout=60s`,
  PgBouncer `QUERY_WAIT_TIMEOUT=30s`, `CLIENT_IDLE_TIMEOUT=600s`.
- **Read-only enforcement**: Analyst path sets `SET LOCAL transaction_read_only = on` inside
  the SQLAlchemy `begin` event. Cannot be bypassed by LLM-generated SQL.
- **LLM SQL sandbox**: regex blocks `DROP/ALTER/TRUNCATE`. `UPDATE/DELETE` allowed only on
  the target table; rolls back if >50% rows affected.
- **PII auto-detect + mask**: qualified-column detection at query time, masked in result,
  audit log row written.
- **RBAC**: `check_project_permission(slug, required_role)` on all 36+ endpoints.
- **Path traversal**: slug must match `^[a-z0-9_-]+$` before any disk path is built.
- **Secrets**: connector tokens base64-encoded in `dash_data_sources.config` jsonb;
  encryption-at-rest planned (see `SECURITY.md`).
- **Caddy**: HSTS, X-Frame-Options, nosniff, XSS, 250MB body, 300s timeout.
- **Non-root Docker user**, **AGNO_DEBUG=False** in production.

See `SECURITY.md` for full threat model.

## Scaling profile

| Component | Default replicas | Memory limit | CPU limit | Notes |
|-----------|-----------------|--------------|-----------|-------|
| api | 3 (HPA 3-10) | 4Gi | 2 | 8 uvicorn workers per pod |
| ml-worker | 1 | 1Gi | 1 | Single poller, SIGALRM 5min |
| caddy | 2 | 256Mi | 0.5 | Stateless, K8S only |
| pgbouncer | 1 (single point) | 512Mi | 0.5 | Txn mode, 3000 client conn |
| db | 1 (StatefulSet) | 4Gi | 2 | 300 max_connections |

Validated load: 200 concurrent users × 5 endpoints = 1000 simultaneous requests,
100% pass rate, 81 stable DB connections (PgBouncer-fronted).

## Related docs

- `AGENTS.md` — full agent inventory, 30 agents
- `PATTERNS.md` — kpt + Scout + Dash design patterns
- `CLAUDE.md` — recent changes, behavior log
- `SECURITY.md` — threat model, RBAC, sandbox
- `DEPLOYMENT.md` — Compose + K8S + Helm runbooks
- `OPERATIONS.md` — runbooks (legacy, folding into DEPLOYMENT.md)
- `UPGRADE.md` — migration playbook
- `CHANGELOG.md` — version history
