# CityPharma — Expert Memory

> Single-agent pharma-analytics product, **forked + locked + pruned from "Dash"** (multi-tenant self-learning data notebook). ONE hardcoded **CityPharma Analyst** over ONE locked workspace `proj_demo_citypharma`. No project creation, no agent builder, no tenant switching. The authoritative running log is `CLAUDE.md` (7665 lines) — this file is the distilled map.

---

## 1. What it is

Pharmacy analytics + counter-staff medicine finder. Primary persona = **pharmacy counter staff** (check stock, find by salt, find substitutes for THEIR branch), secondary = analyst (totals/trends). Stack:

```
FastAPI (gunicorn, app.main:app) → PgBouncer (txn pooling, NullPool) → PostgreSQL 18 (pgvector)
                                                                        ↑
ML worker (cp-ml) ──────────────────────────────────────────────────────┘
Frontend: SvelteKit (Svelte 5) baked into image, served from /app/frontend/build
```

Agent framework = **Agno**. LLMs via **OpenRouter** (Gemini 3 flash / GPT-5.4-mini / Gemini 3.1 flash-lite tiers).

---

## 2. Single-agent lock (READ FIRST)

- `.env`: `SINGLE_AGENT_MODE=1`, `LOCKED_PROJECT_SLUG=proj_demo_citypharma`, `PRODUCT_NAME=CityPharma`.
- `dash/single_agent.py` — `is_single_agent()`, `locked_slug()`, `product_name()`, `resolve_slug()`, `guard_no_project_management()`.
- `GET /api/flags` (public, in `SKIP_PATHS`) → `single_agent`/`locked_slug`/`product_name`. Frontend `+layout.svelte` fetches on mount → 4-item nav + redirects `root/home/projects/chat` → locked chat.
- `app/projects.py`: create/delete/duplicate → `guard_no_project_management`; `list_projects` scoped to locked slug.
- Slug stays `proj_demo_citypharma` internally (brand = `PRODUCT_NAME`) — renaming would touch 111k rows + FKs.

---

## 3. Infra (cp-* containers, NOT dash-*)

- Containers: `cp-db cp-pgbouncer cp-api cp-redis cp-caddy cp-ml cp-mcp cp-backup`.
- Compose **service names kept** (`dash-db`, `dash-api` = internal DNS); only `container_name` renamed `cp-*` (avoids clash with a live Dash on same host).
- Ports: cp-api `127.0.0.1:8011:8000`; caddy `8090:80` / `8453:443`. (Separate citymart-geo on :8000.)
- Login: `demo` / `<SUPER_ADMIN_PASS>` (super-admin). Login response field is **`token`** (NOT `access_token`); frontend stores `localStorage.dash_token`.
- `DB_HOST=dash-pgbouncer` (never direct to db); all engines `poolclass=NullPool`; search_path via `SET LOCAL` in begin events (pgbouncer txn-safe); `AUTH_TYPE=scram-sha-256`.
- Workers default to `cpu_count` (~14); `WORKERS=2` in .env is **NOT** wired into `scripts/gunicorn_conf.py` default → slow 30–60s cold boot.
- Daemon dedup: only `WORKER_RANK=0` worker spawns daemons (`gunicorn_conf.py post_fork` stamps `WORKER_RANK=worker.age`). Master kill: `DAEMONS_DISABLED=1`.

---

## 4. Deploy — DURABLE = rebuild image (do NOT trust hot-copy)

**Rule (user-confirmed): durable deploy MUST rebuild the image — never `docker cp` hot-copy.**

⚠️ **compose SERVICE names are `dash-*`, container_names are `cp-*`.** Use `dash-api`/`dash-db` in every `docker compose` command — `docker compose build cp-api` silently **no-ops** ("no such service: cp-api") and leaves a stale image. `docker exec`/`docker inspect` use the **container** name (`cp-api`/`cp-db`). Services: `docker compose config --services` → dash-db, dash-pgbouncer, dash-redis, dash-api, caddy, dash-backup.

```
docker compose build dash-api                                       # SERVICE name, not cp-api
docker exec cp-db psql -U ai -d ai -c "DELETE FROM dash.dash_daemon_leader;"   # clear leader (container name)
docker compose up -d --force-recreate dash-api
curl -fsS http://127.0.0.1:8011/api/health | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'],d['staleness_warning'])"   # ok False
```

Dev-only iteration (ephemeral, reverts on recreate):
```
cd frontend && npm run build && docker cp build/. cp-api:/app/frontend/build/
docker cp app/<f>.py cp-api:/app/app/<f>.py
```

**Deploy gotchas (each bit a session):**
1. `kill -HUP 1` does NOT re-run FastAPI lifespan — keeps stale in-memory state. A broken lifespan import passes HUP, crashes only on full restart. Always validate module deletes/renames with a **full restart**.
2. Hot-copies are ephemeral. `--force-recreate cp-api` reverts to baked image, **wipes all hot-copies**. Don't recreate until image rebuilt.
3. Chat endpoint is **form-encoded** (`-F "message=..."`), not JSON. `app/projects.py` reads `form.get("message")`.
4. Routers in `main.py` are try/except guarded → deleting a guarded router just unregisters it (boot-safe). **Lifespan-body imports are NOT guarded** (e.g. `init_sharepoint`) → those crash startup.
5. Daemon leader: `DELETE FROM dash.dash_daemon_leader` before restart or workers lose the claim.
6. Stale-image defense (3 layers): git post-checkout hook, CI `deploy-check.yml`, `/api/health` `staleness_warning` + `/api/admin/image/info`.

---

## 5. Code map

```
app/                 ~70 FastAPI routers (single agent.main:app)
  main.py            entry: lifespan (migrations, daemons, init hooks), CORS, auth, router registration (all try/except), SKIP_PATHS
  auth.py            login/register, OIDC/Keycloak, JWT tokens, roles (viewer/editor/admin), check_project_permission
  projects.py        locked-project CRUD + CHAT endpoint (form-encoded) — sets links_ctx ContextVars around team.run
  api_gateway.py     OpenAI-compatible /api/v1 external gateway, 3-tier store-scoped access (own/other/ref)
  overview_api.py    Dashboard cockpit aggregates: /overview, /graph, /chemist, /chemist/eval (AUTOCOMMIT, fail-soft, table auto-detect)
  wiki_api.py        Brain Wiki: /wiki index + /wiki/page (backlinked concept pages from company_brain ∪ knowledge_triples)
  brain*.py          unified Brain merge engine (brain_unified + brain_merge_* + brain_actions promote/pull/resolve + brain_versions)
  learning.py/_api   self-learning (memories, feedback, evals, patterns, quality checks, NL→SQL rules)
  datasource_api.py  /datasource agg (health rings + pipeline), auto-trains untrained tables
  + accuracy/golden/mdl/diff/scope-audit/actions/metricflow (Trust & Governance), embeddings, drift, export, evals, admin/ops

dash/                agent + LLM core
  single_agent.py    lock helpers
  team.py            Agno team factory (persona injection)
  instructions.py    dynamic system prompt assembly (13 context layers + SHOP COUNTER + PHARMA CHEMIST + KNOWLEDGE GRAPH blocks)
  llm_client.py      OpenRouter client, model tiers, multi-key pool, fallback cascade
  settings.py        shared config + training_llm_call + training_vision_call
  scope_classifier.py / scope_deriver.py / api_scope.py   query→lane routing
  agents/            analyst, conductor, router, reasoner, engineer, researcher, factory, inspector, parser, scanner,
                     vision_agent, + verticals (market_sentinel, ops_optimizer, supply_sentry, deal_analyst,
                     customer_strategist, skill_refiner)
  tools/   (~86)     see §6
  learning/ (~50)    brain, Q&A gen, dream/autosim, eval-gated self-tune, fingerprinting, training runs/logs, verified_reward
  cron/    (~24)     daemons (chemist_eval, golden_drift, dream, precompute, ab-revert, brainbench...) — leader-gated
  providers/         LLM providers + federation/
  agentic/           LIVE infra (run-context/hooks/agentic memory) — NOT the agent-OS builder, do NOT delete

frontend/src/        SvelteKit (Svelte 5), Tailwind v4
  routes/+layout.svelte         single-agent nav (4 items) + flag fetch + redirects
  routes/project/[slug]/
    overview/        Dashboard cockpit (DEFAULT landing) — KPI/health/pharma-signals/insights/chemist strips, 30s refresh
    graph/           Sigma.js v3 + graphology FA2 live graph (relational substitute web, 20,982 pairs)
    wiki/            Brain Wiki reader (142 backlinked concept pages)
    settings/        Workspace — ONE rail: WORKSPACE · BRAIN · AGENTS · SHARING · INTELLIGENCE (Data Source = no-rail #upload)
    +page.svelte     chat (Compass-style AnswerCard, collapsed SQL trace, no tabs)
  routes/command-center/        Admin (super-admin) — pruned to Overview·People·Data·System + Trust & Governance
  lib/api.ts         dashFetch (auto Bearer + X-Scope-Id) — every admin panel MUST use it, raw fetch = silent 401
  lib/AgentFlow.svelte          animated cockpit "Agent Flow" diagram
  lib/chat/{ChatMessageList,AnswerCard}.svelte

compose.yaml · db/ (migrations + Dockerfile.age) · helm/ (k8s cronjobs) · mcp_server/ · scripts/ (gunicorn_conf, build_pharma_graph)
```

**Current nav:** `Chat · Data Source · Workspace · API Gateway · Embed · Admin` (Gateway + Embed = super-admin only).

---

## 6. Pharma-specific tools (the product's heart)

All in `dash/tools/`, wired in `build.py`, gated `PHARMA_GRAPH_DISABLED`, each opens its **own read-only direct cp-db connection** (service `dash-db:5432`, bypassing pgbouncer), **auto-detects table names** via `information_schema`, returns `_source: article_code=…` for audit.

- **`pharma_shop_tool.py`** — `stock_check`: match by brand OR generic(salt), join stock for the user's branch (in-stock first) → `{brand, salt, your_stock, in_stock, cost, other_branches[]}`. Branch from `dash_users.site_code`.
- **`pharma_chemist_tool.py`** (clinical brain, "Claude-as-chemist" analog) — `drug_profile(name)`, `substitutes(name, in_stock_only)` (same-generic siblings, relational), `indication_search(symptom)` (INVERSE), `interaction_check(a,b)` (duplicate-therapy + shared side-effect).
- **`pharma_graph_tool.py`** — `find_substitutes` / `alternatives_for_indication` / `drug_relationships`. **Was Apache AGE openCypher; now REWRITTEN RELATIONAL** (same-generic / indication ILIKE) because AGE got wiped (see §8).

**Prompt modes** (`instructions.py`): **SHOP COUNTER MODE** (stock/find/substitute → medicine-finder list, suppresses analyst HEADLINE/KPI/etc) and **PHARMA CHEMIST MODE** (clinical questions → chemist tools, MANDATORY AUDIT: cite generic + article_code + stock, no fact without source row).

Other tool categories: SQL/query (run_sql_query, hybrid_search, connector_query, query_plan_extractor, sql_cost_guard), EDA/analysis (eda_tools, analysis_types ×11, proactive_insights, clv_churn, forecast), charting/deck (build, deck_edit, slide_critic/polish, codegen_pptxgenjs, deck_visual_qa, slide_icons), file gen (file_generation, venture_excel, upload_tools), knowledge (knowledge_graph, column_describer, suggest_rules, skill_refinery, apply_skill), web/ops (web_fetch, ops_tools, action_tools, recommendations).

### Data caveats (DATA, not code)
- `indication` text is **Burmese** (ဖျားနာခြင်း = fever) → English `indication_search('fever')` returns 0. Agent must search Burmese terms.
- No retail/MRP price (only `weighted_cost_price` = cost). Pack/strength buried in brand string ("ALAXAN 10's"). `dosage` col = Burmese patient instructions.
- No UI to self-pick "my branch" yet — set `dash_users.site_code` per shop login (one UPDATE) or add a picker. demo bound to `20063-CCBRBKMY` (busiest of 53 sites).

### Live data tables (re-uploaded — old names GONE)
- Catalog = `articles_list_07052026` (~4886 SKUs, 1036 generics, 101 categories). Stock = `balance_stock_07052026` (~106k rows, 11.34B MMK value, 53 sites).
- Old `citypharma_articles` / `citypharma_balance_stock` are **DELETED** — any code referencing them is broken. Tools auto-detect with `*_07052026` fallback.

---

## 7. Agent brain — how a chat becomes an answer

- **13 context layers** (matches OpenAI in-house agent): query patterns, human annotations, Codex-enriched knowledge (purpose/grain/PK/FK/usage/freshness), institutional KB (pgvector hybrid search), memory (personal/project/global), runtime schema introspect, grounded facts (LangExtract), self-correction strategies, evolved instructions, knowledge graph, Company Brain (formulas/glossary/aliases/patterns/org/thresholds/calendar).
- **Analyst self-correction loop:** SQL → execute → validate → (zero rows? investigate joins/filters | error? introspect schema | suspicious? cross-validate COUNT) → retry up to 3, save learning.
- **Modes:** FAST (direct SQL) / DEEP (think+analyze), auto-selected by complexity.
- **Self-learning pipeline** (background after each chat): quality scoring 1-5, rule suggestion, learning approval → `dash_memories`, follow-ups, source attribution. Auto-evolve instructions every 20 chats. KG triple extraction.
- **Eval-gated self-tune** (`dash/learning/`): propose → score on golden eval → keep-best → stop. Prompt-only, no weight FT.
- **Brain** = unified hub (Agent Brain + Company Brain merged). `GET /api/brain/unified?category=&scope=`. Dedup key = `category + "::" + lower(trim(name))`; status synced/conflict/agent_only/pull. Promote/pull/resolve write version-audited.
- **Chemist clinical eval:** `POST /{slug}/chemist/eval` data-grounded golden (forward drug→profile, generic, substitute≥1, inverse), persisted `dash.dash_chemist_eval`; nightly `chemist_eval_daemon`; Dashboard 🧪 card shows accuracy %. Last: 26/26 = 100%.

### Models (OpenRouter)
| Tier | Default | Use |
|---|---|---|
| CHAT_MODEL | `google/gemini-3-flash-preview` | chat, SQL, vision, Q&A, dashboard |
| DEEP_MODEL | `openai/gpt-5.4-mini` | deep analysis, relationships, auto-evolve |
| LITE_MODEL | `google/gemini-3.1-flash-lite-preview` | scoring, routing, extraction, meta-learning |
| EMBEDDING | `google/gemini-embedding-2-preview` | cascade: Gemini→OpenAI lg→OpenAI sm→Cohere |

Reranking: `cohere/rerank-4-pro` via OpenRouter.

---

## 8. Landmines (will bite)

1. **AGE graph is GONE.** cp-db was recreated without the baked-AGE image → `age` extension + `shared_preload_libraries` wiped. `age.so` lives in container fs (ephemeral); `shared_preload='age'` in PGDATA. `docker restart cp-db` = safe; **recreate/rebuild WITHOUT baked AGE = postgres fails to boot** (missing age.so). Durable fix: `docker build -f db/Dockerfile.age -t cp-db-age:pg18 .` → point cp-db `image:` at it → recreate. Until then graph tools run **relational**. **Do NOT recreate cp-db** on the plain image.
2. **Hot-copies vanish** on `--force-recreate` — see §4.
3. **`kill -HUP 1` masks deleted-module 404s** — modules `docker cp`'d but not baked disappear on restart; their try/except router imports become None → frontend 404s. Diagnose: `docker exec cp-api test -f /app/app/<router>.py` → GONE = 404 = kill the tab; PRESENT + 401 = panel using raw `fetch` not `dashFetch`.
4. **Frontend array assumption** (Issue #29): backend JSONB / LLM payloads come back as string/object in some rows, array in others. `(x||[]).join` passes on non-arrays then crashes mid-render → Svelte aborts hydration → URL/rail desync. **Always `Array.isArray(x)` guard** on backend-sourced fields (`primary_keys`, `foreign_keys`, `usage_patterns`, `alternate_tables`, `relationships`, any KG/codex field).
5. **Stale Docker bundle** (Issue #11): Dockerfile must COPY frontend src BEFORE `npm run build` so build-layer cache key tracks frontend only. `make rebuild` / `make rebuild-fast`.
6. **Migrations idempotent or die** (#27): every migration MUST use `IF NOT EXISTS` / `ON CONFLICT DO NOTHING` / `CREATE OR REPLACE`. Force-apply: `POST /api/admin/migrations/apply-pending`.
7. **Raw `fetch` in admin panels = silent 401** — swap to `dashFetch` (auto Bearer + X-Scope-Id).
8. **Daemon leader** must be cleared (`DELETE FROM dash.dash_daemon_leader`) before restart.

---

## 9. Prune history (single-agent cleanup, on `main`)

Fork inherited ~130 routers / 1118 routes of multi-tenant Dash → cut to **825 routes**. Deleted 39 router files (all main.py-only, guarded → boot-safe): agents_api, agent_os_*, channels, venture/market/campaigns/customer_360, slides/deck/canvas/graph/links/journal/dataview, hitl/approval/governance, federation/connector surface. Deleted 9 dead-UI route dirs. Cut sharepoint/gdrive/onedrive from main.py (kept DB `connectors`). Killed Workflows feature (single-agent never fires declarative chains). Killed Obsidian dead-graph stack (GraphPanel/LinkedBy/dash_links per-chat writes).

**KEPT — do NOT delete:** `dash/templates`, `dash/verticals`, `dash/agentic` (live infra, lazy-imported into chat hot path). `dream_lite` fires per-chat; `hippo_rag` referenced by `instructions.py`. Core: auth, locked projects, upload, learning, brain, metrics, rules, scores, dashboards, embeddings, training, drift, sql_validator, golden+accuracy, export, traces, core agents, AgentFlow cockpit. Skipped Phase-5 image shrink (`skills_cowork` +360MB) → image still ~3GB.

---

## 10. Security baseline

Non-root Docker, Caddy auto-SSL + security headers, CORS, `check_project_permission()` on all endpoints, parameterized SQL + read-only enforcement, LLM SQL sandbox (blocks DROP/ALTER/TRUNCATE, rollback on >50% row change), 50K msg limit, 5-min streaming timeout, rate limiter (`RATE_LIMIT`, default 500/min), prompt-injection sanitization, path-traversal protection, schema isolation, audit logging, thread-safe token cache, engine cache TTL eviction. Pin `bcrypt==4.0.1` (passlib 1.7 crashes on bcrypt≥4.1) — Compas-family rule.

---

## 11. Key dependencies (non-obvious)

`agno` (agent framework), `pymupdf4llm` (PDF→MD), `langextract` (grounded facts), `msal` + `google-auth*` (connector OAuth), `pymysql`, `python-pptx` + `Pillow` + `pdfplumber` (decks/tables), `shap` + `statsmodels` (ML), `python-calamine` + `pyarrow` + `odfpy` (data formats), frontend `sigma` + `graphology` + `graphology-layout-forceatlas2` (graph view).

---

---

## 12. Prod-breaker audit + fixes (2026-06-06)

Full-code audit for production breakers. 6 fixed + deployed (image rebuild → HEALTH_OK), 1 false alarm.

| # | Issue | Fix |
|---|---|---|
| 1 | `app/auth.py` `apigw_outlets` queried dead `citypharma_balance_stock` → API Gateway outlet picker empty | auto-detect stock table via `information_schema` (site_code+stock_qty), fallback `balance_stock_07052026`. Verified live: **53 outlets** |
| 2 | AGE durability — cp-db recreate on plain image won't boot (missing age.so) | `compose.yaml` cp-db → `image: cp-db-age:pg18` + `build: db/Dockerfile.age` (durable image already on host) |
| 3 | `scripts/build_pharma_graph.py` refs dead `citypharma_articles` | `ART` now auto-detected via information_schema in `main()` |
| 4 | Cockpit tab open fired 3 dead `/agent-template/*` 404s (`agent_templates_router=None`, API removed) | dropped the `loadAgentTpl()` trigger in settings rail onclick; panel was already gated `{#if agentTplStatus?.applied}` (null) |
| 5 + 6 | Orphan admin routes + components calling deleted backends | **DELETED**: routes `admin/{governance,agent-os,telemetry}`, lib dirs `lib/admin/{governance,agent-os,telemetry}`, components `ApprovalQueue`/`HITLConfirm`/`WorkflowRunDrawer` (0 importers); removed dead `/ui/agent-os/workflows` link in `ScheduleAnalysisModal`. Kept `admin/log-agent` (backend live) |
| 7 | 4 `skills_cowork/.../ooxml/scripts/{pack,validate}.py` "syntax errors" | **FALSE ALARM** — host py3.9 lacks `match` (3.10+); container py3.12 compiles fine, files live via `dash/tools/deck_edit.py` subprocess |

**Left as harmless dead code** (zero runtime cost, editing = needless risk): `lib/api.ts` agent-os/workflow wrapper exports (never called); `admin/log-agent` orphan route (functional).

**Audit method note:** subagents can't be spawned in this repo — the 744KB `CLAUDE.md` auto-loads into every agent's context → "Prompt is too long". Do audits inline with targeted `grep`/`find` (and `ls` returns empty here — a hook blocks it; use `find`).

---

## 13. Federated auth — LDAP + OIDC/SSO (2026-06-06, OpenWebUI-modeled)

Added optional LDAP + generic OIDC/SSO on top of local login. **OFF by default; local username/password unchanged.** Code: `app/auth_federation.py` (`fed_router`, guarded-imported in `main.py`). Branch merged to `main` (local).

- **LDAP**: `ldap3` (new dep, pinned `2.9.1`; `pyasn1` already present) bind-search-bind — app-bind → search by `LDAP_ATTRIBUTE_FOR_USERNAME` (`escape_filter_chars`) → rebind as user DN. Auto-provisions `dash_users` (bcrypt placeholder, `auth_provider='ldap'`, `external_id`=DN). TLS + `LDAP_VALIDATE_CERT`.
- **OIDC**: manual auth-code flow (NO Authlib → avoids app-wide SessionMiddleware). Discovery via `/.well-known/openid-configuration`, **state + nonce + PKCE(S256)**, transient store = NEW `public.dash_oauth_flow` (created at lifespan, 15-min GC), **id_token JWKS verify via pyjwt `PyJWKClient`** (aud/iss/nonce). Built-ins: generic / Keycloak / Google / Microsoft. Replaces old hand-rolled Keycloak (no state/PKCE/JWKS + token-in-URL leak).
- **Token handoff**: callback sets 120s JS-readable `dash_sso` cookie + `/ui/login?sso=1` (no token in URL); login page moves it to `localStorage` then clears.
- **Access + branch**: `OAUTH_ALLOWED_ROLES` gate (reject if no allowed role); `group_to_site` maps LDAP-group / OIDC group-claim → `dash_users.site_code` (auto-binds federated users to their branch for Shop-Counter mode). **No admin elevation** — `dash_users` has no role column, super-admin = fixed username.
- **Config**: ENV baseline (OpenWebUI var names) merged with ONE JSON row in `dash_admin_settings` (key=`auth_config`, global). **Secrets (`LDAP_APP_PASSWORD`, `*_CLIENT_SECRET`) ENV-ONLY — never persisted** (`/config` POST strips them). 30s cache.
- **Endpoints**: `GET /api/auth/methods` (public, SKIP_PATHS), `POST /api/auth/ldap/login` (public), `GET /api/auth/oidc/{provider}/login`+`/callback` (SKIP_PREFIX `/api/auth/oidc/`), super-admin `GET|POST /api/auth/config`.
- **Frontend**: login page renders methods from `/api/auth/methods` (replaced dead `alert()` stubs) + SSO-cookie pickup; NEW super-admin **`/ui/auth-admin`** editor (General/LDAP/OIDC + raw-JSON).
- **Verified E2E** (throwaway `osixia/openldap`): correct→token, wrong→401, unknown→401, provisioned. OIDC vs real Google discovery: 302 w/ state+nonce+PKCE, flow row persisted.
- **No DB migration** — `auth_provider`/`external_id`/`site_code` columns already existed; only `dash_oauth_flow` auto-created. **New `ldap3` dep ⇒ deploy MUST rebuild image** (not hot-copy).
- Config/turn-on reference: `.env.example` federation block + README "Authentication" section + CLAUDE.md `latest+11`.

---

*Distilled from CLAUDE.md (7665 lines) + code structure, 2026-06-06. For session-level detail and the running build log, read CLAUDE.md (CityPharma section lines 7–448 override the inherited Dash log).*
