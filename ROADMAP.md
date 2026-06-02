# Dash Skills Roadmap

> Living document. Adopts SKILL.md open standard. Skills come from 3 sources:
> **Custom** (Dash-built) · **Native** (Anthropic-cloned, Apache 2.0 only) · **Community** (MiniMax / agentskills.io / MIT-Apache).

---

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Shipped — working in production |
| 🚧 | In progress — current sprint |
| 📋 | Planned — confirmed scope, not started |
| 💡 | Backlog — idea, needs validation |
| ❌ | Rejected — license, scope, or strategic mismatch |

---

## Phase 4 prerequisite — Skills engine (must ship first)

| Component | Status | Notes |
|---|---|---|
| `dash/skills_engine/loader.py` | 📋 | scans `dash/skills/*/SKILL.md`, parses YAML frontmatter |
| `dash/skills_engine/registry.py` | 📋 | DB CRUD on `dash_skills_registry` |
| `dash/skills_engine/activation.py` | 📋 | tool-call detector + sentinel regex fallback |
| `dash/skills_engine/executor.py` | 📋 | runs `scripts/*.py` and `*.ts` in ML worker sandbox |
| Migration `041_skills_registry.sql` | 📋 | 3 tables: registry / project_skills / invocations |
| `<available_skills>` injector in `dash/instructions.py` | 📋 | prepends to Leader/Analyst/Engineer/Researcher |
| `/api/skills/*` endpoints (5) | 📋 | list / detail / install / uninstall / invoke |
| `/ui/cmd-center/skills` marketplace page | 📋 | install/uninstall + audit log + filters |
| Vendor `agentskills-sdk` (Python) | 💡 | optional — saves loader implementation time |

---

## Custom skills (Dash-built · ship in repo Day-1)

### Agents (27 — reformat existing)

| Skill ID | Replaces | Status |
|---|---|---|
| `agent-leader` | `dash/agents/leader.py` instructions | 📋 |
| `agent-analyst` | `dash/agents/analyst.py` instructions | 📋 |
| `agent-engineer` | `dash/agents/engineer.py` instructions | 📋 |
| `agent-researcher` | `dash/agents/researcher.py` instructions | 📋 |
| `agent-customer-strategist` | `dash/agents/customer_strategist.py` instructions | 📋 |
| `agent-data-scientist` | `dash/agents/data_scientist.py` instructions | 📋 |
| `agent-router` | `dash/agents/router.py` instructions | 📋 |
| `agent-comparator` | specialist agent — period MoM/YoY | 📋 |
| `agent-diagnostician` | specialist — root cause | 📋 |
| `agent-narrator` | specialist — exec summary | 📋 |
| `agent-validator` | specialist — data quality | 📋 |
| `agent-planner` | specialist — what-if scenarios | 📋 |
| `agent-trend-analyst` | specialist — time series | 📋 |
| `agent-pareto-analyst` | specialist — 80/20 | 📋 |
| `agent-anomaly-detector` | specialist — outlier z-score | 📋 |
| `agent-benchmarker` | specialist — entity vs avg | 📋 |
| `agent-prescriptor` | specialist — actionable recs | 📋 |
| `agent-judge` | background — quality scoring | 📋 |
| `agent-rule-suggester` | background — rule extraction | 📋 |
| `agent-proactive-insights` | background — anomaly post-chat | 📋 |
| `agent-query-plan-extractor` | background — table/join mining | 📋 |
| `agent-meta-learner` | background — self-correction tracking | 📋 |
| `agent-auto-evolver` | background — evolved instructions | 📋 |
| `agent-chat-triple-extractor` | background — KG SPO triples | 📋 |
| `agent-conductor` | upload — orchestrator | 📋 |
| `agent-parser` | upload — data extraction | 📋 |
| `agent-scanner` | upload — document intelligence | 📋 |
| `agent-vision` | upload — image OCR + Vision LLM | 📋 |
| `agent-inspector` | upload — quality validation | 📋 |
| `agent-visualizer` | auto-detect chart type + ECharts | 📋 |

### AutoML agent team (8 — Phase 2 just shipped)

| Skill ID | Replaces | Status |
|---|---|---|
| `automl-agent-lead` | `dash/automl/agents/lead.py` | ✅ |
| `automl-agent-data-engineer` | `dash/automl/agents/data_engineer.py` | ✅ |
| `automl-agent-eda-analyst` | `dash/automl/agents/eda.py` | ✅ |
| `automl-agent-feature-engineer` | `dash/automl/agents/feature_engineer.py` | ✅ |
| `automl-agent-ml-engineer` | `dash/automl/agents/ml_engineer.py` | ✅ |
| `automl-agent-explainability` | `dash/automl/agents/explain.py` | ✅ |
| `automl-agent-domain-expert` | `dash/automl/agents/domain_expert.py` | ✅ |
| `automl-agent-report-writer` | `dash/automl/agents/report_writer.py` | ✅ |

### Domain experts (7 verticals)

| Skill ID | Replaces | Status |
|---|---|---|
| `expert-hr` | `dash/automl/domain_experts/people.py` | ✅ |
| `expert-revenue` | `dash/automl/domain_experts/revenue.py` | ✅ |
| `expert-supply` | `dash/automl/domain_experts/supply.py` | ✅ |
| `expert-finance` | `dash/automl/domain_experts/finance.py` | ✅ |
| `expert-healthcare` | `dash/automl/domain_experts/healthcare.py` | ✅ |
| `expert-hospitality` | `dash/automl/domain_experts/hospitality.py` | ✅ |
| `expert-starter` | `dash/automl/domain_experts/starter.py` | ✅ |

### AutoML templates (12)

| Skill ID | Status |
|---|---|
| `automl-template-hr-attrition` | ✅ ready |
| `automl-template-customer-churn` | 📋 preview |
| `automl-template-sales-forecast` | 📋 preview |
| `automl-template-stockout-risk` | 📋 preview |
| `automl-template-defect-predictor` | 📋 preview |
| `automl-template-loan-default` | 📋 preview |
| `automl-template-budget-variance` | 📋 preview |
| `automl-template-readmission-30d` | 📋 preview |
| `automl-template-refill-adherence` | 📋 preview |
| `automl-template-adr-forecast` | 📋 preview |
| `automl-template-headcount-forecast` | 📋 preview |
| `automl-template-custom` | 💡 LLM-driven, no preset |

### Output formats (own implementations)

| Skill ID | Backend | Status |
|---|---|---|
| `slides-pptx` | PptxGenJS (browser) | 📋 |
| `slides-pptx-server` | python-pptx (worker batch) | ✅ Phase 2 base |
| `slides-pdf` | reportlab → HTML fallback | ✅ Phase 2 base |
| `slides-html` | revealjs / marp | 💡 |
| `slides-md` | markdown export | 💡 |
| `slides-keynote` | XML | 💡 |
| `report-pdf` | reportlab — exec summary 1-pager | ✅ |
| `excel-export` | xlsxwriter | ✅ existing |
| `dashboard-spec` | JSON for `dash_dashboards_v2` | ✅ existing |

### Brain queries

| Skill ID | Status |
|---|---|
| `brain-glossary-lookup` | 📋 |
| `brain-formula-resolve` | 📋 |
| `brain-alias-expand` | 📋 |
| `brain-pattern-search` | 📋 |
| `brain-org-traverse` | 📋 |
| `brain-fact-promote` | 📋 |

### Knowledge graph

| Skill ID | Status |
|---|---|
| `kg-search` | 📋 |
| `kg-entity-detail` | 📋 |
| `kg-triple-extract` | 📋 (chat triple extractor) |
| `kg-community-detect` | 📋 |

### Connectors (existing)

| Skill ID | Status |
|---|---|
| `conn-postgres` | ✅ existing |
| `conn-mysql` | ✅ existing |
| `conn-fabric` | ✅ existing |
| `conn-sharepoint` | ✅ existing |
| `conn-gdrive` | ✅ existing |
| `conn-onedrive` | ✅ existing |

### ML engines

| Skill ID | Status |
|---|---|
| `automl-flaml` | ✅ Phase 1 default |

### Chart providers

| Skill ID | Status |
|---|---|
| `chart-echarts` | ✅ existing visualizer |

### Brain seed packs (10 verticals)

| Skill ID | Status |
|---|---|
| `brain-seed-retail` | ✅ |
| `brain-seed-pharmacy` | ✅ |
| `brain-seed-distribution` | ✅ |
| `brain-seed-finance` | ✅ |
| `brain-seed-hr` | ✅ |
| `brain-seed-saas` | ✅ |
| `brain-seed-supply-chain` | ✅ |
| `brain-seed-healthcare` | ✅ |
| `brain-seed-banking` | ✅ |
| `brain-seed-hotel` | ✅ |

### Demo seed packs (10 verticals)

| Skill ID | Status |
|---|---|
| `demo-retail` | ✅ |
| `demo-pharmacy` | ✅ |
| `demo-distribution` | ✅ |
| `demo-finance` | ✅ |
| `demo-hr` | ✅ |
| `demo-saas` | ✅ |
| `demo-supply-chain` | ✅ |
| `demo-healthcare` | ✅ |
| `demo-banking` | ✅ |
| `demo-hotel` | ✅ |

### Visibility / RLS

| Skill ID | Status |
|---|---|
| `visibility-policy-engine` | ✅ existing |
| `visibility-template-pharmacy` | ✅ |
| `visibility-template-retail` | ✅ |
| `visibility-template-hotel` | ✅ |
| `visibility-template-bank` | ✅ |
| `visibility-template-generic` | ✅ |

### Slide Factory (Phase 3 plan)

| Skill ID | Status |
|---|---|
| `slide-factory-outline` | 💡 — outline agent |
| `slide-factory-section-drafter` | 💡 — parallel section agent |
| `slide-factory-narrative-weaver` | 💡 |
| `slide-factory-visual-designer` | 💡 |
| `slide-factory-vision-qa` | 💡 — heuristic-gated vision check |
| `slide-factory-render` | 💡 — dispatcher (PptxGenJS / python-pptx) |
| `slide-factory-refine-slide` | 💡 — per-slide regen |
| `slide-factory-chat-analyzer` | 💡 — chat → outline bridge |

### Slide themes (8)

| Skill ID | Status |
|---|---|
| `theme-midnight-executive` | ✅ existing |
| `theme-forest-moss` | ✅ |
| `theme-coral-energy` | ✅ |
| `theme-ocean-gradient` | ✅ |
| `theme-charcoal-minimal` | ✅ |
| `theme-teal-trust` | ✅ |
| `theme-berry-cream` | ✅ |
| `theme-cherry-bold` | ✅ |

---

## Native skills (clone from anthropics/skills · Apache 2.0 only)

| Anthropic skill | License | Action | Status |
|---|---|---|---|
| `code-reviewer` | Apache 2.0 | Clone as `dash-code-reviewer` | 💡 low priority for data agent |
| `web-search` | Apache 2.0 | Clone — replaces `dash/learning/web_search.py` exec layer | 📋 |
| `markdown-formatter` | Apache 2.0 | Clone | 📋 |
| `bash-runner` | Apache 2.0 | Clone — feeds into ML worker sandbox | 💡 |
| `artifacts` | Apache 2.0 | Clone — saves chat artifacts | 💡 |
| `pdf` | Source-available | ❌ re-implement (use our own `slides-pdf`) | n/a |
| `docx` | Source-available | ❌ re-implement | n/a |
| `pptx` | Source-available | ❌ re-implement (`slides-pptx` PptxGenJS) | n/a |
| `xlsx` | Source-available | ❌ re-implement (use our `excel-export` xlsxwriter) | n/a |

---

## Community skills (vendor or marketplace install)

### MiniMax-AI/skills (MIT)

| Skill | Useful? | Status |
|---|---|---|
| `pptx-generator` | ✓ alt to Anthropic source-available | 📋 vendor as `pptx-minimax` |
| `minimax-pdf` | ✓ alt | 📋 vendor as `pdf-minimax` |
| `minimax-xlsx` | ✓ alt | 📋 vendor as `xlsx-minimax` |
| `minimax-docx` | ✓ alt | 📋 vendor as `docx-minimax` |
| `vision-analysis` | ✓ if MiniMax API key present | 💡 conditional |
| `frontend-dev` | ✗ coding agent, not data | ❌ |
| `fullstack-dev` | ✗ | ❌ |
| `android-native-dev` | ✗ | ❌ |
| `ios-application-dev` | ✗ | ❌ |
| `flutter-dev` | ✗ | ❌ |
| `react-native-dev` | ✗ | ❌ |
| `shader-dev` | ✗ | ❌ |
| `gif-sticker-maker` | ✗ toy | ❌ |
| `minimax-multimodal-toolkit` | ✗ requires MiniMax API | ❌ |
| `minimax-music-gen` | ✗ | ❌ |
| `buddy-sings` | ✗ | ❌ |
| `minimax-music-playlist` | ✗ | ❌ |

### agentskills.io marketplace (mixed MIT/Apache)

| Skill | Use case | Status |
|---|---|---|
| `notify-slack` | Workflow alert relay | 📋 |
| `notify-telegram` | Workflow alert relay | 💡 |
| `notify-pagerduty` | Critical incident escalation | 💡 |
| `notify-email` | Email send (replace stub) | 📋 |
| `notify-webhook` | Generic webhook | 💡 |
| `automl-optuna` | Alt to FLAML | 💡 |
| `automl-tpot` | Genetic search | 💡 |
| `automl-prophet` | Time-series forecasting | 📋 — useful for Sales/ADR forecasts |
| `connector-snowflake` | Cloud DW | 💡 |
| `connector-bigquery` | Cloud DW | 💡 |
| `connector-databricks` | Cloud DW | 💡 |
| `connector-redshift` | Cloud DW | 💡 |
| `chart-d3` | D3.js custom viz | 💡 |
| `chart-plotly` | Plotly | 💡 |
| `chart-vega` | Vega/Vega-Lite | 💡 |
| `pii-scrubber` | Replace existing PII regex with community-maintained | 💡 |
| `vision-fix` | Auto-fix overflow/contrast | 📋 — Slide Factory dependency |
| `deck-themes` | Extra slide themes | 💡 |
| `code-interpreter` | Sandbox Python exec | 💡 |
| `youtube-transcript` | Pull transcripts as data | 💡 |
| `web-scraper` | URL → structured data | 💡 |

---

## Phased rollout

### Phase 4A — Skills engine foundation (~12hr)
- [ ] Loader + registry + activation router + executor
- [ ] Migration 041
- [ ] `<available_skills>` injector
- [ ] `/api/skills/*` (5 endpoints)
- [ ] `/ui/cmd-center/skills` page
- [ ] Vendor 4 MiniMax office format skills
- [ ] Reformat 3-5 highest-value existing entities as proof (agent-leader, agent-analyst, automl-flaml, slides-pptx, brain-seed-retail)

### Phase 4B — Migration sweep (~8hr · parallelizable)
- [ ] Migrate 27 agents → `dash/skills/agents/agent-*/SKILL.md`
- [ ] Migrate 7 domain experts → `dash/skills/experts/expert-*/SKILL.md`
- [ ] Migrate 10 brain seeds → `dash/skills/brain/brain-seed-*/SKILL.md`
- [ ] Migrate 10 demo seeds → `dash/skills/demos/demo-*/SKILL.md`
- [ ] Migrate 8 themes → `dash/skills/themes/theme-*/SKILL.md`
- [ ] Migrate 12 AutoML templates → `dash/skills/automl-templates/`
- [ ] Compat layer keeps existing imports working

### Phase 4C — Slide Factory + multi-format (~10hr)
- [ ] PptxGenJS browser renderer (`slides-pptx`)
- [ ] DeckSpec JSON schema in `dash/skills/_shared/`
- [ ] Format dispatcher endpoint
- [ ] Share page output picker UI

### Phase 4D — Marketplace polish (~6hr)
- [ ] Install-from-URL flow
- [ ] Skill validator (frontmatter + license check)
- [ ] Per-project enable/disable
- [ ] Audit log of invocations
- [ ] Cost tracker per skill

### Phase 4E — Community ingestion (ongoing)
- [ ] Vendor `notify-slack` + `notify-email` (workflow relays)
- [ ] Vendor `automl-prophet` (forecasting)
- [ ] Sync with agentskills.io registry — daily cron
- [ ] Allowlist of trusted publishers

### Phase 4F — Bonus distribution (later)
- [ ] Expose Dash skills as MCP server (`/api/mcp/skills`)
- [ ] Submit `dash-customer-360` etc to agentskills.io marketplace
- [ ] PR to `MiniMax-AI/skills` if any of our skills are reusable
- [ ] Create `dash-skills` repo for public release of our 50+ custom skills

### Phase 4G — DeepAnalyze (framework only · use our LLMs)

**Strategic decision:** copy DeepAnalyze methodology, run on existing OpenRouter LLMs. Their 8B fine-tune is specialized but weaker overall than Gemini 3 Flash / GPT 5.4 mini. Methodology + bigger LLM = best output quality at zero infra cost.

#### 4G.1 — Shared report-structure template (~7hr · 1 agent)

**Single template injected across 8 surfaces.** ~150 tokens per prompt. Same structure everywhere: Quantitative Findings · Qualitative Insights · Methodology · Recommendations · Caveats.

- [ ] Create `dash/_shared/deepanalyze_template.py` (template constants + few-shot examples)
- [ ] Add `build_deepanalyze_layer()` helper in `dash/instructions.py`
- [ ] Inject into 8 surfaces (1 line each):
  - `dash/agents/analyst.py` — project chat ANALYSIS tab
  - `dash/agents/router.py` — Dash Agent cross-project chat
  - `dash/automl/domain_experts/*.py` (7 experts)
  - `dash/automl/stages/narrate.py` — experiment closing summary
  - `dash/automl/stages/report.py` — PDF/PPTX exec content
  - `dash/slide_factory/stages/weave.py` — deck narrative (when shipped)
  - `dash/tools/proactive_insights.py` — post-chat insights
  - `app/upload.py` step 6 — research-grade table descriptions
- [ ] Length-aware skip heuristic — short queries ("what's MRR?") stay freeform
- [ ] Frontend `<details>` collapsible per section in chat ANALYSIS tab
- [ ] End-to-end test: 5 chat queries + 5 AutoML runs

**Effect:** every analysis output across Dash gets predictable 6-section structure. Same template, same shape — easier to compare runs, easier to template, easier to share with management.

#### 4G.2 — Eval benchmark from DataScience-Instruct-500K (~4hr)

- [ ] One-time download of dataset (~500MB)
- [ ] Sample 50 representative prompts (10 per task type: SQL · EDA · forecast · classify · report)
- [ ] INSERT into `dash_evals` with `source='deepanalyze_benchmark'`
- [ ] Existing eval pipeline runs them weekly
- [ ] Cron alert if quality regression > 10%

#### 4G.3 — Future-proof skill wrapper (~3hr)

- [ ] `dash/skills/automl-deepanalyze/SKILL.md` (status: optional)
- [ ] `requires_env: DEEPANALYZE_BASE` in frontmatter
- [ ] Commented `DEEPANALYZE_BASE` env in `dash/settings.py`
- [ ] Auto-activates when env set (GPU users opt-in later)

#### 4G.4 — Curriculum training reference (1hr · documentation only)

- [ ] Save `docs/training_recipe.md` — DeepAnalyze's SFT → multi-ability cold-start → RL pattern
- [ ] Reference for future custom Dash model fine-tune (not now)

#### Skipped — no GPU, no scale

- ❌ Self-host 8B model via vLLM (no GPU)
- ❌ Run their 8B model directly (our LLMs stronger on general reasoning)
- ❌ Distillation at our scale (cost prohibitive)
- ❌ Fine-tune custom Dash model (no infra)

#### Phase 4G total

~15hr · zero infra · zero recurring cost · 8 surfaces upgraded with single template.

#### Where it shows up

| Surface | Today | After 4G |
|---|---|---|
| Project chat ANALYSIS tab | freeform paragraph | 6-section structured report |
| Dash Agent cross-project chat | mixed quality | predictable structure |
| AutoML domain expert output | freeform | research-grade per vertical |
| AutoML result narrator | mixed | structured exec summary |
| AutoML PDF/PPTX content | template-driven | structure-driven |
| Slide Factory narrative | template-driven | structure-driven |
| Proactive insights cards | short blurb | structured insight |
| Training table descriptions | generic | research-grade per table |

#### Cost impact

+$0.0005/query (extra ~150 tokens). Negligible vs quality gain.

---

## Phase 5 — Multi-agent architecture upgrades (research-backed)

Inspired by deep research into MetaGPT DataInterpreter, RD-Agent, Wren AI, TaskWeaver, business-science/ai-data-science-team. Backed by 2025 benchmark ([arxiv 2604.02460](https://arxiv.org/html/2604.02460v1)) showing single-agent + skills matches multi-agent under equal token budgets.

### Phase 5A — Plan-tree pattern (vendor from MetaGPT DataInterpreter)
- [ ] Borrow `examples/di/` planning loop — `goal → tasks → actions` decomposition
- [ ] Apply to AutoML pipeline (already has 8 stages, formalize as plan-tree nodes)
- [ ] Apply to Slide Factory (outline → drafts → weave → design → QA → render)
- [ ] Each node: explicit inputs/outputs/verification

### Phase 5B — Supervisor + skill specialists pattern
- [ ] LangGraph supervisor library
- [ ] Convert 12 chatty agents → callable tools (keep 6 high-judgment as agents)
- [ ] Single Supervisor LLM routes; specialists invoked as tools
- [ ] Cuts token spend ~40% on routine queries

### Phase 5C — Code-first state passing (TaskWeaver pattern)
- [ ] Persistent Python kernel in ML worker
- [ ] Agents exchange variable handles, not JSON dumps
- [ ] DataFrames + fitted models + plots stay live across agent turns
- [ ] Eliminates #1 token sink: re-serializing pandas every hop

### Phase 5D — Anthropic-style prompt caching
- [ ] `cache_control` on instruction blocks in `dash/settings.py`
- [ ] Cuts input cost ~90%, latency ~75% on repeat queries
- [ ] Cache warm-up on lifespan startup for hot skills

### Phase 5E — Single-vs-team A/B test
- [ ] Validate research finding on our workload
- [ ] Measure F1 / cost / latency per agent count

---

## Phase 6 — Semantic layer (Wren AI integration)

**Strategic decision:** vendor Wren AI as MDL engine instead of building our own. Wren is Apache 2.0, ships 20+ connectors + Apache DataFusion (Rust) + WASM. Saves ~25hr.

### Gap analysis vs Wren AI

| Capability | Dash today | Wren AI | Gap |
|---|---|---|---|
| Semantic layer | Brain + Ontology + KG (scattered) | MDL (single spec) | ⚠ unify |
| MDL versioning | brain_versions table | git-like history | partial |
| Connectors | 6 (PG/MySQL/Fabric/SP/GDrive/OneDrive) | **20+** | major gap |
| SQL engine | direct PG via SQLAlchemy | Rust + DataFusion | speed gap |
| Federated queries | per-schema only | cross-source via DataFusion | major gap |
| WASM browser | none | wren-core-wasm npm | future |
| External agents | Bearer keys | MCP-native | partial (Phase 4F) |

### What Dash KEEPS (don't lose)

✓ Multi-tenant project schemas · ✓ 27-agent team · ✓ AutoML hybrid · ✓ Slide Factory · ✓ Self-learning · ✓ Visibility/RLS · ✓ 7 domain experts · ✓ 10 vertical demos

### Phase 6A — Vendor Wren engine (~6hr)
- [ ] `pip install wren-engine` (Apache 2.0)
- [ ] `dash/mdl/spec.py` — Pydantic schema on top of MDL
- [ ] `dash/mdl/compiler.py` — calls wren-engine for SQL plans
- [ ] `dash/mdl/adapters/from_brain.py` — Brain entries → MDL
- [ ] `dash/mdl/adapters/from_ontology.py` — Ontology types → MDL
- [ ] `dash/mdl/adapters/from_kg.py` — KG triples → MDL relationships

### Phase 6B — `/v1/mdl` external API (~4hr)
- [ ] Auto-generate MDL JSON from existing Brain + Ontology + KG
- [ ] Wren-compatible spec
- [ ] Bearer-key + scope (existing `dash_ontology_api_keys`)
- [ ] Frontend: `/ui/ontology` adds MDL tab showing the unified model

### Phase 6C — Connector skills (~15hr · 5 connectors × 3hr)
- [ ] `conn-snowflake` (vendor from Wren or build fresh, Apache 2.0)
- [ ] `conn-bigquery`
- [ ] `conn-databricks`
- [ ] `conn-clickhouse`
- [ ] `conn-redshift`

(Future: trino, spark, oracle, sql-server, duckdb, doris, s3, gcs, minio)

### Phase 6D — Federated queries via DuckDB (~10hr)
- [ ] DuckDB embedded in ML worker
- [ ] Foreign data wrappers for PG / Snowflake / BigQuery / Databricks
- [ ] Chat command: `@federated SELECT * FROM proj_a.t1 JOIN proj_b.t2`
- [ ] Or: built into MDL compiler — multi-source JOINs resolved automatically

### Phase 6E — MDL versioning UI (~5hr)
- [ ] Extend `dash_brain_versions` pattern to MDL
- [ ] Diff view (column rename, relationship change)
- [ ] Migration helper (auto-rewrite agent prompts on rename)
- [ ] Rollback to prior MDL version

### Phase 6F — WASM bundle (~8hr · later)
- [ ] Compile MDL validator + ontology lookups to WASM
- [ ] Browser-cached, instant alias resolution + schema autocomplete
- [ ] Replaces server roundtrip for chart suggestions

### Strategic positioning

**Don't BE Wren AI. Be Dash + Wren-ish features.**

```
Wren AI:                  Dash (after Phase 6):
─────────                 ─────────────────────
Semantic broker only      Multi-tenant data agent platform
   ├─ MDL                    ├─ MDL (vendored Wren)
   ├─ 20 connectors          ├─ 11 connectors (subset, expand later)
   ├─ DataFusion             ├─ DuckDB federation
   └─ MCP                    ├─ MCP (Phase 4F)
                             ├─ AutoML (shipped)
                             ├─ Slide Factory (planned)
                             ├─ Cowork hub (planned)
                             ├─ Self-learning (shipped)
                             └─ Domain experts (shipped)
```

Dash absorbs Wren's broker layer + keeps everything else.

### Phase 6 total

~48hr · ~15hr wall with 4 parallel agents. Ship in 1 week.

---

---

## DashSkillHub (branding)

| Layer | Brand |
|---|---|
| Format | SKILL.md (open standard, agentskills.io spec) |
| Registry UI | **DashSkillHub** — curated marketplace at `/ui/cmd-center/skills` |
| Verification | **Dash Verified ✓** — admin-vetted skills |
| Day-1 ship | 50+ custom skills + 5 native + 4 community vendored |

---

## Total counts

| Source | Count | Status |
|---|---|---|
| **Custom shipped** | 22 | ✅ |
| **Custom planned** | 60+ | 📋 |
| **Native cloneable** | 5 | 📋 |
| **MiniMax MIT** | 4 | 📋 |
| **Community marketplace** | 100+ | 💡 (per-project install) |
| **Total Day-1 catalog** | ~80 | mix of ✅ + 📋 |

---

## Decision log

- **2026-05-09 — Adopt SKILL.md open standard** — not Claude/Anthropic-locked, ~30 tools support it.
- **2026-05-09 — Reject Anthropic Office skills (pdf/docx/pptx/xlsx)** — source-available license incompatible with our distribution. Re-implement using MIT/Apache alternatives.
- **2026-05-09 — Vendor MiniMax 4 office skills as MIT alternatives** — drop-in replacement for Anthropic's restricted office set.
- **2026-05-09 — Skip MiniMax coding skills** — wrong category for data agent.
- **2026-05-09 — Brand registry, not format** — DashSkillHub for curation, leave SKILL.md as community standard.
- **2026-05-09 — Compat-first migration** — existing imports keep working through Phase 4B; skills become the source of truth gradually.
- **2026-05-09 — DeepAnalyze: lightweight integration only** — borrow dataset + report style + curriculum docs. No self-host (no GPU). No fine-tuning at our scale.
- **2026-05-09 — Adopt MetaGPT plan-tree pattern (Phase 5A)** — `goal → tasks → actions` decomposition replaces free-form chat for complex pipelines.
- **2026-05-09 — Convert chatty agents → tools (Phase 5B)** — backed by Oct 2025 benchmark showing single-agent + skills matches multi-agent under equal token budgets. Keep 6 high-judgment as agents, demote 12 to tools.
- **2026-05-09 — TaskWeaver code-first state (Phase 5C)** — persistent Python kernel between agent calls. Eliminates DataFrame re-serialization (#1 token sink).
- **2026-05-09 — Vendor Wren AI as MDL engine (Phase 6)** — instead of building our own semantic layer from scratch. Apache 2.0. Saves ~25hr. Get 20+ connector patterns + DataFusion + WASM for free.
- **2026-05-09 — Don't compete with Wren AI directly** — absorb it as our semantic layer. Dash positioning = multi-tenant data agent platform; Wren positioning = semantic broker. Different layers.
- **2026-05-09 — DuckDB for federated queries (Phase 6D)** — 95% of DataFusion's power, zero new infra, embeddable in ML worker.
- **2026-05-09 — DeepAnalyze: framework only, not the model** — Phase 4G adopts methodology, runs on existing OpenRouter LLMs (Gemini 3 Flash · GPT 5.4 mini). Their 8B fine-tune narrower than our LLMs on general reasoning. Methodology + bigger LLM = better outputs without GPU.
- **2026-05-09 — Single shared template injected at 8 surfaces (Phase 4G.1)** — `dash/_shared/deepanalyze_template.py` is the only source of truth for report structure. Inject via 1-line helper. Avoids drift across surfaces.
- **2026-05-09 — Length-aware skip on short queries** — DeepAnalyze structure is overkill for "what's MRR?" — heuristic gates it on complexity threshold.

---

## Open questions

1. Should we ship our `automl-template-*` skills (12 templates) as separate marketplace items so users can install only what they need?
2. Do we need a Dash-specific frontmatter extension (e.g. `dash_required_role: editor`) or stay 100% spec-compliant?
3. Slide Factory: ship PptxGenJS browser renderer first OR python-pptx-server first?
4. MCP server exposure: Phase 4F or never? Dependencies on ecosystem maturity.
5. Wren AI: vendor (Phase 6A) vs build our own MDL engine? Recommended: vendor.
6. DuckDB federation: embed in ML worker (recommended) or separate service?
7. Phase 5 (multi-agent restructure) order: plan-tree first OR supervisor pattern first?
8. Should Wren MDL replace our Brain entirely, or coexist (recommended: coexist — Brain = chat-time context, MDL = SQL-time semantics)?
9. Phase 4G length-aware threshold — what's the right complexity heuristic? word count? keyword? LLM classifier?
10. Phase 4G few-shot examples — load all from DeepAnalyze dataset OR augment with our own best Dash chat outputs? (Recommended: hybrid — 3 from theirs + 2 from ours per skill.)
11. Phase 4G `<details>` collapsible — default expanded or collapsed per section? (Recommended: Quantitative + Recommendations expanded; rest collapsed.)

---

*Last updated: 2026-05-09 (auto-generated from session log)*

---

## References

### Standards & specs

- [agentskills.io specification](https://agentskills.io/home) — open SKILL.md spec
- [github.com/agentskills/agentskills](https://github.com/agentskills/agentskills) — community spec fork
- [anthropics/skills](https://github.com/anthropics/skills) — Anthropic-published reference skills (Apache 2.0 + source-available office)
- [Anthropic Skills cookbook](https://platform.claude.com/cookbook/skills-notebooks-01-skills-introduction)
- [Anthropic engineering: Equipping agents with skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Claude Skills are awesome — Simon Willison](https://simonwillison.net/2025/Oct/16/claude-skills/)
- [Claude Skills deep dive — Lee Hanchung](https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/)
- [Progressive Disclosure vs MCP — MCPJam](https://www.mcpjam.com/blog/claude-agent-skills)
- [Agent Skills are Open Standard — evoailabs](https://evoailabs.medium.com/agent-skills-are-open-standard-can-be-used-with-any-llm-agent-feb0cba4e0ff)
- [Spring AI Agent Skills support](https://spring.io/blog/2026/01/28/apring-ai-anthropic-agentic-skills)
- [Agent Skills in Google ADK](https://damimartinez.github.io/agent-skills-google-adk/)

### Community skill sources

- [MiniMax-AI/skills](https://github.com/MiniMax-AI/skills) — 17 MIT skills (4 useful for Dash)
- [pratikxpanda/agentskills-sdk](https://github.com/pratikxpanda/agentskills-sdk) — Python loader + multi-LLM adapters
- [different-ai/openwork](https://github.com/different-ai/openwork) — open Cowork clone (Tauri + OpenCode)

### Multi-agent data science research

- [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) — DataInterpreter, plan-tree pattern, 67.8k stars, MIT
- [DataInterpreter paper (arXiv 2402.18679)](https://arxiv.org/html/2402.18679v1/) — SOTA on DABench (94.9%)
- [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) — R+D dual-agent loop, leads MLE-bench (30.22%), MIT
- [Canner/WrenAI](https://github.com/Canner/WrenAI) — semantic broker, MDL spec, 20+ connectors, Apache-2.0
- [business-science/ai-data-science-team](https://github.com/business-science/ai-data-science-team) — concrete role decomposition, 5.2k stars, MIT
- [microsoft/TaskWeaver](https://github.com/microsoft/TaskWeaver) — code-first state, persistent Python kernel, MIT
- [microsoft/autogen](https://github.com/microsoft/autogen) — maintenance-only, replaced by [agent-framework](https://github.com/microsoft/agent-framework)
- [crewAIInc/crewAI-examples](https://github.com/crewAIInc/crewAI-examples) — supervisor + role specialists
- [SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) — agentic tree search for full research
- [guosyjlu/DS-Agent](https://github.com/guosyjlu/DS-Agent) — case-based reasoning, ICML'24
- [JulesLscx/DS-Star](https://github.com/JulesLscx/DS-Star) — Google paper implementation
- [K-Dense-AI/agentic-data-scientist](https://github.com/K-Dense-AI/agentic-data-scientist)
- [langchain-ai/langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) — supervisor pattern

### Slide / presentation generation

- [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) — JS PPTX renderer, MIT, v4.0.1
- [PptxGenJS docs](https://gitbrent.github.io/PptxGenJS/)
- [presenton/presenton](https://github.com/presenton/presenton) — markdown → HTML/Tailwind → pptx
- [allweonedev/presentation-ai](https://github.com/allweonedev/presentation-ai) — outline-first, 38 themes, Plate Editor
- [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) — reference deck analysis, vision reflection
- [barun-saha/slide-deck-ai](https://github.com/barun-saha/slide-deck-ai) — cleanest JSON spec, python-pptx
- [Anthropic pptx skill SKILL.md](https://github.com/anthropics/skills/blob/main/skills/pptx/SKILL.md) — confirms Anthropic uses PptxGenJS
- [Streaming pptx-svg pattern (Zenn)](https://zenn.dev/t_ujiie/articles/d5eb5c4c74748d?locale=en)

### Models & datasets

- [ruc-datalab/DeepAnalyze](https://github.com/ruc-datalab/DeepAnalyze) — 8B autonomous data science LLM, MIT
- [DataScience-Instruct-500K](https://github.com/ruc-datalab/DeepAnalyze) — open dataset, used for Phase 4G eval benchmark
- [Anthropic Labs: Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs)

### Papers

- [arXiv 2402.18679 — DataInterpreter](https://arxiv.org/html/2402.18679v1/) — plan-tree decomposition
- [arXiv 2604.02460 — Single-agent vs multi-agent benchmark (2025)](https://arxiv.org/html/2604.02460v1) — single + skills matches team under equal token budget
- [arXiv 2510.26585 — Stop Wasting Your Tokens](https://arxiv.org/html/2510.26585v2) — token efficiency patterns

### Cost & economics

- [Hidden economics of AI agents — Stevens](https://online.stevens.edu/blog/hidden-economics-ai-agents-token-costs-latency/) — Reflexion-style loops 50× tokens

### Existing Dash docs (internal)

- `CLAUDE.md` — full session history + architecture
- `README.md` — feature reference
- `docs/VISIBILITY.md` — visibility framework concept
- `docs/VISIBILITY_API.md` — endpoint reference
- `docs/VISIBILITY_DEMO.md` — pharma E2E walkthrough
- `docs/VISIBILITY_FAQ.md` — common questions

---

## Quick reference index

| Topic | Roadmap section | Key external link |
|---|---|---|
| Skills engine | Phase 4 prereq | [agentskills.io](https://agentskills.io/home) |
| Office format skills | Phase 4 + custom | [MiniMax-AI/skills](https://github.com/MiniMax-AI/skills) |
| Slide Factory | Custom skills | [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) |
| Plan-tree pattern | Phase 5A | [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) |
| Supervisor pattern | Phase 5B | [langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) |
| Code-first state | Phase 5C | [microsoft/TaskWeaver](https://github.com/microsoft/TaskWeaver) |
| MDL semantic layer | Phase 6 | [Canner/WrenAI](https://github.com/Canner/WrenAI) |
| Connectors | Phase 6C | Wren AI · DuckDB |
| Research benchmark | Phase 4G.2 | [ruc-datalab/DeepAnalyze](https://github.com/ruc-datalab/DeepAnalyze) |
| Single vs team validation | Phase 5E | [arXiv 2604.02460](https://arxiv.org/html/2604.02460v1) |
