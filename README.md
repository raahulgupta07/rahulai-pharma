# CityPharma

Single-agent pharmaceutical analytics product. One hardcoded **CityPharma Analyst** agent over a locked workspace — pharma inventory / stock / sales analytics, chat-first. Forked from the Dash multi-tenant data-notebook platform and locked down to one agent, one workspace.

> Not a platform. No project creation, no agent builder, no multi-tenant switching. Chat with the analyst, train its brain by uploading data/docs, inspect the Agent Brain + Company Brain. That's it.

---

## What it is

- **One agent** — "CityPharma Analyst" (Inventory Management · Pharma Regulatory Compliance · Data Integrity). Locked to project `proj_demo_citypharma`.
- **Chat** — ask pharma questions in natural language → grounded SQL answers over your stock/sales/article data, clean Compass-style answer card (SQL hidden in a collapsed trace).
- **Agent Brain** — the full training/knowledge cockpit (datasets, knowledge, rules, training, queries, lineage, agents, evals) + animated Agent Flow diagram.
- **Upload** — drop files, Train, all brain layers update. Standalone page.
- **Company Brain** — formulas, glossary, aliases, patterns, org map (+ Ontology merged in).
- **Admin** — Command Center (super-admin only), multi-project grids hidden in single-agent mode.

Top nav: **Chat · Agent Brain · Upload · Company Brain · Admin**

---

## Pharmacy capabilities

Primary persona = **pharmacy counter staff**. The chat answers branch-scoped medicine questions:

| Staff asks | Backed by |
|---|---|
| "is X in stock at my branch?" / "do we have X" | `stock_check` (relational, branch-scoped) |
| "find `<salt>`" (e.g. paracetamol) | `stock_check` by generic_name |
| "X is out of stock — alternatives?" | **Apache AGE graph** `find_substitutes` (same-salt traversal) |
| "what do we have for `<condition>`" | `alternatives_for_indication` |
| "tell me about `<drug>` and related" | `drug_relationships` |

- **Branch scoping:** each login is bound to a branch (`dash_users.site_code`); the chat injects `## SHOP CONTEXT` so "stock" = their branch, with other branches shown as a transfer hint. 53 sites in the demo data.
- **Apache AGE knowledge graph** lives in `cp-db` (PostgreSQL 18 + AGE + pgvector). Graph `citypharma`: 4,892 Article nodes + 41,042 `SUBSTITUTE_OF` edges (same generic molecule). Stock (106k rows) stays relational, joined by `article_code`. Built by `scripts/build_pharma_graph.py`. Tools in `dash/tools/pharma_graph_tool.py` + `pharma_shop_tool.py`.
- **Output = shop medicine-finder** (name · salt · branch stock · cost · substitutes), not analyst KPI cards — for stock/find/substitute queries.

> ⚠️ **AGE durability landmine:** `age.so` lives in the cp-db container fs + `shared_preload_libraries='age'`. `docker restart cp-db` is safe; **recreating cp-db WITHOUT a baked-AGE image breaks postgres boot.** Insurance: `cp-db-age:pg18` snapshot + `db/Dockerfile.age`. Do NOT recreate cp-db until that image is wired (see CLAUDE.md → "Apache AGE graph").

**Data gaps (data, not code):** no retail/MRP price (only cost `weighted_cost_price`); pack/strength is inside the brand string; `dosage` = Burmese patient instructions; no UI yet to self-pick "my branch" (set `dash_users.site_code` per login).

### Ingestion paths
- **File upload** (`POST /api/upload`) — CSV/Excel/etc → train.
- **DB connector** (`/api/connectors/*`) — PostgreSQL / MySQL / Microsoft Fabric: test → pick tables → sync → train.
- **SFTP — not implemented** (no `paramiko`). Build if pull-from-SFTP is needed.

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

## Deploy (hot-copy workflow)

Source edits do NOT reach the running container automatically — code is baked into the image. Deploy by copying into the live container:

**Frontend**
```bash
cd frontend && npm run build
docker cp build/. cp-api:/app/frontend/build/
# hard-refresh browser: Cmd+Shift+R
```

**Backend**
```bash
docker cp app/<file>.py cp-api:/app/app/<file>.py
```

### ⚠️ Critical deploy gotchas

1. **`kill -HUP 1` (graceful reload) does NOT re-run FastAPI lifespan.** It reloads workers but the `lifespan` startup events (migrations, daemons, init hooks) keep the old in-memory state. A broken lifespan import can pass an HUP reload and only crash on a **full container restart**. → After deleting/renaming backend modules, validate with a full restart, never just HUP.
2. **Hot-copies are ephemeral.** The image is stale relative to the running container. `docker compose up -d --force-recreate cp-api` **reverts to the old baked image and wipes every hot-copy**. Don't recreate `cp-api` until you've rebuilt the image.
3. **Make it durable** = rebuild the image from current source: `docker compose build cp-api` (bakes all changes in). Only then is `force-recreate` safe.
4. **Workers** = `cpu_count` (the `WORKERS` env isn't wired into `scripts/gunicorn_conf.py`'s default), so cold boot spins up ~14 heavy Agno workers → 30–60s to healthy. Be patient polling `/api/health`.
5. **Chat endpoint is form-encoded** (`message=...` via `-F`), not JSON.

---

## Single-agent prune (2026-06)

The fork inherited ~130 routers / ~1118 routes of multi-tenant Dash machinery. Pruned the dead surface for a single-agent product:

- Deleted 9 dead-UI route dirs (agent-os, os, channels, skills, dashboard-studio, presentations, scope-picker, mcp, embed-templates).
- Deleted 39 dead/wrong-domain/over-built router files (agent-OS builder, pack marketplace, MRR/attribution/campaigns/customer-360, sim lab, slides/deck gen, HITL/governance, Obsidian steals, entity-resolution, federation, sharepoint/gdrive/onedrive).
- **Routes 1118 → 825.** Core (analyst, upload, training, brain, metrics, rules, dashboards, embeddings, drift, golden/accuracy, export) untouched.

Kept (lazy-imported into the chat hot path, zero image gain to remove): `dash/templates`, `dash/verticals`, `dash/agentic`. Full cut list + rationale in `CLAUDE.md` → "CityPharma" section.

---

## Repo map (CityPharma-relevant)

```
app/main.py                 FastAPI entry, router registration (try/except guarded), lifespan
app/projects.py             locked chat endpoint + project guards
app/upload.py               data/doc upload + training pipeline
app/learning.py             brain / memories / training API
app/brain.py                Company Brain
app/metrics_api.py          metric definitions
dash/single_agent.py        lock primitives (is_single_agent, locked_slug, product_name)
dash/team.py                agent team factory (Analyst/Engineer/Researcher/DataScientist)
dash/instructions.py        dynamic agent instructions (13 context layers)
dash/agents/                core agents
frontend/src/routes/        SvelteKit pages (base /ui)
frontend/src/lib/AgentFlow.svelte   animated cockpit flow diagram
frontend/src/app.css        design tokens (single Inter font family)
```

For the full inherited platform internals (training pipeline, 13 context layers, self-learning, security model, all gotchas), see **`CLAUDE.md`**.
