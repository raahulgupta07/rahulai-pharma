# CityPharma

Single-agent pharmaceutical analytics product. One hardcoded **CityPharma Analyst** agent over a locked workspace — pharma inventory / stock / sales analytics, chat-first. Forked from the Dash multi-tenant data-notebook platform and locked down to one agent, one workspace.

> Not a platform. No project creation, no agent builder, no multi-tenant switching. Chat with the analyst, train its brain by uploading data/docs, manage everything from one **Workspace** (Brain included). That's it.

---

## What it is

- **One agent** — "CityPharma Analyst" (Inventory Management · Pharma Regulatory Compliance · Data Integrity). Locked to project `proj_demo_citypharma`.
- **Chat** — ask pharma questions in natural language → grounded SQL answers over your stock/sales/article data, clean Compass-style answer card (SQL hidden in a collapsed trace).
- **Workspace** — ONE settings page, ONE left rail, everything in groups: **WORKSPACE** (Cockpit · Training · Docs · Queries · Lineage · Files) · **BRAIN** · **AGENTS** (Agents · Schedules · Evals) · **SHARING** (Users · Config · Embed · Sources) · **INTELLIGENCE** (Learn · Pipeline).
- **Brain** — a group inside the Workspace rail (not a separate page or second menu). Items render as normal rail entries: Definitions · Glossary · Patterns · Rules · Graph · Schema · Org · Promote ⤴ · Pull ⤓ · Conflicts ⚠. A **SCOPE switch** [THIS AGENT · COMPANY · PERSONAL · ALL] sits as a horizontal filter atop the content. Each knowledge category renders as ONE deduplicated merged list — agent-side ∪ company-side keyed by `category::lower(name)` — with per-row status: ✓ synced · ⚠ conflict · ⤴ agent-only · ⤓ company-only. Conflicts expand to a side-by-side diff; **Promote** (agent→company) / **Pull** (company→agent) / **Resolve** act on definitions/patterns/rules (glossary/graph/schema/org are read-only). Backed by `GET /api/brain/unified` + `POST /api/brain/{promote,pull,resolve}` (version-audited). Same `BrainHub.svelte` component also serves the standalone `/ui/brain` route — single source, no drift.
- **Data Source** — redesigned single page (its own full-width no-rail view): **health rings** (tables · rows · %trained · Q&A · vectors · issues) + **live pipeline strip** + **expandable table rows**. Each row folds its own quality scorecard / columns / FK-links / 5-row preview / **Train now** inline. Backed by one call `GET /api/projects/{slug}/datasource`. Untrained tables auto-train 24/7 (daemon) or via per-row Train-now.
- **Admin** — Command Center (super-admin only), multi-project grids hidden in single-agent mode.

Top nav: **Dashboard · Chat · Data Source · Workspace · API Gateway · Embed · Admin** (Brain lives in the Workspace rail — no separate top-nav button). **Dashboard** is the default landing.

### Dashboard cockpit (`/project/{slug}/overview`)
Operator landing page — one screen, 9 fail-soft strips that reuse existing endpoints: **KPI rail** (chats 24h · catalog SKUs · tables · stock value · units), **System Health** (`/api/health` + daemons), **Data Quality**, **Pharma Signals** (live SQL: stock-outs / low-stock / value / top category), **Tool Health**, **Insights** (dismissable), **Activity** (training runs), **Live Log**, **Top Questions**, **API Gateway**. 30s auto-refresh. Plus two launchers:

- **Knowledge Graph** (`/project/{slug}/graph`) — Obsidian-style **Sigma.js** WebGL force-map. Drug **substitute web** (drugs sharing `generic_name`, ~20k pairs, derived relationally — no AGE dependency) ↔ **Brain KG** toggle. Continuous force animation (FA2 worker), node size ∝ links, color by category, hover-highlight, search→2-hop ego-graph, click→ask agent. Live mini-graph also renders inline on the Dashboard card.
- **Brain Wiki** (`/project/{slug}/wiki`) — auto-generated, backlinked concept pages (glossary / formulas / aliases / KPIs / patterns + KG entities). Search, `[[wikilinks]]`, backlinks, related siblings, "Ask agent about X". Zero LLM — pure projection of `dash_company_brain` + `dash_knowledge_triples`. The readable wiki layer over the self-learning loop.
- **🧪 Pharma Chemist** card — the clinical brain (Anthropic "Claude as a chemist" analog for pharma retail). Shows catalog size, distinct generics/categories, drugs-with-substitutes, and **clinical-field coverage %** (composition / indication / dosage / side_effect / generic / category). Plus **Clinical accuracy %** from the golden eval + a **Run eval** button. Accuracy auto-refreshes nightly (`chemist_eval` daemon, 24h, leader-gated, kill switch `CHEMIST_EVAL_DISABLED=1`).

---

## Pharmacy capabilities

Primary persona = **pharmacy counter staff**. The chat answers branch-scoped medicine questions:

| Staff asks | Backed by |
|---|---|
| "is X in stock at my branch?" / "do we have X" | `stock_check` (relational, branch-scoped) |
| "find `<salt>`" (e.g. paracetamol) | `stock_check` by generic_name |
| "X is out of stock — alternatives?" | `find_substitutes` / `substitutes` (same generic, relational) |
| "what do we have for `<condition>`" | `alternatives_for_indication` / `indication_search` |
| "tell me about `<drug>` and related" | `drug_relationships` / `drug_profile` |

- **Branch scoping:** each login is bound to a branch (`dash_users.site_code`); the chat injects `## SHOP CONTEXT` so "stock" = their branch, with other branches shown as a transfer hint. 53 sites in the demo data.
- **All drug-relationship tools are RELATIONAL** over the catalog (drugs sharing `generic_name` = substitutes; `indication` ILIKE = therapeutic alternatives). No Apache AGE dependency — survives any cp-db recreate. Tables auto-detected (data lands as `*_07052026`). Tools in `dash/tools/pharma_graph_tool.py` + `pharma_shop_tool.py`.
- **Output = shop medicine-finder** (name · salt · branch stock · cost · substitutes), not analyst KPI cards — for stock/find/substitute queries.

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

### Ingestion paths
- **File upload** (`POST /api/upload`) — CSV/Excel/etc → train.
- **DB connector** (`/api/connectors/*`) — PostgreSQL / MySQL / Microsoft Fabric: test → pick tables → sync → train.
- **SFTP — not implemented** (no `paramiko`). Build if pull-from-SFTP is needed.

---

## API Gateway — OpenAI-compatible (`/api/v1`)

External apps (e.g. a PHP storefront) call the CityPharma agent through a standard OpenAI client — swap base URL + key, no SDK changes.

- **Endpoints:** `GET /api/v1/models`, `POST /api/v1/chat/completions` (blocking + `stream:true` SSE).
- **Auth:** `Authorization: Bearer dash-key-…` service keys, minted super-admin only.
- **Admin console:** `/ui/gateway` (super-admin) — tabbed: **Overview · Chat sandbox · Keys · Outlets · Usage · Developer · Access · Rate Limit**. Mint/revoke keys, outlet picker, live rate cap, usage analytics, full developer docs inline.
- **Chat sandbox** (`TEST → Chat sandbox` tab): live-test the real `POST /api/v1/chat/completions` from the browser — paste a `dash-key-…` (auto-fills from a freshly-minted key), type a message, optional stream toggle, see the answer + latency/tokens. Preset chips exercise the 3-tier masking (own stock · other branch · catalog).
- **Standalone docs page:** `GET /api/v1/docs` — full HTML developer guide (quickstart, auth, schemas, streaming, code examples). No auth required, suitable for sharing with external dev teams.
- **Live rate limit:** Redis fixed-window, per store key, editable in UI (`API_GW_RATE_PER_MIN` fallback). 429 + `Retry-After` on exceed.

### 3-tier store-scoped access (the security boundary)

Each key is bound to a SET of outlets. The **toolset is the boundary** — store keys lose raw SQL at build time, so prompt injection can't pull cross-store quantities.

| Tier | Scope | Sees |
|---|---|---|
| 1 — owned outlets | any `site_code` in the key's SET | full data incl. stock_qty + cost |
| 2 — other stores | not in the SET | availability only — no qty, no price |
| 3 — reference | rows with no site_code (catalog, substitutes, indications) | unrestricted |

`scope_mode=store` = tiered masking · `scope_mode=global` = no mask (internal/admin only).

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

- **Admin console:** `/ui/embed` (super-admin) — **Overview · Widgets · Config · Usage · Developer**.
- **Test chat:** each widget row in the **Widgets** tab has a **▶ Test chat** button → opens `GET /api/embed/try/{embed_id}`, a live sandbox running the real widget (public embeds open directly; supports `?claim_store_id=&claim_role=` impersonation to test masking).
- **Mint widgets:** bind each embed to a store (or leave unbound for catalog-only Tier 3 access).
- **Store-scoped auth:** pass `data-user='{"store_id":"20063"}'` signed with HMAC → session enforces Tier 1/2/3 masking via the same `StoreScope` ContextVar as API Gateway.
- **Public mode:** no `store_id` → Tier 3 only (drug catalog, substitutes, indications). Staff cannot see any stock quantities without a signed store claim.

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

---

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + Agno (AgentOS), gunicorn (`app.main:app`), Python 3.12 |
| DB | PostgreSQL 18 + pgvector, via PgBouncer (transaction pooling, NullPool) |
| Cache | Redis |
| Frontend | SvelteKit 5 (Svelte 5 runes), base path `/ui`, adapter-static, Tailwind v4, ECharts |
| Proxy | Caddy |
| LLM | OpenRouter (Gemini / GPT / Claude tiers via `dash/settings.py`) |

Single font family (Inter) across the whole UI — `--pw-serif` aliases the sans stack.

---

## Single-agent lock

Controlled by `.env`:

```
SINGLE_AGENT_MODE=1
LOCKED_PROJECT_SLUG=proj_demo_citypharma
PRODUCT_NAME=CityPharma
```

- `GET /api/flags` (public, no auth) exposes `single_agent`, `locked_slug`, `product_name`.
- Frontend `+layout.svelte` fetches flags on mount → renders the 4-item nav, redirects `root/home/projects/chat` → the locked chat.
- `dash/single_agent.py` holds the lock primitives; `app/projects.py` guards create/delete/duplicate and scopes `list_projects` to the locked slug.

---

## Authentication (local + LDAP + OIDC/SSO)

Local username/password is always on. **LDAP** and **OIDC/SSO** are optional, off by default, OpenWebUI-modeled (`app/auth_federation.py`):

- **LDAP / Active Directory** — `ldap3` bind-search-bind. Set `ENABLE_LDAP=true` + `LDAP_SERVER_HOST/PORT`, `LDAP_APP_DN`, `LDAP_APP_PASSWORD`, `LDAP_SEARCH_BASE`, `LDAP_ATTRIBUTE_FOR_USERNAME` (`sAMAccountName` on AD). Users auto-provision on first login.
- **OIDC / SSO** — generic OpenID Connect with **state + nonce + PKCE + JWKS id_token verification**. Set `OPENID_PROVIDER_URL` (issuer) + `OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET`, or the Keycloak / `GOOGLE_*` / `MICROSOFT_*` built-ins. Callback = `{PUBLIC_URL}/api/auth/oidc/{provider}/callback`. Account-merge by email via `OAUTH_MERGE_ACCOUNTS_BY_EMAIL`.
- **Access gate + branch binding** — `OAUTH_ALLOWED_ROLES` rejects users lacking an allowed role; an LDAP-group / OIDC-group → `site_code` map (edit at `/ui/auth-admin`) auto-binds federated users to their pharmacy branch (Shop-Counter mode).
- **Config** — env (see `.env.example`) merged with a live super-admin editor at **`/ui/auth-admin`**. **Secrets stay in env only** (`LDAP_APP_PASSWORD`, `*_CLIENT_SECRET`) — never written to the DB. The login page shows enabled methods from `GET /api/auth/methods`.

No DB migration needed (`auth_provider`/`external_id`/`site_code` columns already exist; only a transient `dash_oauth_flow` table is auto-created). Adding LDAP pulls a new dep (`ldap3`) → deploy must **rebuild the image**, not hot-copy.

---

## Run

```bash
cp .env.example .env        # set OPENROUTER_API_KEY, DB_PASS, SUPER_ADMIN_PASS
docker compose up -d --build
```

**Containers** (`cp-*`): `cp-db` · `cp-pgbouncer` · `cp-api` · `cp-redis` · `cp-caddy` · `cp-ml` · `cp-mcp` · `cp-backup`

**Ports**
| Service | Host |
|---|---|
| API (cp-api) | `127.0.0.1:8011` → 8000 |
| Caddy | `8090:80`, `8453:443` |

**Login**: `demo` / `demo@2026` (super-admin). API login response field is `token` (not `access_token`); frontend stores `localStorage.dash_token`.

Open: `http://localhost:8011/ui`

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
     https://localhost:8453/api/projects/proj_demo_citypharma/chat \
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
curl -X POST "http://127.0.0.1:8011/api/projects/proj_demo_citypharma/retrain?force=1" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"table_names":["citypharma_articles"],"force":true}'   # omit table_names = all

# Clear all project data (keeps project/agent/auth/brain). BACK UP FIRST:
docker exec cp-db pg_dump -U ai -d ai -Fc -f /tmp/backup.dump
docker exec cp-db psql -U ai -d ai -c "
  DROP TABLE IF EXISTS proj_demo_citypharma.<table> CASCADE;
  DELETE FROM public.dash_table_metadata    WHERE project_slug='proj_demo_citypharma';
  DELETE FROM public.dash_training_qa       WHERE project_slug='proj_demo_citypharma';
  DELETE FROM public.dash_training_runs     WHERE project_slug='proj_demo_citypharma';
  DELETE FROM public.dash_training_steps    WHERE project_slug='proj_demo_citypharma';
  DELETE FROM public.dash_knowledge_triples WHERE project_slug='proj_demo_citypharma';
  DELETE FROM public.dash_memories          WHERE project_slug='proj_demo_citypharma';"
docker exec cp-api sh -c 'rm -rf /app/knowledge/proj_demo_citypharma'   # disk cache
```

> **Ghost-table note:** a raw-SQL wipe like the one above drops the DB table but leaves orphaned `knowledge/{slug}/**.json`. Eval/relationship/synthesis code now filters by live DB tables (`_live_tables`) + `_purge_orphan_knowledge` runs at every retrain, so a stale JSON can't seed ghost relationships or eval cases against a dropped table. The `rm -rf` above is still the cleanest manual reset.

### Training pipeline — live log + flow

`POST /retrain` runs **Phase 1 (per-table)** → drift → SQL profile + `profile_v2` → dimension catalog → hierarchy detect → sample → deep analysis (skipped if unchanged) → Q&A gen (verified vs real DB) → persona → workflows → knowledge index → brain fill → domain knowledge → persona enrich → SQL experiments, then **Phase 2 (master tail, once)** → knowledge_graph → vector_backfill → codex_code_enrich → scope guardrail → vertical detect+apply → evals → dream-lite bootstrap. One table ≈ 110s, ≈ $0.025, 100% Q&A-verified.

**Live log in the robot panel.** Every step + every LLM call (latency/tokens/cost) lands in `dash_training_runs.logs`. The floating **CityAgent robot** (bottom-right) streams it during training:

```bash
# cursor-paged training log (since = array index already seen)
curl "http://127.0.0.1:8011/api/projects/proj_demo_citypharma/auto-train/log?since=0" \
  -H "Authorization: Bearer $TOK" -H "X-Scope-Id: proj_demo_citypharma"
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
- **Trimmed Trust & Governance** — dropped Approvals / Dataview / Packs (backends gone); kept accuracy / golden / mdl / diff / scope-audit / actions / metricflow.
- **Fixed HTTP 401** on the 5 kept eval panels (Accuracy, Golden, Diff, Scope-audit, Metricflow) — they used bare `fetch()` with no auth; switched to `dashFetch` (auto Bearer + scope). Rule: **lib/admin panels must use `dashFetch`, never raw `fetch`.**

Top nav: **Upload → Data Source** (single-agent), reflecting the file-upload + DB-connector ingestion paths. The old standalone **Data Quality** tab was folded into the Data Source page (auto-loads on open; Cockpit per-table drill scrolls to `#dq-section`).

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
