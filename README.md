# CityPharma

Single-agent pharmaceutical analytics product. One hardcoded **CityPharma Analyst** agent over a locked workspace — pharma inventory / stock / sales analytics, chat-first. Forked from the Dash multi-tenant data-notebook platform and locked down to one agent, one workspace.

> Not a platform. No project creation, no agent builder, no multi-tenant switching. Chat with the analyst, train its brain by uploading data/docs, manage everything from one **Workspace** (Brain included). That's it.

---

## What it is

- **One agent** — "CityPharma Analyst" (Inventory Management · Pharma Regulatory Compliance · Data Integrity). Locked to project `citypharma`.
- **Chat** — ask pharma questions in natural language → grounded SQL answers over your stock/sales/article data, clean Compass-style answer card (SQL hidden in a collapsed trace). **Casual / capability / greeting questions** ("what can you do", "who are you", "hello") get a plain conversational pharmacist reply — no answer card, no charts, no confidence breakdown — while data questions keep the full structured card.
- **Bilingual (English + Burmese)** — the agent mirrors your language: ask in Burmese (မြန်မာ) → answer in Burmese, ask in English → English (brand names + numbers stay as-is). Works in the UI chat and the API gateway, including analytical answers (table headers/labels come back in Burmese too). Driven by a `REPLY_LANG` contract (`dash/instructions.py`) + per-language team cache (`dash/team.py`) + **bilingual training data across every surface** — Burmese twins of training Q&A, agent memories, query patterns, rules, company-brain definitions, and inline Burmese in the schema/column descriptions, with lang-filtered loaders that serve the Burmese row on a Burmese turn (falling back to English where no twin exists). Twins are regenerated automatically on every force-retrain (`scripts/regen_bilingual.py`, hooked into training-complete in `app/upload.py`), so they survive a full retrain. In the Workspace, every training data point is shown **bilingually stacked** — line 1 English, line 2 Burmese (memories, query patterns, definitions/glossary, rules, and each schema column description); the twin stays a separate row in the DB (so the agent loads one clean language) and the UI folds it in for display. Model routing matters: `REASONING_MODEL=google/gemini-3-flash-preview` (honors Burmese) with `openai/gpt-5-mini` as an OpenRouter `models[]` fallback; every model carries `provider.data_collection="allow"` (`dash.settings.OR_DATA_POLICY`) or the account data policy 404s. Details: `docs/DEVLOG.md` latest+27 → latest+29.
- **Workspace** — ONE settings page, ONE left rail, everything in groups: **WORKSPACE** (Cockpit · Training · Docs · Queries · Lineage · Files) · **BRAIN** · **AGENTS** (Agents · Schedules · Evals) · **SHARING** (Users · Config · Embed · Sources) · **INTELLIGENCE** (Learn · Pipeline).
- **Brain** — a group inside the Workspace rail (not a separate page or second menu). Items render as normal rail entries: Definitions · Glossary · Patterns · Rules · Graph · Schema · Org · Promote ⤴ · Pull ⤓ · Conflicts ⚠. A **SCOPE switch** [THIS AGENT · COMPANY · PERSONAL · ALL] sits as a horizontal filter atop the content. Each knowledge category renders as ONE deduplicated merged list — agent-side ∪ company-side keyed by `category::lower(name)` — with per-row status: ✓ synced · ⚠ conflict · ⤴ agent-only · ⤓ company-only. Conflicts expand to a side-by-side diff; **Promote** (agent→company) / **Pull** (company→agent) / **Resolve** act on definitions/patterns/rules (glossary/graph/schema/org are read-only). Backed by `GET /api/brain/unified` + `POST /api/brain/{promote,pull,resolve}` (version-audited). Same `BrainHub.svelte` component also serves the standalone `/ui/brain` route — single source, no drift.
- **Data Source** — redesigned single page (its own full-width no-rail view): **health rings** (tables · rows · %trained · Q&A · vectors · issues) + **expandable table rows** (the live training pipeline moved to the Dashboard — see below). Each row folds its own quality scorecard / columns / FK-links / 5-row preview / **Train now** inline. Backed by one call `GET /api/projects/{slug}/datasource`. Untrained tables auto-train 24/7 (daemon) or via per-row Train-now. A **⚡ Force Train All** button (Workspace header + Data Source `↻ retrain all` + Training tab) POSTs `/api/projects/{slug}/retrain?force=1` to re-run the full pipeline on every table (bypasses the fingerprint "unchanged" skip) and re-refreshes the bilingual twins at the end.
- **Admin** — Command Center (super-admin only), multi-project grids hidden in single-agent mode.

Top nav: **Dashboard · Chat · Data Source · Workspace · API Gateway · Embed · Admin** (Brain lives in the Workspace rail — no separate top-nav button). **Dashboard** is the default landing.

### Dashboard cockpit (`/project/{slug}/overview`)
Operator landing page — one screen, fail-soft strips that reuse existing endpoints: header actions **⬆ Upload data** (→ Workspace Data Source, uploader open) + **🔄 Sync** (rescan data-quality + reload stats, no retrain); **KPI rail** (chats 24h · catalog SKUs · tables · stock value · units), **Training Pipeline** (live boiler-style flow viz — 10-stage schematic + KPI strip + 11-chip data-store rail + collapsible 60-step layer detail + dark live-log; polls `GET /api/projects/{slug}/training-flow` every 2s while training, idles otherwise; `TrainingFlow.svelte`/`TrainingSchematic.svelte`/`trainingFlowSpec.ts`), **Data Tables** (every uploaded table — rows · cols · trained · health pill · per-row `train` + **Train all** + filter/sort; row → Data Source; `/datasource?quality=true`), **System Health** (`/api/health` + daemons), **Data Quality**, **Pharma Signals** (live SQL: stock-outs / low-stock / value / top category), **Eval Health** (latest golden-eval run — pass/partial/fail + avg score, via `/api/projects/{slug}/eval-health`; replaced the empty Tool Health card 2026-06-09), **Insights** (dismissable), **Activity** (training runs), **Live Log**, **Top Questions**, **API Gateway**. 30s auto-refresh. A **single floating auto-train robot** (`lib/FloatingRobot.svelte`) sits bottom-right on the Dashboard + Integration (gateway/embed) screens only — bubble → live CLI log console streaming `/auto-train/log`, small `▶ train`, auto-expands when a run starts. Plus two launchers:

- **Knowledge Graph** (`/project/{slug}/graph` + embedded on the Dashboard) — Obsidian-style **Sigma.js** WebGL force-map, one shared component (`lib/KnowledgeGraph.svelte`) rendered full-page **and** embedded on the overview card (no two-renderer drift). A typed **HUB web** derived relationally from `articles_clean` (no AGE dependency): brand (grey leaf) → generic (yellow hub) → category (red hub), with brand → indication blue bridges fusing the molecule cliques into ONE connected web. ↔ **Brain KG** toggle. Continuous FA2 animation, node size ∝ links, hover-highlight, search → 2-hop ego-graph. **Click a node →** drug nodes show a full profile (identity · clinical · per-store stock bars · substitutes); hub nodes show an aggregate rollup (member list · stock summary · shared clinical · spanned categories) — member rows click through to the drug. Backed by `GET /{slug}/graph` · `/graph/node` · `/graph/hub`.
- **Brain Wiki** (`/project/{slug}/wiki`) — auto-generated, backlinked concept pages (glossary / formulas / aliases / KPIs / patterns + KG entities). Search, `[[wikilinks]]`, backlinks, related siblings, "Ask agent about X". Zero LLM — pure projection of `dash_company_brain` + `dash_knowledge_triples`. The readable wiki layer over the self-learning loop.
- **🧪 Pharma Chemist** card — the clinical brain (Anthropic "Claude as a chemist" analog for pharma retail). Shows catalog size, distinct generics/categories, drugs-with-substitutes, and **clinical-field coverage %** (composition / indication / dosage / side_effect / generic / category). Plus **Clinical accuracy %** from the golden eval + a **Run eval** button. Accuracy auto-refreshes nightly (`chemist_eval` daemon, 24h, leader-gated, kill switch `CHEMIST_EVAL_DISABLED=1`).

---

## Design & architecture

One FastAPI backend (gunicorn + uvicorn workers) serving a SvelteKit 5 SPA, fronted by Caddy, talking to Postgres 18 (pgvector + Apache AGE) through PgBouncer, with Redis for rate-limits/cache. Everything runs as `cp-*` containers from one `compose.yaml`.

```
                         ┌──────────── browser / embed widget / external PHP app ───────────┐
                         │  /ui (SvelteKit SPA)   <script> embed.js   POST /api/v1 (OpenAI)  │
                         └───────────────────────────────┬──────────────────────────────────┘
                                                          │  HTTPS
                                                  ┌───────▼────────┐
                                                  │  Caddy (cp-caddy)   :8090/:8453
                                                  └───────┬────────┘
                                                          │
                                            ┌─────────────▼──────────────┐
                                            │  FastAPI app  (cp-api :8000)│
                                            │  gunicorn + N uvicorn       │
                                            │  ┌───────────────────────┐  │
                                            │  │ auth middleware        │  │
                                            │  │ scope guardrail        │  │
                                            │  │ single-agent lock      │  │
                                            │  └───────────────────────┘  │
                                            │  CityPharma Analyst (Agno)  │
                                            │  team: Analyst·Engineer·    │
                                            │  Researcher·DataScientist   │
                                            │  tools: stock_check ·       │
                                            │  substitutes · indications ·│
                                            │  run_sql · search_all       │
                                            └──┬───────────┬─────────┬────┘
                                               │           │         │
                          ┌────────────────────▼──┐   ┌────▼────┐  ┌─▼─────────────┐
                          │ PgBouncer (cp-pgbouncer)│  │ Redis   │  │ OpenRouter     │
                          │  txn pool → 80 conns    │  │(cp-redis)│ │ Gemini / GPT   │
                          └───────────┬─────────────┘  │ rate +  │  │ LLM + embed    │
                                      │                │ cache   │  └────────────────┘
                              ┌───────▼────────┐       └─────────┘
                              │ Postgres 18    │   cp-ml (forecasts) · cp-mcp · cp-backup
                              │ (cp-db)        │   cp-init (one-shot schema seeder)
                              │ pgvector +     │
                              │ Apache AGE     │
                              │ public.dash_*  │  platform tables (users, feedback, costs, embeds…)
                              │ citypharma.*   │  the locked pharma workspace (articles, stock, sales…)
                              └────────────────┘
```

**Two schemas, one tenant.** `public.dash_*` = platform plumbing (auth, usage/cost, feedback, embeds, training runs). `citypharma.*` = the locked workspace's actual pharma tables. The product is permanently single-tenant — `is_single_agent()` is hardcoded, no project creation, schema name == slug == `citypharma`.

**Data engines (rule):** `get_sql_engine()` = shared READ engine (PgBouncer routes it read-only). `get_write_engine()` = WRITE engine. Every write to `dash.dash_*` / `public.dash_*` must use the write engine, or PgBouncer silently rejects with "Cannot write".

**Knowledge stores** the agent grounds on: relational tables (text-to-SQL), pgvector embeddings (semantic search), `dash_company_brain` (definitions/glossary/rules), `dash_knowledge_triples` + Apache AGE (graph), file-based golden corpus (`_golden.json`).

## Workflow (end-to-end)

**1 · Load → Train.** Upload CSV/Excel/docs in the Workspace → the 14-step per-table pipeline runs (drift → profile → dimension catalog → sample → Q&A gen verified against the real DB → persona → knowledge index → brain fill → bilingual twins), then a master tail (knowledge graph → vector backfill → scope guardrail → evals). Untrained tables also auto-train 24/7 via the leader-gated daemon. Live progress streams in the floating robot console + Dashboard Training Pipeline.

```
upload ─▶ per-table pipeline ─▶ master tail ─▶ embeddings + brain + graph ─▶ agent grounded
            (×N tables, parallel)                 golden corpus + bilingual twins
```

**2 · Ask → Answer.** A chat turn → scope guardrail (off-topic? instant refusal) → metric shortcut (golden corpus hit?) → else the Agno team plans → picks tools (stock_check / substitutes / indications / run_sql / search_all) → grounds on SQL + vectors + brain → returns a structured answer card (SQL hidden in a collapsed trace). Casual/greeting turns skip the card and reply as a plain pharmacist. Bilingual: Burmese in → Burmese out.

```
question ─▶ scope gate ─▶ golden shortcut ─▶ agent team ─▶ tools+SQL+RAG ─▶ answer card (+ 👍/👎)
```

**3 · Feedback → Improve.** 👍/👎 on any answer opens a comment/correction modal → writes `dash_feedback`. A 👎 + correction is an **unverified** claim queued for admin review in `/ui/usage` → admin **Promotes** it into the golden corpus (or dismisses). Verified 👍 + SQL auto-promotes. The self-tuning + auto-evolve loops fold corrections back into the agent's instructions. Same loop runs from the embed widget (anonymous).

```
👍/👎 + comment ─▶ dash_feedback ─▶ admin review (promote/dismiss) ─▶ golden corpus ─▶ better answers
```

**4 · Serve.** Beyond the UI: the **embed widget** (drop-in `<script>`, consumer or analyst mode) and the **OpenAI-compatible API gateway** (`/api/v1/chat/completions`, 3-tier store-scoped keys) let external apps consume the same agent.

---

## Pharmacy capabilities

Primary persona = **pharmacy counter staff**. The chat answers branch-scoped medicine questions:

| Staff asks | Backed by |
|---|---|
| "is X in stock at my branch?" / "do we have X" | `stock_check` (relational, branch-scoped) |
| "find `<salt>`" (e.g. paracetamol) | `stock_check` by generic_name |
| "X is out of stock — alternatives?" | `find_substitutes` / `substitutes` (same generic, relational) |
| "what do we have for `<condition>`" / "something for a bad headache" | **`catalog_search`** (hybrid vector + keyword — semantic) |
| "X is out of stock — alternatives to `<brand>`?" | **`catalog_search`** / `find_substitutes` |
| "tell me about `<drug>` and related" | `drug_relationships` / `drug_profile` |

- **Branch scoping:** each login is bound to a branch (`dash_users.site_code`); the chat injects `## SHOP CONTEXT` so "stock" = their branch, with other branches shown as a transfer hint. 53 sites in the demo data.
- **All drug-relationship tools are RELATIONAL** over the catalog (drugs sharing `generic_name` = substitutes; `indication` ILIKE = therapeutic alternatives). No Apache AGE dependency — survives any cp-db recreate. Tables auto-detected (data lands as `*_07052026`). Tools in `dash/tools/pharma_graph_tool.py` + `pharma_shop_tool.py`.
- **Output = shop medicine-finder** (name · salt · branch stock · cost · substitutes), not analyst KPI cards — for stock/find/substitute queries.
- **Semantic catalog search** (`dash/tools/catalog_search.py`): advisory/find/"what for `<symptom>`"/fuzzy/similar runs **hybrid vector + keyword (RRF)** over 4,886 embedded products (`dash.dash_vectors`, `namespace='catalog'`, built by `scripts/build_catalog_vectors.py`, refreshed on training). Counts + per-store stock stay SQL — vectors only FIND, SQL COUNTS. Catalog is Tier-3 global (no store scope). Beats ILIKE on synonyms/intent (e.g. "high blood pressure" → real antihypertensives, not vitamins).

### Pharma Chemist — the clinical brain

A pharmacist/clinical layer over the catalog's clinical columns (`composition` / `indication` / `dosage` / `side_effect` / `generic_name` / `category`). No fine-tuning, no AGE — **every answer returns its source `article_code` for pharmacist audit** (the trust feature; the agent is instructed to cite it).

| Tool | Does |
|---|---|
| `drug_profile(name)` | full clinical profile — composition, what it treats, dosage, side effects, in-stock |
| `substitutes(name)` | same-generic siblings ranked by stock, each with WHY (matched generic) |
| `indication_search(symptom)` | **inverse** — symptom → candidate drugs (indication data is Burmese; search the user's term) |
| `interaction_check(a, b)` | flags duplicate therapy (same generic) + shared side-effect terms (heuristic — confirm against a clinical reference) |

- **Clinical golden eval** (`POST /{slug}/chemist/eval`): data-grounded forward (drug→profile) · generic (drug→generic) · substitute (drug→siblings) · inverse (symptom→drug) checks run against the real tools, scored to a **Clinical accuracy %** persisted in `dash.dash_chemist_eval`, surfaced on the Dashboard. Nightly `chemist_eval` daemon refreshes it.

> Note: the prior **Apache AGE** pharma graph is gone (cp-db was recreated without the baked-AGE image). The relational tools above fully replace its capability and survive recreate. **The `dash-db` service now pins `image: cp-db-age:pg18` + `build: db/Dockerfile.age`** (durable AGE+pgvector image), so a future cp-db recreate is boot-safe even with `shared_preload_libraries='age'` in PGDATA. To rebuild the AGE graph for graph-native traversal: ensure the image is built (`docker compose build dash-db`) → recreate cp-db → re-run `scripts/build_pharma_graph.py` (it now auto-detects the `*_07052026` tables).

**Data gaps (data, not code):** no retail/MRP price (only cost `weighted_cost_price`); pack/strength is inside the brand string; `dosage` = Burmese patient instructions; no UI yet to self-pick "my branch" (set `dash_users.site_code` per login).

### Models & chat modes (2026-06-09)

Benchmarked all Gemini models on EN+Burmese (generic Burmese tasks + paired EN/MY questions on the real catalog CSV): **`gemini-3-flash-preview` reads and answers Burmese as well as English** (EN==MY parity), so it now runs **every chat + training role**. Only two other models stay: **`gemini-3.1-flash-lite-preview`** for cheap routers/scorers + FAST lookups, and **`text-embedding-3-small`** for vectors. The OpenAI `gpt-5.4-nano/mini` tiers are retired from the runtime.

- **Chat has 2 modes + AUTO:** **FAST** (quick stock/drug lookup, lite model, <500ms) · **REASON** (thinks step-by-step, chat model — multi-part / analytical) · **AUTO** (router auto-picks FAST vs REASON per question). The old 5-tier BI split (ANALYSIS/AGENTIC/REASONING/ULTRA) collapsed to these two — a pharmacy counter needs no more. `/deep` and `/quick` slash commands still force a mode.
- **LLM CONFIG panel** (Command Center → LLM config) is grouped into three cards — **CHAT** (FAST ⚡ / REASON ◆ + AUTO), **TRAINING**, **EMBEDDING** — each showing the model + what it drives (tool chips), click a row to change the model live (no restart, DB wins over env). Legacy per-tier models are tucked into an **Advanced** expander (still editable — `deep_model` still drives deep training tasks).
- **`training_model`** is a real setting now (empty = follow CHAT). Training (Q&A-gen, vision OCR, extraction, dashboard-gen) runs `gemini-3-flash-preview` by default.

### Ingestion paths
- **File upload** (`POST /api/upload`) — CSV/Excel/etc → train. Code/ID columns (e.g. `article_code`) land as **TEXT** even when values look numeric (the `_is_id_colname` separator-normalize) so barcodes aren't rounded and cross-table joins don't hit a bigint↔text mismatch.
- **DB connector** (`/api/connectors/*`) — PostgreSQL / MySQL / Microsoft Fabric: test → pick tables → sync → train.
- **SFTP — not implemented** (no `paramiko`). Build if pull-from-SFTP is needed.

### Training pipeline (2026-06-09 hardening)
- **Status:** `running` → `finalizing` (post-hooks: bilingual twins · catalog vectors · `shop_flat` denorm) → `done`. A run never reports `done` while post-hooks are still building.
- **No hangs:** relationship/QA verify SQL runs with `statement_timeout=30s`; a watchdog (`_reap_stale_runs`, `STALE_RUN_MINUTES` default 12) auto-fails any run stuck with no log progress so the UI spinner can't spin forever.
- **`shop_flat` is derived, not trained** — it's rebuilt by `build_shop_flat` in post-hooks; it's excluded from the training table list and from the stock resolver (`STOCK_COLS` requires `article_code`, which `shop_flat` lacks — it keys on `art_key`).
- **No ML step / no "ML Models" badge** — AutoML was removed; the agent answers are LLM-native, no model training.
- **Data-quality:** a re-upload with bad joins is visible, not silent — orphan stock (code with stock but no catalog row) lands in `shop_flat` as `linked=false`, never as a false "out of stock".

### Engineer semantic layer — materialized views (2026-06-10, flag `ENGINEER_SEMANTIC_LAYER`)
During training an **Agno "Engineer" agent** designs Postgres **materialized views** over the trained tables (pre-joined / pre-aggregated reads). It has READ-ONLY tools only (inspect schema · relationships · sample rows · `EXPLAIN` dry-run) and returns a *structured plan* — it never executes SQL. Trusted Python (`dash/training/semantic_layer.py`) whitelist-validates every proposal (single pure `SELECT`, project schema only, no DDL/comments/cross-schema, mandatory unique index), rebuilds the DDL from the struct, `EXPLAIN`s it, then creates it in one transaction and registers it (`dash_table_metadata.semantic_layer=true`). Matviews are excluded from training and refreshed (`REFRESH … CONCURRENTLY`) after each run. A real run built `article_stock_summary`, `site_inventory_metrics`, `category_performance_stats`. View them at **Data Source → Semantic Layer** (`GET /api/projects/{slug}/semantic-layer`). Derived matviews are consumed **by name** — they are deliberately invisible to the generic table resolver so they can't hijack catalog/stock resolution.

### Catalog enrichment — fill missing fields with the LLM (2026-06-10, flag `CATALOG_ENRICH`)
Missing catalog fields (e.g. ~1,566 rows with no `generic_name`) can be **suggested** by `gemini-3-flash`, grounded on the rows that already have those fields. **Suggestion-only and human-gated — the source table is never mutated.**
- Suggestions land in `citypharma.catalog_enrichment` as `pending` (`app/catalog_enrich.py`); `"unknown"` is allowed and skipped (a blank beats a fabricated value).
- **Low-risk** fields (`category`, `indication`) can auto-approve above a confidence threshold; **clinical** fields (`generic_name`, `composition`) and med-risk (`dosage`, `side_effect`) **always** require human approval — the model is overconfident (≈1.0 even when wrong), so don't trust the number.
- Approvals go live via the **`articles_enriched` view** = `COALESCE(source, approved suggestion)` + an `is_enriched` flag (`app/catalog_apply.py`). Rejecting or re-uploading instantly reverts; `shop_flat` reads the enriched view so filled names flow downstream.
- Review at **Data Source → Catalog Gaps** (`/api/projects/{slug}/catalog-enrich/{gaps,run,suggestions,decide}`). `CATALOG_ENRICH=0` by default (the LLM gap-fill costs); the view and manual approvals work regardless.

---

## API Gateway — OpenAI-compatible (`/api/v1`)

External apps (e.g. a PHP storefront) call the CityPharma agent through a standard OpenAI client — swap base URL + key, no SDK changes.

> **Public base URL (AWS):** every URL shown to developers (gateway `base_url`, embed snippets, downloadable SDK files, logo URLs) is driven by one env — set `PUBLIC_URL=https://pharma.yourdomain.com` on deploy and they all follow. Blank locally → the dashboard uses the browser origin. Exposed via `GET /api/flags` (`public_base_url`).

- **Endpoints:** `GET /api/v1/models`, `POST /api/v1/chat/completions` (blocking + `stream:true` SSE).
- **Auth:** `Authorization: Bearer dash-key-…` service keys, minted super-admin only.
- **Admin console:** `/ui/gateway` (super-admin) — rail: **Console · Overview · Analytics · Developer (Quickstart…Errors) · Access · Rate Limit**. **Console** is the default landing — an **all-in-one workspace** that fuses the three interactive tools on one page (no separate Chat/Keys/Outlets tabs): **Service Keys top-left · live Chatbot top-right · Store/Outlets full-width bottom**. Built with Svelte snippets so the same components also back any standalone view. **Overview** opens with a **HOW IT WORKS** flow diagram (`RequestFlow.svelte`): question → gate (auth·rate·scope) → agent → tier-mask → OpenAI response.
- **Service Keys** (Console top-left): **cards** (not a table) — status dot + name + scope pill + **plain-words tier** ("tier-1 own (N outlets) · tier-2 cross" / "reference only" / "global · full"), the store binding, and a **usage join** ("N req · last …" from the analytics window). Inline **+ MINT** drawer; secret shown **once** at mint (never retrievable after).
- **Chatbot** (Console top-right): a **multi-turn chatbot** hitting the real `POST /api/v1/chat/completions` — paste a `dash-key-…` (auto-fills from a freshly-minted key), optional stream toggle, scope chips (own / other branch / catalog) prefill the composer. Each reply shows **tokens · latency** + a **🛡 masked** badge (client heuristic; masking enforced server-side) + a **▸ inspect** expander that folds the per-turn **request line + copy-as-cURL + raw JSON** into the reply (no side panel).
  - **Live "agent working" strip** (opt-in): when the Console streams it sends `X-Agent-Steps: 1`, and the gateway interleaves tool/reasoning activity as chunk frames carrying a non-standard `delta.x_agent_step` field (🧠 Reasoning · 📦 checking stock · 🔧 …). Official OpenAI SDKs ignore unknown delta keys, so **external clients still get an answer-only v1 stream** — only the internal Console renders the strip. **ChatGPT-style trace (2026-06-08):** steps are Title-Cased phase titles ("Querying inventory", "Checking branch stock", "Planning the stock lookup"); the active phase **shimmers** (gradient light-band sweep, not flat dots); done steps stack in a `✓` rail; once the answer lands the rail collapses to a clickable **"▸ Worked for 4.2s · 3 steps"** fold (ChatGPT-like). Same `x_agent_step` pipe — restyle only, no transport change. *Frontend is vite-baked into the image — changes need `docker compose build dash-api`, and a browser hard-refresh to drop the cached bundle.*
  - **Clean single-shape answers:** the gateway prepends an `[API MODE]` style directive (answer + at most one compact markdown table, never the dashboard SOURCES/WHY/KPI scaffolding) and the drain **de-duplicates** the team echo — in coordinate mode the analyst member streams the real answer and the leader re-synthesizes it; the gateway streams the member answer and drops the leader echo, so the reply is never printed twice. The Console renders markdown tables natively (`renderMd` GFM tables).
  - **Warm chemist voice (store keys):** store-key stock answers are reshaped into a counter-pharmacist reply by a deterministic post-processor (`_humanize_api_answer`) — a natural opening ("Yes — we're stocked on Paracetamol at your branch, 5 lines on the shelf right now."), a clean **Medicine · Salt · Stock · Price (MMK)** table (raw `article_code`/`composition` columns dropped, friendly headers), no model-computed (and often wrong) Total/Summary block, and one **correct** "💊 Tip: X is your deepest stock" computed from the rows. Catalog matches that are all out of stock get an honest "we carry it but every line's out — worth a transfer or substitute" lead instead. Runs on both the blocking and streaming paths, so the Console shows the same clean reply (no raw `##`/`###` scaffolding); the analyst system prompt overrides any prompt-level directive, so the cleanup is done in code, not by asking the model. Global/BI keys keep the analyst format.
- **Store / Outlets** (Console bottom, full-width): a **freshness card** proving currency — `source_table · row_count · outlet_count · uploaded (rel-time)` from `GET /api/auth/apigw-outlets` (enriched via `dash_table_metadata.updated_at`) + ⟳ refresh — over a `site_code` grid with **▣ bound / ○ unbound** badges (client join vs active keys). Resolved from the **current uploaded** stock table (`dash/tools/table_sync.py`), so it never drifts from what the agent queries.
- **Deployable copy snippets + all-shops bundle (2026-06-08):** each outlet's **copy** code (PHP · curl · Python · .env) is a **complete runnable streaming + live-thinking client** — it sets `stream:true` + header `X-Agent-Steps:1` and parses both `delta.content` (answer) and `delta.x_agent_step` (the live trace), so a dev gets the full Console experience, not a flat blocking answer (warm format is server-side, automatic). **⬇ Bundle .zip** (next to Copy .env / CSV) → `GET /api/embed/sdk/gateway-bundle.zip` → `citypharma-shops.zip` = one **key-agnostic** client (`examples/multishop/client.php` + `client.py` + README) that loads the **Copy .env** download, loops every `CITYPHARMA_KEY_<outlet>` and serves **all shops from one file** (new shop = one `.env` line, zero code change). Bundle ships **code only — no live keys**; `ask_shop(...)` is a reusable fn so the dev wires the live strip into their own UI. *Caveat (in README + comments): the thinking trace needs raw SSE parse — official OpenAI SDKs drop `x_agent_step` → answer-only.*
- **Standalone docs page:** `GET /api/v1/docs` — full HTML developer guide (quickstart, auth, schemas, streaming, code examples). No auth required, suitable for sharing with external dev teams.
- **PHP gateway tester** (`examples/php-tester/`): a runnable external-app demo — single-file `index.php` (browser chat UI → PHP backend holds the key → gateway) + `docker-compose.yml`. One command: `docker compose -f examples/php-tester/docker-compose.yml up -d` → **http://127.0.0.1:8092** (joins the stack network, hits the API as `cp-api:8000`; no PHP install needed). Quick-question chips, stream toggle, per-reply HTTP/latency/tokens + copy-cURL + raw JSON, and **key presets** (Global·analytics / Store·stock) so you can see the tier behavior live. Native run: `php -S 127.0.0.1:8090 -t examples/php-tester`.
- **Live rate limit:** Redis fixed-window, per store key, editable in UI (`API_GW_RATE_PER_MIN` fallback). 429 + `Retry-After` on exceed.

### 3-tier store-scoped access (the security boundary)

Each key is bound to a SET of outlets. The **toolset is the boundary** — store keys lose raw SQL at build time, so prompt injection can't pull cross-store quantities.

| Tier | Scope | Sees |
|---|---|---|
| 1 — owned outlets | any `site_code` in the key's SET | full data incl. stock_qty + cost |
| 2 — other stores | not in the SET | availability only — no qty, no price |
| 3 — reference | rows with no site_code (catalog, substitutes, indications) | unrestricted |

`scope_mode=store` = tiered masking · `scope_mode=global` = no mask (internal/admin only).

> **Pick the tier for the app, not just security.** Because store keys lose `run_sql_query`, they answer **pharma tools only** (stock / drug / substitute / indication). **Analytical** questions — catalog totals, category breakdowns, top-N, counts — need raw SQL, so they only work on a **global** key. A store key asked an analytical question replies *"I couldn't query the database… No data returned"* (`ERROR Function run_sql_query not found` in logs) — that's the SQL-strip boundary working as designed, not a data fault. Shop-counter app → store key; BI/reporting app → global key.

Masking is enforced two ways: store keys lose `run_sql_query`/`introspect`/raw-SQL specialists at tool-build time (`is_store_locked()` gate), and `mask_row()` nulls the sensitive field set on any non-owned row as belt-and-suspenders. `_SENSITIVE_KEYS` (`dash/api_scope.py`) covers per-row **and** aggregate quantity/value fields — `stock_qty`, `your_stock`, `qty`, `total_stock_qty`, `cost`, `weighted_cost_price`, `price`/`unit_price`/`mrp`/`retail_price`, `sales_value`/`value`/`amount`/`revenue`/`total_inventory_value`.

### One key, many outlets (set-membership)

A single key can own multiple stores. `dash_users.store_ids` (CSV) holds the SET; the agent sums Tier-1 stock across **all** owned outlets and returns a per-outlet breakdown.

```bash
# mint a multi-outlet key (super-admin session token, NOT a dash-key)
curl -X POST $HOST/api/auth/api-key \
  -H "Authorization: Bearer <SUPER_ADMIN_TOKEN>" -H "Content-Type: application/json" \
  -d '{"service_account_name":"php-multi",
       "store_ids":["20060-CCBHSC","20063-CCBRBKMY"],
       "scope_mode":"store"}'
# → {"api_key":"dash-key-…", "store_ids":[...]}   (key shown once)

# single outlet still works (back-compat): {"store_id":"20060-CCBHSC"}
```

Outlet picker for the mint form: `GET /api/auth/apigw-outlets` (distinct site_codes). Enforcement implemented in `dash/api_scope.py` (`StoreScope.stores`, `owns()` membership, `bound_stores()`), `app/auth.py` (mint/list/validate/resolve), `dash/tools/pharma_shop_tool.py` (`site_code = ANY(owned)`), `app/api_gateway.py` (response sanitizer whitelists owned set). Full reference: `docs/API.md`, standalone HTML guide: `GET /api/v1/docs`, sample client: `examples/php-openai-client.php`.

---

## Embed Widget (`/ui/embed`)

Browser-facing access to the same agent. Drop a `<script>` tag on any web page — the widget opens a chat bubble backed by the CityPharma Analyst with **the same 3-tier store-scoped access** as the API Gateway.

- **Admin console:** `/ui/embed` (super-admin) — **Overview · Brand · Widgets · Monitoring · Snippet & Docs** (rebuilt 2026-06-09). Clicking a widget opens a **per-widget cockpit** (Appearance · Snippet & Deploy · Share link · Activity + live test) — this replaced the standalone **Playground** tab. (The separate **Usage Analytics** tab was removed 2026-06-09 — Monitoring is a superset.)
- **Monitoring** (`#monitoring`, 2026-06-08): rich usage dashboard (embed equivalent of the Gateway Usage Analytics) — `GET /api/admin/usage/embed-overview` over `dash_embed_calls`. KPI strip (requests, error %, avg/p50/p95/p99 latency, unique users, sessions, active widgets, avg reply chars), activity bar chart, latency distribution, per-store/widget table, top users, origins; 24h/7d/30d + widget-picker + hour/day filters + CSV/JSON export. **No token/cost** — embeds don't log them (gateway-only).
- **Disable surfaces (super-admin kill switch, 2026-06-08):** two toggles `gateway_enabled` / `embed_enabled` on **Admin → Overview → INTEGRATIONS card**. **`gateway_enabled` defaults OFF as of 2026-06-09** — a fresh install does NOT expose `/api/v1` until a super-admin turns it on (embed stays ON). The card has two switches + **Save** (saving reloads so the nav updates); also in Admin settings → Integrations. OFF = the surface vanishes from the Integrations nav **and** its API routes return 403 (`/api/v1*` for gateway, `/api/embed*` for embed) — enforced in `AuthMiddleware` (30s cached, fail-open). State surfaced via `/api/flags`, which reads the DB fresh (not the settings cache) so the nav reflects a change on the next reload.
- **Overview** (`#overview`) opens with a **HOW IT WORKS** flow diagram (`RequestFlow.svelte`, cockpit-style lanes): a worked example question → handshake (origin+HMAC) → agent → 3-tier mask → masked answer bubble, so a dev sees the whole request path at a glance.
- **Brand** (`#brand`, single-point theme, 2026-06-09): set color/position/theme/welcome/logo **once** and **every widget inherits it** — no per-store clicking. Live preview + inheritance counts + **Reset ALL widgets to brand**. Backend `GET/PUT /api/projects/{slug}/embed-brand` + `POST .../reset-widgets`; stored in the global `embed_brand` setting. Render resolution in `/api/embed/config/{id}` = `per-widget override (non-empty) ?? brand default ?? hard fallback`. A widget keeps its own look only if you pick **○ Override** in its cockpit Appearance (default is **◉ Inherit**).
- **Widget cockpit** (click a widget): Appearance (Inherit/Override + logo upload) · Snippet & Deploy · Share link · Activity, with a persistent **real in-page chat-bubble test**. The **shareable signed test-link** lives here (pick expiry + store + role → `POST /embeds/{id}/test-token` → signed expiring `/api/embed/try/{id}?token=…`; secret stays server-side). Replaces the old Playground tab.
- **One-click deploy (2026-06-09):** `⤓ Deploy .zip` on each widget = a ready-to-host folder (`index.html` working page + `snippet.html` + `README.txt`, **keys pre-baked, no editing**) via `GET /api/embed/deploy/{id}.zip`; a header **⤓ Download ALL stores (.zip)** (`/deploy/all.zip`) bundles every store (folder-per-store + `INDEX.html` + `HOW-TO-DEPLOY.txt`). Base URL templated from `PUBLIC_URL`. Both public (in `main.py` SKIP_PREFIXES).
- **Widget UX** (`dash/embed/widget.js`, shadow-DOM isolated): bot replies left-aligned (resets `text-align` which inherits across the shadow boundary from a centered host page), markdown renders headings / ordered+bullet lists / bold+italic / code, numbers shown as **digits** (`1,272,014 units`, not spelled out). A **live agent-activity strip** streams what the agent is doing — seeded "understanding your question" step (never a blank `thinking…`), each step ticks **✓** when the next starts, a "writing answer" step on first token, then collapses to `✓ done · 1.2s · N steps` (click to re-expand). The loading indicator is **3 store-accent bouncing dots** (`.load-dots`) — not a blinking cursor — shown while waiting and trailing after streamed text, gone on done. **Consumer/store-scoped embeds stream the activity strip but NOT raw tokens** — the answer is buffered, masked (currency band + qty), then emitted in one chunk so 3-tier masking is never bypassed mid-stream; analyst-style embeds stream token-by-token.
- **Clean consumer output + thumbs + follow-ups (2026-06-10):** consumer-mode embeds no longer leak the model's raw reasoning-step titles into the activity strip (was showing junk like "music ×9") — all thinking collapses to one `🧠 Thinking`, only friendly tool labels stream, capped at 6 visible steps. `sanitize_consumer_response` strips banded-price tails (`— [banded] MMK` → clean ranked list), truncates on a sentence/line boundary (no dangling `*…`), and adds a "_Prices hidden — ranked highest to lowest_" note. Every bot answer gets **👍 / 👎** (👎 → inline tag chips + comment) → `POST /api/embed/feedback` → the **same `dash_feedback` table** as app chat (anonymous, `session_id='embed:{embed_id}'`) so it shows in admin Like/Dislike + feeds training. **Per-answer follow-up chips** (zero-latency heuristic) appear under the answer.
- **Widgets admin list** (`#widgets`): rows **expand inline** (no navigation) → **KEYS** (embed_id · public_key + **↻ rotate** · **secret reveal** [Fernet-decrypt via `GET /embeds/{id}/secret`, super-admin; public-auth widgets show "no secret"] · endpoint), **CONFIG** (scope/role/rate/auth/status), **drop-in snippet**, **FULL PHP CODE** (tabs `widget-embed.php` / `CityAgentClient.php`, templated with the widget's keys; the HMAC secret stays a server-side `getenv()` — never baked into the code), and actions (**⤓ Deploy .zip · ▶ Test · ⚙ Configure** [→cockpit] **· Revoke**). `↻ rotate` regenerates the public key (old one dies; `POST /embeds/{id}/rotate-key`). One embed per store (`bound_scope_id=<site_code>`, `bound_intent=private`) = isolated per-store answers; bulk-mintable via `embed_mgr.create_embed`.
- **Auto-provision per new outlet (2026-06-08):** you don't mint store widgets by hand. When a new outlet appears (a stock upload introduces a new `site_code`), a `store-<site_code>` widget is created automatically — same path that auto-mints the outlet's API key. It fires on **stock ingest** (immediate), on opening the **Widgets** tab, and via `POST /api/projects/{slug}/embeds/provision-stores` (for a scheduler/button). Idempotent (existing widgets untouched) and toggleable with `EMBED_AUTO_PROVISION=0`. The `setup_embeds.py` script is now load-test only.
- **Snippet & Docs** (`#developer`) = status banner (live/draft + origins) + 3-path snippet toggle (Drop-in / HMAC-PHP / REST) with **real keys pre-filled** + errors cheat-sheet + a **DOWNLOAD SDK** section: drop-in client files (`examples/CityAgentClient.php`, `widget-embed.php`, `rest_client.py`, `rest_client.js`, `quickstart.sh`) — preview / copy / download per file or **Download all (.zip)**, each served **pre-templated with this embed's real keys + host** (`GET /api/embed/sdk*`, no Composer/pip). The SDK clients handle canonical-JSON HMAC signing, session token caching, and SSE parsing for you. Mirrored by the no-login public page `GET /api/embed/docs`. Full dev handoff in `EMBED_DEV_HANDOFF.md` (repo root).
- **Test chat:** also reachable from the **Widgets** tab **▶ Test chat** button → `GET /api/embed/try/{embed_id}` (public embeds open directly; `?claim_store_id=&claim_role=` impersonation to test masking). For `access_mode='signed'` embeds a valid `?token=` is required — the minter (`gen_test_token`) and verifier (`_verify_test_token`) sign the identical `embed_id|nonce|exp|claims_canon` string.
- Tabs persist in the URL hash (`#playground`, `#widgets`, …) — refresh stays on the same tab.
- **Mint widgets:** bind each embed to a store (or leave unbound for catalog-only Tier 3 access).
- **Store-scoped auth:** pass `data-user='{"store_id":"20063"}'` signed with HMAC → session enforces Tier 1/2/3 masking via the same `StoreScope` ContextVar as API Gateway.
- **Public mode:** no `store_id` → Tier 3 only (drug catalog, substitutes, indications). Staff cannot see any stock quantities without a signed store claim.
- **Concurrency:** unlike the gateway, the embed path is NOT serialized — N stores run in parallel, gated by the async `LLM_PARALLEL_CAP_CHAT` semaphore (default 20). Each store gets its own per-store cached team (keyed on the embed's synthetic viewer id), and the SQL tool enforces `WHERE site_code` via the `StoreScope` so a store can never see another store's numbers.
- **Load test:** `examples/embed-test/` — `setup_embeds.py` makes N store-bound embeds, `run_embed_test.py` fires them concurrently (EN+MY) and scores latency / %Burmese / per-store accuracy vs DB truth → CSV. Flush the embed cache first (`docker exec cp-redis redis-cli FLUSHALL`) for honest latency. Verified 38/40 (95%) correct under 20-way parallel.

```html
<!-- Store-scoped embed (staff terminal) -->
<script src="http://localhost:8011/api/embed/widget.js"
        data-embed-id="emb_XXXX"
        data-key="pub_XXXX"
        data-user='{"store_id":"20063-CCBRBKMY","role":"staff"}'
        data-user-sig="<hmac-sha256>"
        async></script>
```

Access enforcement: `app/embed_public.py` calls `resolve_api_scope()` from session `user_attrs` and sets `API_STORE_SCOPE` ContextVar before every chat call.

## Embedding API + vector search (`app/embeddings_api.py`)

OpenAI-compatible embeddings + per-project semantic vector store.

- `POST /v1/embeddings` — OpenAI-compat. Body `{input: str|str[], model?}`. Returns 1536-dim L2-normalized vectors (`openai/text-embedding-3-small` via OpenRouter, deterministic pseudo-vector fallback only when no key). The `model` field is a display label — `"default"`/empty resolves to the real embedder.
- `POST /api/projects/{slug}/vectors/ingest` — bulk upsert `{rows:[{namespace, source_id, text, scope_attrs?, metadata?}]}`, sha256 dedup, RLS-scoped (`dash.dash_vectors`).
- `POST /api/projects/{slug}/vectors/search` — `{query, namespaces?, top_k, hybrid?}` → cosine (pgvector) or hybrid BM25+vector.
- `GET /api/projects/{slug}/vectors/list` · `DELETE /api/projects/{slug}/vectors/{source_id}`.

Gotchas baked in: writes go through **`get_write_engine()`** (the read engine has a read-only guard that silently rolls back); the best-effort audit row writes in its **own transaction** (a failed audit INSERT would otherwise abort the txn and roll back the real inserts); RLS session vars set via `set_config(...)` (can't bind-param `SET LOCAL`). Verify ingest/search via the API or in-container engines — a direct `docker exec cp-db psql` may hit a different DB than PgBouncer routes the app to.

## Usage & Cost dashboard (`/ui/usage`)

Standalone super-admin page (Admin ▾ → People → **Usage & Cost**) unifying **all** usage across sources — platform chat · API keys · embeddings · embed widget · training — into one cross-source view with date filters. **Left-rail nav** (grouped: Overview / Models & Tokens / Learning / People / Analytics / Billing) + KPI tile row + per-section `● live · Xms` badges. The rail is the shared **"Admin Clean"** style — flush full-height, flat `--pw-bg-alt`, 220px, white-card active with a 3px terracotta accent bar — **byte-identical to the Admin command-center rail** (unified 2026-06-09; both `.u-rail`/`.cc-rail` share spec + `--pw-*` tokens). Sections:

- **Overview** — KPI tiles (Spend/Requests/Tokens/Errors/Active-users) then `◷`-titled **section panels**: Trends (stacked-by-model Spend/Requests/Tokens cards, period-over-period ▲▼ + cost-per-request & per-1k-tokens), Breakdown (by-source & by-model tables), Activity heatmap (day×hour), Users & activity (who-logged-in + grouped breakdown + full activity log CSV export).
- **Models** (2026-06-10) — spend-by-model table with bars + a **Trending** panel (this window vs the immediately-preceding equal window: ▲New / ↑% / ↓%). From `v_usage_unified` (`GET /api/admin/usage/models`).
- **Tokens** (2026-06-10) — prompt / completion / **reasoning** / **cached** token breakdown + **cache-hit-rate** KPI + stacked bars. Reasoning + cached are captured from OpenRouter `usage.*_tokens_details` into new `reasoning_tokens`/`cached_tokens` columns on `dash_llm_costs` + `dash_apigw_usage` (mig 182) — forward-filling (`GET /api/admin/usage/tokens`).
- **Embeddings** (2026-06-10) — calls / tokens / cost / models / avg-latency tiles + recent embedding calls **with the embedded text/question** (new `input_preview` column on `dash_apigw_usage`, privacy-gated `EMBED_LOG_INPUT=1`; shows an enable-hint when off) (`GET /api/admin/usage/embeddings`).
- **Like / Dislike** (Learning, 2026-06-10) — 👍/👎 totals + **satisfaction %**, by-project satisfaction, and **top-disliked answers** with the user's **comment + tags + correction** (question + answer + SQL = retrain candidates). From `dash_feedback` (`GET /api/admin/usage/feedback`). Each pending correction gets **▲ Promote → golden** (`POST …/feedback/{id}/promote`) + **Dismiss** — a 👎 correction is an *unverified* claim, so the admin reviews before it trains (only verified 👍+SQL auto-promotes). Real user thumb-clicks now persist here (previously only synthetic training seeds did); embed widget feedback flows in too. Comment/correction are threaded into the agent's evolved-instructions + AVOID-PATTERNS prompt. *(Per-model satisfaction is a known gap — `dash_feedback` has no model column.)*

  **Feedback capture** — every chat answer (app + embed) shows a thumb. `FeedbackModal.svelte` opens on click: 👎 → quick-pick tags + "what was wrong" + optional "correct answer/SQL"; 👍 → optional note. Migration 183 added `comment`/`comment_tags`/`correction`/`correction_status` to `dash_feedback`.
- **People** (2026-06-09) — per-user activity, **split into two populations** via an `App users / Embed users` segment toggle. *App users* = registered `dash_users` (humans + `svc:*` API keys): sortable leaderboard (Last active · Sessions · Questions · Q/sess · 👍/👎 satisfaction · Tokens · Cost · Err%) + search + humans-only toggle; click a row → drawer (KPIs, 👍/👎, daily-questions sparkline, by-source, recent sessions, rated questions). *Embed users* = **anonymous** widget visitors from `dash_embed_calls` (identity = `session_token`, grouped per `embed_id` → store): By-session / By-widget tables; click a session → drawer with origin/IP + full message-turn conversation (bodies when `EMBED_LOG_BODIES=1`). Two separate populations, two tables — never mixed.
- **Performance** — p50/p95/p99 latency overall + by source/model + slowest calls. (Reads `v_usage_unified.latency_ms`, added by **mig 179** 2026-06-09 — the live mig-174 view was missing the column so this tab + the Overview activity feed silently rendered empty; appending it revived both.)
- **Errors** — error rate, by-source, top error codes, recent failures.
- **Tools** — what the agent actually ran (per-tool calls / error% / p50 / p95).
- **Security** — guardrail events: cross-store leak attempts, rate-limited (429s), auth failures.
- **Entities** — top users/keys & stores → click any row for a slide-over drilldown drawer.
- **Billing** — daily/monthly budget targets (alerts when over) + invoice rollup per store/key (CSV).
- **Live** — active sessions, tokens/min, 5s auto-refresh.

**Layout (all rail pages, 2026-06-09):** left rail is fixed; only the right content pane scrolls (independent scroll). Pattern = shell `height:calc(100vh-64px)+overflow:hidden`, rail `stretch+overflow-y:auto`, main `min-height:0+overflow-y:auto` — applied to Usage, Admin command-center, Embed, Gateway, project Settings. On Usage → Overview → **Users & activity**, the Breakdown / Who tables are stacked top/bottom, each `max-height:400px` with internal scroll + sticky header.

Backend `app/usage_api.py` (`/api/admin/usage/*`, super-admin, fail-soft; People tab adds `/people` + `/person` for registered users joining users⋈sessions⋈feedback⋈usage-view, and `/embed-people` + `/embed-session` for anonymous embed visitors off `dash_embed_calls`⋈`dash_agent_embeds`). Cost spine = `public.v_usage_unified` view in its **LIVE 174 shape** (mig 175's dash_traces rewrite is logically-applied but the live view is 174's — drift): platform/training from `dash_llm_costs`, gateway chat + embeddings from `dash_apigw_usage`, embed widget from `dash_embed_calls`. **Real LLM cost (mig 178, 2026-06-09):** the gateway used to log the caller's OpenAI *alias* (e.g. `citypharma-analyst`) → no price match → `$0`; now `app/api_gateway._log_usage` + `app/embed_public` store the **real engine model** (`engine_model` col on dash_apigw_usage + dash_embed_calls) + price off it (`dash.settings._compute_cost`), and the view surfaces `COALESCE(NULLIF(engine_model,''), model)` so "BY MODEL (COST)" shows `google/gemini-3-flash-preview` with non-zero cost. Old embed rows stay `$0` (tokens were never logged — unrecoverable; new calls price). Optional gateway chat-body logging behind env `APIGW_LOG_BODIES=1` (off by default). Full design + gotchas: `docs/DEVLOG.md` latest+44.

## Brain (Workspace) — single-tenant merged view

Brain is one unified view (single-tenant). The multi-tenant scope tiers (THIS AGENT / COMPANY / PERSONAL) and the Promote / Pull / Conflicts sharing layer are hidden — the backend `/api/brain/unified?scope=all` already unions everything, so the merge is display-only and reversible. Rows show a type-colour dot + inline value preview; each list has a filter box. The **Graph** view has a **MAP / LIST** toggle: MAP is a force-directed ECharts node-link (tables = circles, metrics = diamonds; value-spam predicates like `found_in_column` collapse to a single node).

---

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + Agno (AgentOS), gunicorn (`app.main:app`), Python 3.12 |
| DB | PostgreSQL 18 + pgvector, via PgBouncer (transaction pooling, NullPool). **172 tables** (public 97 / dash 51 / ai 21 / citypharma 3) — pruned from 221 (mig 180/181 dropped 49 dormant inherited-Dash + Skills tables, 2026-06-09); per-table map in `docs/TABLE_TEST_REPORT.md` |
| Cache | Redis |
| Frontend | SvelteKit 5 (Svelte 5 runes), base path `/ui`, adapter-static, Tailwind v4, ECharts |
| Proxy | Caddy |
| LLM | OpenRouter (Gemini / GPT / Claude tiers via `dash/settings.py`) |

Single font family (Inter) across the whole UI — `--pw-serif` aliases the sans stack.

---

## Single-agent lock (permanent — single-tenant)

CityPharma is a **single-tenant** product built on the multi-tenant Dash codebase. A second tenant is now **structurally impossible** — four independent, fail-closed guards:

1. **`is_single_agent()` is hardcoded `True`** (`dash/single_agent.py`) — NOT env-controlled. No stray env var can flip the product back to multi-tenant. To restore full Dash you must deliberately edit that function.
2. **`create_project_schema()` refuses any slug but the locked one** (`db/session.py`) — the only place a new tenant schema can be born raises `RuntimeError` for non-locked slugs, even from internal callers.
3. **`_make_slug()` always returns the locked slug** (`app/projects.py`) — the slug minter can't produce a new tenant id.
4. **`guard_no_project_management()` 403s** the create/delete/duplicate HTTP endpoints; `list_projects` is scoped to the locked slug only.

Env (still read for `locked_slug` / `product_name`, but `SINGLE_AGENT_MODE` no longer gates the lock):

```
SINGLE_AGENT_MODE=1                       # informational; lock is hardcoded
LOCKED_PROJECT_SLUG=citypharma
PRODUCT_NAME=CityPharma
```

- `GET /api/flags` (public, no auth) exposes `single_agent`, `locked_slug`, `product_name`.
- Frontend `+layout.svelte` fetches flags on mount → renders the 4-item nav, redirects `root/home/projects/chat` → the locked chat.

**Tenancy model.** The DB design stays multi-tenant-shaped (schema-per-project + `project_slug` columns) but runs locked at N=1: one fixed data schema `citypharma`, one agent. The slug was renamed `proj_demo_citypharma` → `citypharma` for production (2026-06-07; atomic schema+table+row migration — see `docs/DEVLOG.md` latest+20). `project_slug` columns + `dash_*` table names stay as harmless plumbing labels (dropping `project_slug` = weeks, buys nothing). Functionally matches upstream `agno-agi/dash` single-tenant behavior.

### Single-tenant daemon gates (quiet boot)

The fork inherits multi-tenant Dash daemons that, single-tenant, just hammer pruned/un-migrated tables and spam errors (training + chat are unaffected). Gate them off:

```
CONNECTOR_SCHEDULER_DISABLED=1   # no external DB connectors single-tenant
VERTICAL_DAEMONS_DISABLED=1      # venture/ops/supply verticals are off here
```

Both default to `1` for CityPharma and **must be set in BOTH `.env` AND the `dash-api` `environment:` block in `compose.yaml`** — compose lists individual env vars (no `env_file`), so `.env` alone won't reach the container. With these set, boot is clean (0 ERROR / 0 failed-migration); the only remaining lines are `WARNING X not loaded/not started` for intentionally-pruned routers + gated daemons (expected confirmations, not errors).

**Migration tracking note:** if `public.dash_migrations` count < the number of files in `db/migrations/`, the runner retries the pending ones every boot and the pruned-feature ones fail loudly. On an already-established schema, mark them applied: `INSERT INTO public.dash_migrations(filename) SELECT … ON CONFLICT DO NOTHING` (the runner skips on filename presence). Reversible.

### Data-ingest hardening (ID/code columns)

Large ID/code values exported as scientific notation (`1E+12`) or read as float silently lose precision and **destroy join keys** (e.g. `article_code` → all rows collapse to one value → catalog↔stock join returns 0). Guards:

- **Ingest** (`app/upload.py`): columns matching `*_code / *_id / barcode / sku / ean / upc / gtin / ...` are read as **string** (exact), exempt from numeric coercion, and whole-valued floats are repaired to `Int64`. So a clean export keeps codes intact; a corrupt one lands as text (flagged below) instead of silent float.
- **Data Quality** (`/datasource`): flags an ID column with **≤1 distinct value across many rows**, and a shared key with **0 cross-table value overlap** → `summary.join_warnings`. Surfaces a broken join before you trust an answer.
- **Tools** (`stock_check`): if a matched drug's `article_code` links to **zero** stock rows, returns `stock_linkable:false` + a `linkage_warning` and the agent says *stock unavailable (data issue)* — never a false "out of stock".

If drug↔stock questions return "can't link", the fix is a **clean re-export of the stock file** (article codes as text, not `1E+12`).

---

## Authentication (local + LDAP + OIDC/SSO)

Local username/password is always on. **LDAP** and **OIDC/SSO** are optional, off by default, OpenWebUI-modeled (`app/auth_federation.py`):

- **LDAP / Active Directory** — `ldap3` bind-search-bind. Set `ENABLE_LDAP=true` + `LDAP_SERVER_HOST/PORT`, `LDAP_APP_DN`, `LDAP_APP_PASSWORD`, `LDAP_SEARCH_BASE`, `LDAP_ATTRIBUTE_FOR_USERNAME` (`sAMAccountName` on AD). Users auto-provision on first login.
- **OIDC / SSO** — generic OpenID Connect with **state + nonce + PKCE + JWKS id_token verification**. Set `OPENID_PROVIDER_URL` (issuer) + `OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET`, or the Keycloak / `GOOGLE_*` / `MICROSOFT_*` built-ins. Callback = `{PUBLIC_URL}/api/auth/oidc/{provider}/callback`. Account-merge by email via `OAUTH_MERGE_ACCOUNTS_BY_EMAIL`.
- **Access gate + branch binding** — `OAUTH_ALLOWED_ROLES` rejects users lacking an allowed role; an LDAP-group / OIDC-group → `site_code` map (edit at `/ui/auth-admin`) auto-binds federated users to their pharmacy branch (Shop-Counter mode).
- **Config** — env (see `.env.example`) merged with a live super-admin editor at **`/ui/auth-admin`**. **Secrets stay in env only** (`LDAP_APP_PASSWORD`, `*_CLIENT_SECRET`) — never written to the DB. The login page shows enabled methods from `GET /api/auth/methods`.

No DB migration needed (`auth_provider`/`external_id`/`site_code` columns already exist; only a transient `dash_oauth_flow` table is auto-created). Adding LDAP pulls a new dep (`ldap3`) → deploy must **rebuild the image**, not hot-copy.

---

## Install (fresh box / AWS) — two commands, zero errors

A brand-new install is **two commands**. The database schema seeds itself on the
first boot, so there is **no manual SQL step** and **no "relation … does not exist"
wall** on a fresh DB.

```bash
git clone git@github.com:raahulgupta07/rahulai-pharma.git
cd rahulai-pharma
cp .env.example .env          # fill 3 values: OPENROUTER_API_KEY, DB_PASS, SUPER_ADMIN_PASS
docker compose up -d --build  # builds images, auto-seeds the DB, starts everything
# wait ~1–2 min, then check: curl http://127.0.0.1:8011/api/health  →  {"status":"ok"}
```

That's it. Open `http://localhost:8011/ui` and log in.

### What `docker compose up` does automatically

1. Builds the app image + the AGE/pgvector Postgres image (`cp-db-age:pg18`).
2. Starts `cp-db`, waits until it's healthy.
3. Runs the **one-shot `cp-init` seeder** → loads the complete baseline schema
   (`db/baseline/schema.sql`, 208 tables) into the fresh DB.
   - **Idempotent & safe:** if the DB already has the schema it skips instantly and
     changes nothing — so re-running `docker compose up` on an existing install is a
     no-op for the DB. Restarts / upgrades are unaffected.
   - **Cold-volume safe:** the seeder waits for Postgres to actually accept queries
     (retry loop, not just `pg_isready`) so a cold AWS volume mid-`initdb` can't make
     it die `exit 2`. `dash-db` healthcheck uses `-d` + `start_period`.
4. Only after the seeder finishes does `cp-api` boot — so it never starts against an
   empty DB. First boot is clean.
5. **Writable `knowledge/` volume** (`knowledge_data:/app/knowledge`) is pre-created
   `dash`-owned in the image, so a brand-new volume inherits non-root ownership and
   the app can write decks/logos/training files. (Without this, a fresh volume mounts
   root-owned and every worker crashes `PermissionError /app/knowledge/_decks` — fixed
   2026-06-08; the `/decks` mount is also fail-soft now.)

> **Engineer checklist — that is the whole job.** Do **not** run any
> `init_fresh_db.sh`, `psql`, or migration command by hand. Just `cp .env.example .env`,
> fill the 3 values, `docker compose up -d --build`.

### Recovery — if a DB ever got half-initialized (older builds)

Only needed for a box that was started **before** the auto-seeder existed and is
stuck with a partial schema:

```bash
git pull
docker compose -f compose.yaml build dash-api
bash scripts/init_fresh_db.sh --reset   # DROPS + reloads the DB (data loss — fresh box only)
docker compose -f compose.yaml up -d
```

**Recovery — `cp-api` restart-loops with `PermissionError /app/knowledge/_decks`** (a box first
booted on a build *before* the 2026-06-08 ownership fix → its `knowledge_data` volume is
root-owned). Rebuilding does **not** re-chown an existing volume. The volume is empty (boot
never succeeded), so drop it and let the fixed image recreate it `dash`-owned:

```bash
git pull
docker compose down
docker volume rm "$(basename "$PWD")_knowledge_data"   # e.g. rahulai-pharma_knowledge_data
docker compose up -d --build
```
Or chown in place without dropping: `docker run --rm -v <project>_knowledge_data:/k alpine chown -R 999:999 /k` (999 = the `dash` uid).

**Not in git** (gitignored — supplied/regenerated on the box):
- `.env` — secrets. Copy from `.env.example`, fill real values.
- `knowledge/` — training data. A fresh box starts empty; load data + **Force Train All** in the UI to populate the agent's brain.
- `frontend/build/` — regenerated by the docker build (the Bun multi-stage stage).

> The DB **schema** is created automatically (cp-init). The DB **data** (your pharma
> stock/catalog) is not — upload it + **Force Train All** in the UI on a fresh box.

> The repo holds **all source** (frontend/src, app/, dash/, scripts/) — a clean clone builds. If a build ever fails with `ENOENT … .svelte`, a source file was left untracked: `git status` then `git add` it.

**Containers** (`cp-*`): `cp-db` · `cp-pgbouncer` · `cp-api` · `cp-redis` · `cp-caddy` · `cp-ml` · `cp-mcp` · `cp-backup`

**Ports**
| Service | Host |
|---|---|
| API (cp-api) | `127.0.0.1:8011` → 8000 |
| Caddy | `8090:80`, `8453:443` |

**Login**: `demo` / `<SUPER_ADMIN_PASS>` (super-admin). You can sign in with **either your username or your email** (2026-06-09 — the field is labelled "email"). API login response field is `token` (not `access_token`); frontend stores `localStorage.dash_token`.

Open: `http://localhost:8011/ui`

---

## Upgrade (pull latest → rebuild)

Upgrading an existing install = pull the new code, rebuild the image, recreate. The DB **migrates itself** on boot (idempotent migrations in `db/migrations/`, applied by worker rank 0) — no manual SQL. Your data, `.env`, and `knowledge/` volume are preserved.

```bash
git pull                                              # get new code
docker compose -f compose.yaml build dash-api         # rebuild image (service name = dash-api)
# clear the daemon leader so the new process re-claims it cleanly (see gotcha #9):
docker exec cp-db psql -U ai -d ai -c "DELETE FROM dash.dash_daemon_leader;"
docker compose -f compose.yaml up -d --force-recreate dash-api
# poll until ok, then hard-refresh the browser (Cmd+Shift+R):
curl http://127.0.0.1:8011/api/health                 # → {"status":"ok"}
```

**Notes**
- **Migrations are automatic + idempotent** (`ALTER … IF NOT EXISTS`), run only on worker rank 0 on boot. Nothing to run by hand. A cold-volume install seeds the full baseline instead (see Install).
- **Always rebuild — never `docker cp`.** A hot-copied bundle is wiped by any `force-recreate` (see Deploy gotcha #2).
- **New `.env` var?** `dash-api` lists env vars individually (no `env_file`), so add it to the service `environment:` block in `compose.yaml` too, or it won't reach the container.
- **Frontend changed?** `cd frontend && npm run build` first (or let the docker multi-stage build do it).
- **Rollback** = check out the previous tag/commit and rebuild the same way. DB migrations are additive (forward-only); a rollback keeps the new columns (harmless to old code).

---

## Deploy (rebuild the image — NEVER hot-copy)

**Rule (2026-06):** every code/frontend change ships by **rebuilding the image**, never `docker cp` hot-copy. Hot-copies are ephemeral (wiped on any `force-recreate`) and create stale-bundle confusion (see gotcha #6).

```bash
cd frontend && npm run build                                  # build SPA first
# rtk shell wrapper mangles docker output → bypass with env -i:
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$HOME \
  docker compose -f compose.yaml build dash-api               # service name = dash-api, container = cp-api
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$HOME \
  docker compose -f compose.yaml up -d --force-recreate dash-api
# poll /api/health until "ok", then hard-refresh browser: Cmd+Shift+R
```

A frontend-only change still busts the two fat cached layers (apt + `uv pip sync`) only if `requirements.txt`/`Dockerfile` changed; otherwise just the frontend npm build re-runs (~15 min). Backend-only edits get a fast incremental layer.

> Legacy `docker cp build/. cp-api:/app/frontend/build/` still works for a throwaway test, but is forbidden for real deploys — bake it.

### ⚠️ Critical deploy gotchas

1. **`kill -HUP 1` (graceful reload) does NOT re-run FastAPI lifespan.** It reloads workers but the `lifespan` startup events (migrations, daemons, init hooks) keep the old in-memory state. A broken lifespan import can pass an HUP reload and only crash on a **full container restart**. → After deleting/renaming backend modules, validate with a full restart, never just HUP.
2. **Hot-copies are ephemeral.** The image is stale relative to the running container. `docker compose up -d --force-recreate dash-api` **reverts to the old baked image and wipes every hot-copy**. Don't recreate until you've rebuilt the image.
3. **Make it durable** = rebuild the image from current source: `docker compose build dash-api` (bakes all changes in). Only then is `force-recreate` safe. ⚠️ **Use the compose SERVICE name `dash-api` (NOT the container name `cp-api`)** — `docker compose build cp-api` silently no-ops ("no such service") and leaves a stale image. `docker exec`/`docker inspect` use the container name `cp-api`/`cp-db`.
4. **Workers** = `cpu_count` (the `WORKERS` env isn't wired into `scripts/gunicorn_conf.py`'s default), so cold boot spins up ~14 heavy Agno workers → 30–60s to healthy. Be patient polling `/api/health`.
5. **Chat endpoint is form-encoded** (`message=...` via `-F`), not JSON.
6. **"Chat stuck at *Connecting to agent…*" is almost always a stale frontend, not a dead backend.** Reproduce the chat directly — if it streams, the browser is loading an old/hot-copied bundle; rebuild + `force-recreate` + hard-refresh fixes it. Don't debug the agent.
   ```bash
   # form-encoded, through Caddy with correct SNI (cert CN = localhost). 308 on :8090 = HTTP→HTTPS redirect.
   curl -sk -N -F "message=is paracetamol in stock at my branch?" \
     https://localhost:8453/api/projects/citypharma/chat \
     -H "Authorization: Bearer $TOKEN"
   ```
7. **Off-topic questions get an instant scope-guardrail refusal** (~1s) — e.g. asking the pharma agent "what's our MRR?" returns a polite "I'm your CityPharma Analyst…" redirect. That's correct behavior, not a bug.
8. **Answer-card action chain must forward its payload.** The chip/button row routes through `AnswerCard → ChatMessageList wrapper → +page handleAction`. The middle wrapper re-dispatches `onAction(act, payload)`; if it ever falls to a bare `onAction?.(act)` default it **drops the 2nd arg** → arg-carrying actions (esp. the **Related questions** chips, whose payload IS the question text) silently no-op. Default must be `onAction?.(act, payload)`. (Fixed 2026-06-04.)
9. **Daemon leader-election loses on plain `docker restart`.** Daemons (auto-train, etc.) run only on the DB-heartbeat leader (`dash.dash_daemon_leader`, 30s lease). A restart races the prior process's still-fresh heartbeat → **all 12 workers lose the claim → no daemon starts** (logs `daemons disabled` on every worker, no `auto_train_daemon: started`). Fix: `docker exec cp-db psql -U ai -d ai -c "DELETE FROM dash.dash_daemon_leader;"` **before** restart/force-recreate so the new lifespan claims immediately. `get_daemon_status()` reads per-worker globals, so the `/auto-train/status` endpoint usually hits a non-leader worker and shows `last_check=0.0` even when the daemon ran (cross-worker blind spot, not a failure).
10. **Per-table "trained" = a `dash_table_metadata` row, NOT `dash_training_steps`.** The 14-step per-table pipeline writes its profile to `dash_table_metadata` + Q&A to `dash_training_qa` (has `table_name`). `dash_training_steps` only holds **global** tail steps (knowledge_graph / vector_backfill / ml_auto_create, `scope='project'`) — it never gets `scope=<table_name>` rows. Keying "is this table trained?" on training-steps scope = always false. (Bit both the `/datasource` endpoint and the auto-train daemon — fixed 2026-06-05.)
11. **Aggregation endpoints that COUNT across many `dash_*` tables must use AUTOCOMMIT.** `public.dash_knowledge_triples` doesn't exist on this DB → that one counter query errors and **aborts the shared transaction**, so every later `COUNT(*)` on the same connection silently returns 0. Open the connection with `.execution_options(isolation_level="AUTOCOMMIT")` so a missing-table error can't poison the rest. (`app/datasource_api.py`.)

### Data ops — force-train & clear

```bash
# Force-train (bypass fingerprint "unchanged" skip) — trains untrained-but-unchanged tables.
# Powers the Data Source per-row "Train now" + "retrain all".
curl -X POST "http://127.0.0.1:8011/api/projects/citypharma/retrain?force=1" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"table_names":["citypharma_articles"],"force":true}'   # omit table_names = all

# Clear all project data (keeps project/agent/auth/brain). BACK UP FIRST:
docker exec cp-db pg_dump -U ai -d ai -Fc -f /tmp/backup.dump
docker exec cp-db psql -U ai -d ai -c "
  DROP TABLE IF EXISTS citypharma.<table> CASCADE;
  DELETE FROM public.dash_table_metadata    WHERE project_slug='citypharma';
  DELETE FROM public.dash_training_qa       WHERE project_slug='citypharma';
  DELETE FROM public.dash_training_runs     WHERE project_slug='citypharma';
  DELETE FROM public.dash_training_steps    WHERE project_slug='citypharma';
  DELETE FROM public.dash_knowledge_triples WHERE project_slug='citypharma';
  DELETE FROM public.dash_memories          WHERE project_slug='citypharma';"
docker exec cp-api sh -c 'rm -rf /app/knowledge/citypharma'   # disk cache
```

> **Ghost-table note:** a raw-SQL wipe like the one above drops the DB table but leaves orphaned `knowledge/{slug}/**.json`. Eval/relationship/synthesis code now filters by live DB tables (`_live_tables`) + `_purge_orphan_knowledge` runs at every retrain, so a stale JSON can't seed ghost relationships or eval cases against a dropped table. The `rm -rf` above is still the cleanest manual reset.

### Training pipeline — live log + flow

`POST /retrain` runs **Phase 1 (per-table)** → drift → SQL profile + `profile_v2` → dimension catalog → hierarchy detect → sample → deep analysis (skipped if unchanged) → Q&A gen (verified vs real DB) → persona → workflows → knowledge index → brain fill → domain knowledge → persona enrich → SQL experiments, then **Phase 2 (master tail, once)** → knowledge_graph → vector_backfill → codex_code_enrich → scope guardrail → vertical detect+apply → evals → dream-lite bootstrap. One table ≈ 110s, ≈ $0.025, 100% Q&A-verified.

**Live log in the robot panel.** Every step + every LLM call (latency/tokens/cost) lands in `dash_training_runs.logs`. The floating **CityAgent robot** (bottom-right) streams it during training:

```bash
# cursor-paged training log (since = array index already seen)
curl "http://127.0.0.1:8011/api/projects/citypharma/auto-train/log?since=0" \
  -H "Authorization: Bearer $TOK" -H "X-Scope-Id: citypharma"
# → {run_id, status, current_step, total, events:[{i, ts, msg, table}]}
```

Robot HEADER = authoritative run state (`/auto-train/status`, `is_training`); BODY = this live step log while training, chat-learning feed otherwise. The robot is the **single CLI surface** — the old bottom-of-page CONSOLE window is gone.

**Robot panel — LOG + AGENTS tabs.** While training the robot shows a live **ticker** (active agent + its action + running `N calls · $X · elapsed`), and two body tabs:
- **▸ LOG** — phase-grouped, each line `time · Agent · action`, agent colour-coded by phase (UPLOAD/PROFILE/TRAIN/Q&A/VECTORS/GRAPH); phase headers go `✓ done` (green) → `● active` (pulse).
- **AGENTS** — live roster of who's WORKING / DONE / PENDING / IDLE across the 13-agent training pipeline (Conductor · Profiler · Codex Enricher · Q&A Generator · Analyst · Persona · Workflow · Relationship Mapper · Auto-Memory · Brain Builder · Proactive Insights · Triple Extractor · Embedder), plus the idle chat team.

The log-line → agent mapping is **client-side** (`frontend/src/lib/RobotPanel.svelte` `agentFor()`) — tweak the keyword regexes freely, no rebuild needed for mapping changes.

**Pipeline strip (Data Source page).** The `UPLOAD → PROFILE → TRAIN → Q&A → VECTORS → GRAPH → LIVE` strip lights up per-step: completed stages show a **✓ green** dot + green connector, the current stage **● pulses orange**, later stages stay **○ gray** (driven by `pipeStageIdx(active_run.current_step)`). Drag-drop upload shows a real **percent** (`▸ uploading file.csv · 42%`, XHR `upload.onprogress`).

**Two signals that look contradictory but aren't:** a table flips **TRAINED ✓** ~10s in (its `dash_table_metadata` row is written right after profiling), while the run-level **"Training in progress"** keeps going ~2 min more through the Phase-2 tail (KG / vectors / evals / pharmacy pack). Different scopes — not a bug.

**Data-quality hardening (single-agent pharma data):** free-text columns (`AVG(LENGTH) > 40`, e.g. Burmese instruction fields) are excluded from the dimension catalog; lineage cols (`_period`/`_batch_id`/…) are skipped in hierarchy detection + catalog; null bytes stripped; TEXT dates in `DD/MM/YYYY HH24:MI` (`created_at`/`updated_at`) get a brain rule + a Q&A-gen directive so SQL uses `to_date(col,'DD/MM/YYYY HH24:MI')`, never `col::date` (which throws `DatetimeFieldOverflow`).

### Responsive / CSS gotcha
A later same-specificity `@media .set-shell { grid-template-columns: … }` silently **overrides** a layout-modifier class like `.set-shell-norail` (the no-rail Data Source page). Symptom: header crushed to a sliver, title wrapping char-by-char at narrow widths. Fix: re-declare the modifier (`.set-shell-norail { grid-template-columns: 1fr }`) **inside every breakpoint** that touches the base grid, or scope the media rule with `:not(.set-shell-norail)`. Also `.set-head` must `flex-wrap: wrap` so action buttons drop below the title instead of squeezing it (`.set-head-left { flex: 1 1 260px }`).

---

## Single-agent prune (2026-06)

The fork inherited ~130 routers / ~1118 routes of multi-tenant Dash machinery. Pruned the dead surface for a single-agent product:

- Deleted 9 dead-UI route dirs (agent-os, os, channels, skills, dashboard-studio, presentations, scope-picker, mcp, embed-templates).
- Deleted 39 dead/wrong-domain/over-built router files (agent-OS builder, pack marketplace, MRR/attribution/campaigns/customer-360, sim lab, slides/deck gen, HITL/governance, Obsidian steals, entity-resolution, federation, sharepoint/gdrive/onedrive).
- **Routes 1118 → 825.** Core (analyst, upload, training, brain, metrics, rules, dashboards, embeddings, drift, golden/accuracy, export) untouched.

Kept (lazy-imported into the chat hot path, zero image gain to remove): `dash/templates`, `dash/verticals`, `dash/agentic`. Full cut list + rationale in `CLAUDE.md` → "CityPharma" section.

### Admin Command Center cleanup

The Admin (`/ui/command-center`) rail was pruned to match the single-agent product:

- **Removed 6 wrong-domain tabs** — Architecture, External connectors, Data drift, Federation, Channels, MCP Servers (rail entry + content block both deleted).
- **Removed 3 dead rail groups** — Governance, Agent OS, Telemetry (their routers were deleted in the prune → every sub-tab was HTTP 404).
- **Trimmed Trust & Governance** — dropped Approvals / Dataview / Packs (backends gone). **2026-06-09: further purged mdl / diff / actions / metricflow** (0 rows, multi-tenant/dbt-semantic relics — routers + panels + orphaned agent action tools deleted). Trust & Governance now = **accuracy / golden / scope-audit** only (the 3 with live data).
- **Fixed HTTP 401** on the kept eval panels — they used bare `fetch()` with no auth; switched to `dashFetch` (auto Bearer + scope). Rule: **lib/admin panels must use `dashFetch`, never raw `fetch`.**
- **Rail trim + Overview merge (2026-06-09):** removed redundant **Users / Chat logs / API Gateway** rail items (they duplicate the standalone `/ui/users` + `/ui/gateway`). **Cockpit + Platform stats + System health + Observability folded into ONE scrollable "Overview" page** — sections KPIs → ① System Health → ② Integrations → ③ Platform Stats → ④ Observability → ⑤ Jump To, each with a **live `● live · Nms` badge** computed from its own fetch. Old deep-links `?tab=stats|health|observability` redirect to the merged page.

Top nav: **Upload → Data Source** (single-agent), reflecting the file-upload + DB-connector ingestion paths. The old standalone **Data Quality** tab was folded into the Data Source page (auto-loads on open; Cockpit per-table drill scrolls to `#dq-section`).

---

## Enterprise readiness + hardcoded config (2026-06-07)

Benchmarked against Anthropic's "Building Effective AI Agents" guide and closed the three named gaps, then deduplicated the admin surfaces and hardcoded the single-agent config.

**Observability — durable per-chat reasoning trace.** Every agent tool call (`run_sql_query`, `stock_check`, `find_substitutes`, `search_all`, …) records a span in `public.dash_traces` with args + row-count + token cost (`dash/obs/trace.py`). Admin → **Observability** tab (`/ui/command-center`): kind/days filters · runs/failed/$ rollup · context-health strip · expandable trace tree (root → tool spans) · per-agent table. Kill switch `TRACING_DISABLED=1`.

**Context-exhaustion guards** (`dash/guards/context.py`, fail-open). Tool results capped at ~25k tokens with a pagination sentinel (`TOOL_RESULT_MAX_TOKENS`); stale tool-result content older than N turns is elided from history before `team.run` (`CONTEXT_EDIT_KEEP_TURNS`). Disable with `CONTEXT_GUARDS_DISABLED=1`. Health: `GET /api/admin/traces/context-health` (p50/p95 prompt tokens + caps fired).

**Golden-eval CI gate.** `python -m evals.run --golden --min-pass 90` exits non-zero below threshold; `.github/workflows/golden-gate.yml` blocks PRs to `main` (skips cleanly when no `OPENROUTER_API_KEY` secret). Surfaced as a DEPLOY GATE card in the Admin → Accuracy panel.

**Workspace ⇄ Admin dedup.** Brain lives only in **Workspace** (the agent); Embed lives only at **Integrations → Embed** (`/ui/embed` standalone manager — removed from both Workspace and Admin); Users only in **Admin** (platform accounts); connectors only via the **Data Source** page. Admin Platform group = `gateway · auth`; Workspace Sharing group = `rls · visibility`.

**Hardcoded chemist config.** This is one fixed pharma agent, so the multi-tenant config chrome was removed: the Config FEATURES-TOGGLE / APPLY-VERTICAL / RECOMMENDED tab is gone from the Workspace rail, and the Embed industry-template picker is hardcoded to **Pharmacy**. Capabilities are fixed in `dash/feature_config.py` `DEFAULT_CONFIG` (core only: SQL + charts/dashboards + document research + pharma tools; forecasting / anomaly / venture agents OFF). Pharma tools (stock_check / substitutes / indications) are always on, gated by `PHARMA_GRAPH_DISABLED` not feature_config.

> Note: `DEFAULT_CONFIG` is merged *under* any saved per-project config. To force a new default onto an already-configured project, strip the overridden keys from the saved jsonb (`UPDATE public.dash_projects SET feature_config = feature_config - 'tools' - 'agents' WHERE slug=…`) — never wipe the whole column (it holds the trained `scope` guardrail). `dash_projects` is in the **`public`** schema, not `dash`.

**Theme consistency.** The standalone **Gateway** + **Embed** pages (`/ui/gateway`, `/ui/embed`) were restyled from an old terminal-CLI look (monospace rail, `$ dash …` headers) to the app's clean **sans rail** matching Workspace + Admin (group labels, coral-wash active, muted icons; monospace kept only for code/endpoint/curl chips). The **Dashboard/Overview** page and several others were built on the legacy material `--color-*` tokens — its dark `--color-on-surface` was misused as a **border** color, producing black box outlines. Fixed app-wide: card borders → warm `--pw-border`, dark card headers → coral-on-cream, and the global scrollbar from chunky-dark `8px` to **thin `7px` warm**. Audit any off-theme surface with `grep "solid var(--color-on-surface"` — that token is dark text, never a border.

---

## Scale & capacity (100 admins + 1000 stores)

Audited and load-tested for 100 concurrent platform admins + 1000 stores hitting the API gateway / embed widget.

**What's ready (verified live):**
- **Web tier** — 100 concurrent requests → 100/100, p50 **41ms**. The chat SSE generator is synchronous → Starlette runs it in the anyio threadpool, so it never blocks the worker event loop (concurrency is not capped at worker count).
- **DB** — PgBouncer transaction mode (3000 clients → 80-conn pool, DB `max_connections=300`). App engines: `pool_size=5, max_overflow=10` per engine (raised from 2/3 on 2026-06-09 — 5 conns/worker queued under concurrent agent runs).
- **API gateway** — Redis **global** fixed-window rate limit (shared across workers) + usage metering + 3-tier store-scoping. Built for 1000 keys.
- **Embed** — per-org rate limit is now also a **Redis global** fixed-window (2026-06-09; was per-worker in-memory = 16× the cap) with in-memory fallback if Redis is down + CORS allowlist + HMAC.
- **Auth** — logout/revoke propagates across all workers within 60s (per-worker token cache re-validates the DB; fixed 2026-06-09).
- **Reliability under concurrency = 100%** — a 15-concurrent real-chat test returned 15/15 successful answers even though the LLM key threw 10 × HTTP 429; the OpenRouter pool's per-key cooldown + retry recovered every one.

**Tuning applied:** Redis `maxmemory 1gb`, multi-key pool via `OPENROUTER_API_KEYS`.

**Prod env you MUST set on AWS** (`.env`):
- `PUBLIC_URL=https://pharma.yourdomain.com` — drives every embed/SDK/gateway URL + CORS fallback. Blank → SDK snippets ship `localhost`.
- `CORS_ORIGINS=https://pharma.yourdomain.com` — lock to your domain. Blank → falls back to PUBLIC_URL; if that's also blank, allows all origins **without** credentials.
- `WORKERS` — size to RAM (~1–2/GB), NOT CPU. Default caps at `min(8, cpu_count)`; each worker loads the full agent stack (heavy). 16 needs a 16GB+ box.
- Optional: `EXPORT_ROW_CAP` (default 200000) caps per-table rows in project export (prevents OOM on huge tables); `DEBUG=1` to surface tracebacks in API error bodies (off in prod).

**The one go-live requirement — add OpenRouter keys.** On a single key, latency balloons under load (the 15-chat test ran p50 ~38s / tail 77s vs a ~20s single-chat baseline) purely from 429 throttling. Add 3–5 keys to flatten it:
```bash
# .env
OPENROUTER_API_KEYS=sk-or-...key1;sk-or-...key2;sk-or-...key3
docker compose up -d --force-recreate dash-api
```
The pool round-robins and cooldowns automatically. At true scale, prefer horizontal pods (HPA) over more workers per box.

> Compose gotcha: `dash-api` lists individual env vars (no `env_file`), so a new `.env` var only reaches the container if it's also added to the service `environment:` block (e.g. `- OPENROUTER_API_KEYS=${OPENROUTER_API_KEYS:-}`).

---

## Repo map (CityPharma-relevant)

```
app/main.py                 FastAPI entry, router registration (try/except guarded), lifespan
app/projects.py             locked chat endpoint + project guards
app/upload.py               data/doc upload + training pipeline
app/learning.py             brain / memories / training API
app/brain.py                Brain CRUD (entries, stats, log, graph, versions)
app/brain_unified.py        unified merged-Brain endpoint (GET /api/brain/unified)
app/brain_actions.py        promote / pull / resolve (POST /api/brain/*)
app/brain_merge_*.py        per-category merge engine (definitions/glossary/patterns/rules)
app/metrics_api.py          metric definitions
dash/single_agent.py        lock primitives (is_single_agent, locked_slug, product_name)
dash/team.py                agent team factory (Analyst/Engineer/Researcher/DataScientist)
dash/instructions.py        dynamic agent instructions (13 context layers)
dash/agents/                core agents
frontend/src/routes/        SvelteKit pages (base /ui)
frontend/src/routes/project/[slug]/settings/   Workspace (single rail: WORKSPACE/BRAIN/AGENTS/SHARING/INTELLIGENCE)
frontend/src/lib/brain/BrainHub.svelte   unified Brain hub — embedded in Workspace + standalone /ui/brain (single source)
frontend/src/lib/brain/{MergedList,ScopeSwitch,RailNav}.svelte   merged list · scope switch · standalone rail
frontend/src/lib/AgentFlow.svelte   animated cockpit flow diagram
frontend/src/app.css        design tokens (single Inter font family)
```

For the full inherited platform internals (training pipeline, 13 context layers, self-learning, security model, all gotchas), see **`CLAUDE.md`**.
