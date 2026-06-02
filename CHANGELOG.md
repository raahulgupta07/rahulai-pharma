# CHANGELOG

All notable changes to Dash. Format follows [Keep a Changelog](https://keepachangelog.com).
Versions are tagged `vX.Y.Z` from v1.0.0 onward; older entries remain month-tagged.

## [Unreleased]

### Added
- **Cockpit ⇄ Datasets merge (2026-05-18)** — folded Cockpit into Datasets as single landing page. Rail label "Cockpit", internal id `datasets`. URL `#cockpit` redirects to `#datasets`. New Cockpit block: trained banner → Pipeline status (last training, schedule, drift) → At a glance (today/perf/health) → Intelligence (knowledge/agents/cost) → existing Datasets content unchanged.
- **`GET /api/projects/{slug}/activity`** — returns recent training runs + drift alerts (used by Cockpit refresh)

### Fixed
- **Render-crash class — array assumed, string/object returned** (Issue #29):
  - `data.content.alternate_tables.join is not a function` → guarded with `Array.isArray()` on 5 codex fields (primary_keys, foreign_keys, usage_patterns, alternate_tables, relationships)
  - `(p.tables || []).includes is not a function` → same guard on 3 sites (queryPlans, insights, trainingRuns)
  - Downstream effect: URL hash desync (Svelte hydration aborts mid-render → `$effect` for hash write never runs). Auto-fixes once render stops throwing.
  - New defensive coding rule R26 in PATTERNS.md, troubleshooting entry in README + CLAUDE.md Issue #29

### Added (prior wave)
- **Dream Reflection subsystem v1** — three-tier session-replay self-improvement:
  - **Tier 1 (per-turn, rule-based, $0)**: poignancy capture into `dash_episode_buffer` (rolling LRU 1000/proj)
  - **Tier 2 (between-turn, LITE, ~$0.005)**: dream-lite cycle — persona update (Letta MemoryBlock analog) + anticipated-query precompute queue (Letta sleep-time compute)
  - **Tier 3 (nightly cron 02:30 UTC, DEEP, ~$0.13/proj)**: full reflection — anti-patterns, decision rules, bi-temporal fact reconciliation, skill library promotion, reflection tree (Generative Agents), wiki digest (Devin)
  - Adds Context Layers 14 (anti-patterns), 15 (proven skills), 16 (precompute hints) to Analyst instructions
  - Bi-temporal columns (`valid_at`/`invalid_at`/`expired_at`/`superseded_by`) added to `dash_company_brain` + `dash_knowledge_triples` (Graphiti pattern: never delete, only invalidate)
  - 4 SQL migrations (066–069) + 1 feature-flag migration (070, planned)
  - 30+ HTTP endpoints under `/api/projects/{slug}/dream/*`
  - 5 new minion kinds, all integrate w/ existing `dash_minions` queue: `reflect_sessions`, `dream_lite`, `poignancy_capture`, `precompute_queries`, `ab_revert_check`
  - Settings → SELF-LEARN → 🌙 DREAMING UI w/ 11 sub-views
  - K8s CronJob `dream-reflect-nightly` (02:30 UTC) + `dream-ab-revert-daily` (04:00 UTC) + `dream-precompute-hourly` (:15)
  - Slack digest hook via existing `SLACK_LEARNING_WEBHOOK` env
  - Research-adopted patterns: Letta sleep-time, Mem0 4-op schema, Graphiti bi-temporal, ExpeL vote-weighted insight pool, Voyager skill library, Generative Agents reflection tree, Devin wiki digest, HippoRAG (inspiration)
  - Deep-dive: `docs/DREAM_CYCLE.md`; recipes R21–R25 in `PATTERNS.md`

### Migration
- Apply migrations 066–069 in order via psql
- If `dash_company_brain` lives in `public` schema (older convention), migration 067 auto-detects via DO block and applies bi-temporal ALTERs there
- If `dash.dash_knowledge_triples` table missing, migration 069 bootstraps it (separate `public.dash_knowledge_triples` owned by `dash/tools/knowledge_graph.py` is untouched)
- New env: optional `DREAM_REFLECTION_DISABLED=1` to disable all dream minions + cron

### Planned
- Encryption-at-rest for OAuth tokens (AES-256-GCM, parity with Scout)
- Pre-commit gates (ruff, mypy, gitleaks, ESLint)
- Formal pen-test scope (see `SECURITY.md`)
- MCP provider (steal pattern from Scout)
- Snowflake / BigQuery / Databricks providers
- Cross-source federation join

---

## [v1.1.0] — 2026-05-06 — kpt Patterns + Hardening

### Added
- 12 kpt autoresearch patterns wired into `dash/learning/`:
  - time budget (`cost_guard.py`) — daily cost ceiling, default $1/proj, $5 central
  - parallel research (`researcher.py` `asyncio.gather`, ~4× speedup)
  - branch+prune (`curiosity.py` adversarial 3-variant fork)
  - diff-as-experiment lineage (`lineage.py` `parent_hypothesis_id`)
  - run-then-review digest (`digest.py` "today's discoveries")
  - dry-run canary (Sunday CronJob, no writes)
  - single growth metric (`agent_iq.py` composite `agent_iq`)
  - program.md goals (`goals.py` writes `learning_goals.md` per project)
  - forgetting curve (`forgetting.py` -0.02/day decay)
  - promotion gate (`promotion.py` PII-scrubbed + LLM-gated)
  - verifier timeout (`verifier.py` `statement_timeout 110s` clamp)
  - consolidator routing (`consolidator.py` Memory / KG / Brain / Rules)
- `learning_goals.md` per project (kpt program.md pattern)
- Per-question 120 s wall-clock cap
- Tree-of-experiments UI in Command Center (parent → children walk)
- Optional Slack webhook for daily digest (`SLACK_LEARNING_WEBHOOK`)
- IQ trend SVG sparkline (7d / 30d / 90d) in cockpit
- White-label branding system: `branding/<tenant>/` (`company.json`,
  `logo.svg`, `favicon.ico`, `theme.css`)
- `GET /api/branding` endpoint (loads tenant branding, falls back to `default/`)
- 3 K8S CronJobs in `helm/dash/templates/learning-cronjobs.yaml`:
  daily learning cycle, Sunday dry-run canary, daily forgetting decay
- Migration `006_brain_unique_index.sql` (Brain unique indexes prevent
  duplicate canonicals under concurrent promotion)

### Changed
- In-process scheduler now auto-disables on K8S (multi-pod race avoided);
  override via `LEARNING_SCHEDULER_FORCE_INPROCESS=1`
- `_bootstrap_unique_indexes()` reduced to no-op fallback (migration owns it)
- Curiosity gap detection sources expanded 3 → 10
- PII rank made conservative — collisions resolve to most-restrictive strategy

### Fixed
- SQL `statement_timeout` cooperative cancel (110 s on remote queries)
- PII column collision (qualified column extraction via `sqlglot`)
- Brain unique-index migration (was runtime conditional, now declarative)
- Multi-pod scheduler race (K8S CronJob replaces in-process daemon)
- Curiosity gap shallowness (3 sources → 10 sources + branch+prune)

### Deprecated
- `_bootstrap_unique_indexes()` (no-op fallback; migration 006 is canonical)
- `DEPLOYMENT_CFC.md` → renamed `DEPLOYMENT_K8S.md`
- `values-cfc.yaml` → renamed `values-prod.yaml`

### Migration
- Apply migrations 001–006 in order (idempotent, see `UPGRADE.md`)
- Rename Helm values files in any external scripts
- Set `BRANDING_DIR` if tenant branding needed
- Install K8S CronJobs (`k8s/70-*.yaml`) before upgrade or set
  `LEARNING_SCHEDULER_FORCE_INPROCESS=1`

---

## [v1.0.0] — 2026-05-05 — Self-Learning + Live Fabric

### Added
- 17 self-learning modules in `dash/learning/`: `curiosity`, `researcher`,
  `hypothesis`, `verifier`, `consolidator`, `forgetting`, `promotion`,
  `cycle`, `scheduler`, `agent_iq`, `goals`, `cost_guard`, `lineage`,
  `digest`, `external_data`, `web_search`, `base`
- 7 connector providers in unified abstraction (`dash/providers/`):
  `postgres_local`, `postgres_remote`, `mysql_remote`, `fabric`,
  `sharepoint`, `onedrive`, `gdrive` — all extend `base.BaseProvider`
  and register through `registry.py`
- Per-agent + per-source isolation: `analyst_only` / `researcher_only` /
  `shared` / `project` scope on each data source
- Smart column classifier (`column_classifier.py`): 5 detectors
  (stats, regex, name, LLM, embedding cosine), 69 PII regex patterns
- 7 PII masking strategies in `pii_mask.py` (block / redact / hash /
  hash_email / mask_email / mask_phone / generalize / truncate)
- 241 brain seed canonicals in `column_priors.py`
- XMLA Power BI semantic puller (`xmla_pull.py`)
- 14-step provider trainer pipeline (`trainer.py` + `training_steps.py`
  + `training_steps_v2.py`)
- Per-project + central hybrid intelligence pool with promotion gate
- `agent_iq` sparkline UI in Command Center
- Migrations 001–005: `provider_layer`, `self_learning`, `cost_ceiling`,
  `hypothesis_lineage`, `digests`

### Changed
- Daily K8S CronJob replaces in-process scheduler for multi-pod safety
- `dash_data_sources` extended with `config`, `scope` columns
- Promotion always passes through PII scrub + LLM gate

### Fixed
- Connection exhaustion under per-source isolation (NullPool everywhere)
- Race when two pods promoted same canonical simultaneously (queue lock)

### Migration
- Apply migrations 001–005
- Set `OPENROUTER_API_KEY`, optional `TAVILY_API_KEY`, `FRED_API_KEY`
- Wire Fabric Service Principal env vars if using `fabric` provider

---

## [2026-04] — ML worker + connectors hardening

### Added
- Architecture page in Command Center (interactive ECharts flow diagram, 35 nodes, live DB metrics)
- ML preprocessing pipeline (`_preprocess_df()`): SimpleImputer, temporal features, label encoding
- GridSearchCV hyperparameter tuning for `feature_importance` and `classify` (18 param combos)
- Better ML eval metrics: F1, Precision, Recall, Confusion Matrix, RMSE, MAE, Calinski-Harabasz
- Historical data in forecast (last 12 periods)
- Merged `predict` + `llm_predict` into a single tool with auto-fallback
- ML/LLM badges in UI (green ML, purple LLM)
- Data Scientist context instructions (table shapes, past experiments, active models)
- Analyst context budget increase (30K → 50K chars)
- ML keyword rejection in Analyst (stops on ML keywords, routes to Data Scientist)
- Multi-Agent Questions in Leader (data + docs together)
- Data Scientist fallback chain (business-language explanations on ML failure)
- Leader stuck-agent detection (zero rows → introspect, ML question → re-route)
- `discover_tables` tool (renamed from `introspect_schema` for Data Scientist)
- Currency / comma / `%` stripping in `_clean_dataframe()`
- Multi-level header flatten in `_rules_analyze_sheet()`
- Hidden row / column filter via openpyxl scan
- Multi-sheet similarity detection (Jaccard >0.8 auto-concat)
- Cell comments extraction
- SheetCompressor (~50% token reduction)
- Calamine fast path for clean sheets (5-10× faster)
- Ghost row detection (`max_row > 10000`)
- Row cap on `read_excel` (`nrows=min(actual_rows, 100000)`)
- AI structure validator (auto wide → long unpivot)
- Source tracking columns (`_source_file`, `_source_sheet`)
- Single-sheet Excel pipeline
- Parquet, ODS, XML, HTML, ZIP, EML support
- SQL profiling pipeline (PG-side, $0 RAM)
- Dimension catalog (SELECT DISTINCT for unique < 500)
- Hierarchy detection
- Smart sampling (3 start + 3 mid + 3 end + outliers + nulls)
- Dimension injection into Analyst semantic model
- Document structure extraction (TOC / headings)
- Section-aware chunking
- Hierarchical summarisation
- Page citations
- Table download API (CSV / Excel)

### Changed
- `predict` now uses GPT-5.4-mini (`DEEP_MODEL`) with high thinking via `ml_prediction` task config
- Vision calls use Gemini 3 Flash (was Flash Lite)
- `training_llm_call()` sends `reasoning_effort` for GPT models too

### Fixed
- ML worker port (6432 → 5432) + `pgbouncer` dependency
- LLM SQL sandbox blocks `DROP/ALTER/TRUNCATE`, target-table-only `UPDATE/DELETE`, rollback >50% rows
- DB engine leaks (`ml_models.py` `dispose()` in `finally`)
- ML worker row limit (`LIMIT 100,000`)
- Embedding cascade graceful failure (returns `None` instead of broken embedder)
- Batch predict size limit (10,000 rows, 413 error)
- ML retrain health monitoring (`last_run`, `last_error` in `/health`)
- Contextual enrichment cap (200 chunks max)
- ML worker job timeout (5-min SIGALRM)
- Personal brain auth (regular users can save personal entries)
- Flat chart caption (when all values identical)

### Migration
- Add `dash-ml` to `compose.yaml` if missing
- Verify all `create_engine()` calls use `poolclass=NullPool`

---

## [2026-03] — Connectors + Brain v2

### Added
- SharePoint connector (`app/sharepoint.py`) — Entra ID OAuth, Graph API, SSE sync
- Google Drive connector (`app/gdrive.py`) — Google OAuth, Drive API v3, SSE sync
- Database connectors (`app/connectors.py`) — PostgreSQL, MySQL, MS Fabric
- Project Settings SOURCES tab (5 connector cards)
- Command Center INTEGRATIONS tab (admin connector config)
- IMPORT FROM EXTERNAL SOURCE button
- Visualization Agent (`dash/tools/visualizer.py`)
- 11 Analysis Tools wired to TYPE dropdown
- Smart Multi-Agent Routing (Leader auto-detects data + context)
- Continuous KG Learning (post-chat triple extraction)
- Auto-Memory Promotion
- Rich User Preference Tracking
- Episodic Memory
- Multi-Signal Retrieval (Researcher)
- KPI metric cards, confidence bar, impact summary, related questions, trend arrows
- Inline charts in ANALYSIS tab (up to 3 auto-detected ECharts)
- Multi-chart CHART tab with sub-tabs
- Chart captions ($0, no LLM)
- Smart Router Agent (`dash/agents/router.py`) — 2-tier routing
- Cross-Source Knowledge Graph (`dash/tools/knowledge_graph.py`)
- Company Brain (`app/brain.py`, `/ui/brain`) — 7 tabs, 3-layer
- Project-Scoped Brain (`project_slug`, `user_id` cols on `dash_company_brain`)
- Context Loader Tool (`dash/tools/context_loader.py`)
- Slide Agent Design System Upgrade (8 themes, Visual QA)
- Semantic Search Layer (`dash/tools/semantic_search.py`) — Cohere reranking
- Contextual Chunk Enrichment (49% retrieval improvement)
- 3-Model Architecture (`CHAT_MODEL` / `DEEP_MODEL` / `LITE_MODEL`)
- Gemini Embedding 2 (MTEB ~68, +35% similarity vs OpenAI small)
- Excel Self-Correction Pipeline (5 layers)
- SHAP Explanations (`shap.TreeExplainer`)
- Anomaly-to-SQL Bridge (`CREATE VIEW {table}_anomalies`)
- Scheduled ML Retraining (24 h daemon)
- Batch Prediction API (`POST /api/ml-predict`)
- Model Comparison UI
- ML Worker Container (`ml_worker/main.py`)

### Changed
- Embedding cascade now Gemini 2 → OpenAI large → small → Cohere v4
- Suggestions endpoint uses LITE_MODEL for speed

### Migration
- Add `CHAT_MODEL` / `DEEP_MODEL` / `LITE_MODEL` to `.env` (defaults work)
- Existing tables auto-migrated for `dash_company_brain` columns

---

## [2026-02] — Self-evolution + 13 context layers

### Added
- Codex-Enriched Knowledge Pipeline (purpose, grain, PKs, FKs, usage, freshness)
- Self-Correction Loop (Analyst, 3 attempts)
- Full Evaluation Pipeline (generate → execute → compare → grade)
- Interactive Knowledge Graph (Settings → LINEAGE)
- Rich DATASETS Tab
- Global Dashboard Page
- PIN to Dashboard Modal
- Dashboard Detail View
- Delete Confirmation Modals
- STOP button
- Agent-created dashboards (`create_dashboard` tool)
- Dashboard Side Panel (BagOfWords-style)
- Proactive Insights (`dash/tools/proactive_insights.py`)
- User Preference Learning
- Query Plan Memory (`dash/tools/query_plan_extractor.py`)
- Knowledge Consolidation (`POST /{slug}/consolidate-knowledge`)
- Auto-Evolving Instructions (`dash/tools/auto_evolve.py`, every 20 chats)
- Conversation Pattern Mining (`POST /{slug}/mine-patterns`)
- Meta-Learning (`dash/tools/meta_learning.py`)
- Cross-Project Learning Transfer (`GET /{slug}/transfer-candidates`)
- Self-Evaluation Loop (`POST /{slug}/self-evaluate`)
- LangExtract grounded fact extraction
- PyMuPDF4LLM PDF → Markdown extraction
- Researcher Agent
- Document-to-Workflow

---

## [2026-01] — Foundation + 30 agents

### Added
- 30-agent system (4 core + 10 specialist + 7 background + 5 upload + 1 visualizer + 1 router)
- 13 context layers (matches OpenAI architecture)
- Auto-training pipeline (10+ steps for data, 18 steps for doc-only)
- Self-Train pipeline (4.5 phases)
- Knowledge Sources Control Panel (8 toggles + 3 presets)
- Agent Settings (15 tabs)
- 24 file formats (CSV, Excel, JSON, SQL, PPTX, DOCX, PDF, JPG, PNG, MD, TXT, Parquet, ODS, XML, HTML, ZIP, EML, etc.)
- Excel AI multi-sheet (GPT-5.4-mini analyses structure)
- Excel unpivot (wide → long)
- Multi-table per sheet detection
- Forward-fill merged cells
- Scanned PDF OCR (Tesseract + Vision LLM fallback)
- DOCX image extraction
- JPG / PNG direct upload (Tesseract + Vision)
- Auto-merge same-structure tables (Engineer)
- Per-file upload progress bar
- Source tracking
- Diagram auto-detection
- PPTX slide rendering for vision
- Vision pipeline for images
- Smart suggested questions (LLM-generated eval Q&A)
- Reactive session counter
- Workflow source badges
- Redesigned DATASETS tab
- Raw binary storage (`docs_raw/`)
- Dashboard Generator (D button, 2-step LLM)
- Training per-table progress
- Dynamic Agents tab (API-driven)
- Smart file routing (`/api/upload` vs `/api/upload-doc`)
- Leader doc-only routing
- Role-Based Permissions (viewer / editor / admin)
- User Sharing Modal
- Dashboard Save / Discard
- Command Center 9 tabs
- Project Delete Cleanup
- Production Security Hardening (scram-sha-256, AGNO_DEBUG=False, Caddy headers, 512M cap)

### Migration
- Replace md5 → scram-sha-256 password encryption
- Update PgBouncer `AUTH_TYPE` to `scram-sha-256`

---

## Pre-2026 — Initial release

Initial Dash platform: FastAPI + SvelteKit + Agno + PostgreSQL + PgVector. Single-tenant data notebook with chat, training, and evals.
