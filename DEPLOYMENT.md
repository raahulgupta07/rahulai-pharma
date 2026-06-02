# Deployment

> **Primary deploy path (recommended).** Single-host Docker Compose. For optional K8s/Helm/cloud, see `DEPLOYMENT_K8S.md` (advanced).

Dash supports four deployment modes. Pick one, follow its doc.

| Mode             | Use case                       | Doc                  |
| ---------------- | ------------------------------ | -------------------- |
| Docker Compose   | dev / single-server / demo     | this file            |
| K8S + Helm       | production multi-tenant         | `DEPLOYMENT_K8S.md`  |
| AWS managed      | AWS-native (RDS / ECS / ALB)    | `DEPLOYMENT_AWS.md`  |
| GCP managed      | GCP-native (Cloud SQL / Run)    | `DEPLOYMENT_GCP.md`  |

Compose is the default. Everything below targets it.

## Quick start

```bash
git clone <repo> && cd dash
cp .env.example .env
# edit .env — see "Required" below
docker compose up -d --build
# open https://<DOMAIN> — login admin / admin → change immediately
```

5 containers come up: `dash-app` (FastAPI, 8 workers, SSE), `dash-pgbouncer` (transaction pooler, scram-sha-256), `dash-db` (Postgres 18 + pgvector), `dash-ml` (worker, 1 GB cap), `caddy` (auto-SSL, 512 M cap).

## Environment variables

### Required (4)

| Var                   | Notes                                              |
| --------------------- | -------------------------------------------------- |
| `OPENROUTER_API_KEY`  | https://openrouter.ai/keys — $5 minimum credits    |
| `DB_PASS`             | Strong password. **Never `ai` in production.**     |
| `DOMAIN`              | Caddy auto-SSL hostname (e.g. `dash.acme.com`)     |
| `CORS_ORIGINS`        | `https://dash.acme.com` — never `*` in prod        |

### Tier 1 — model overrides (optional)

| Var               | Default                                  | Use case                                                                  |
| ----------------- | ---------------------------------------- | ------------------------------------------------------------------------- |
| `CHAT_MODEL`      | `google/gemini-3-flash-preview`          | chat / SQL / vision / Q&A / dashboard                                     |
| `DEEP_MODEL`      | `openai/gpt-5.4-mini`                    | deep analysis · relationships · domain · auto-evolve · Excel · ML predict |
| `LITE_MODEL`      | `google/gemini-3.1-flash-lite-preview`   | scoring · routing · extraction · meta-learning                            |
| `EMBEDDING_MODEL` | `google/gemini-embedding-2-preview`      | Cascade: Gemini → OpenAI large → small → Cohere v4                        |

### Tier 2 — connector OAuth (optional)

| Var                                                       | Connector       |
| --------------------------------------------------------- | --------------- |
| `MS_CLIENT_ID` / `MS_CLIENT_SECRET` / `MS_TENANT_ID`      | SharePoint, OneDrive (Entra ID) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`               | Google Drive    |

### Tier 3 — self-learning external data (optional)

| Var                  | Source                                |
| -------------------- | ------------------------------------- |
| `TAVILY_API_KEY`     | Web search (researcher tier)          |
| `BRAVE_API_KEY`      | Web search (fallback)                 |
| `PERPLEXITY_API_KEY` | Web search (deep)                     |
| `FRED_API_KEY`       | US macro economic time-series         |

### Tier 4 — notifications (optional)

| Var                       | Notes                                                    |
| ------------------------- | -------------------------------------------------------- |
| `SLACK_LEARNING_WEBHOOK`  | Daily self-learning digest → Slack channel               |
| `SLACK_TOKEN`             | Slack as conversational interface (full bot, see docs)   |
| `SLACK_SIGNING_SECRET`    | Slack event verification                                 |

See `docs/SLACK_CONNECT.md`.

### Tier 5 — scheduler control (optional)

| Var                              | Default | Notes                                                       |
| -------------------------------- | ------- | ----------------------------------------------------------- |
| `LEARNING_SCHEDULER_DISABLED`    | `false` | Set `true` on K8S API pods (CronJob handles cycle, not pod) |
| `LEARNING_DAILY_TIME_UTC`        | `04:00` | Compose-only scheduler clock                                |
| `LEARNING_DAILY_COST_CAP_USD`    | `1.0`   | Per-project daily cap (override per project in DB)          |

### Tier 6 — branding (optional)

| Var             | Default              | Notes                                                  |
| --------------- | -------------------- | ------------------------------------------------------ |
| `BRANDING_DIR`  | `branding/default`   | White-label logo / theme / company.json per tenant     |

See `branding/default/README.md`.

### Auth / DB / app

| Var                    | Default          | Notes                                                |
| ---------------------- | ---------------- | ---------------------------------------------------- |
| `SUPER_ADMIN`          | `admin`          | First-boot admin username                            |
| `SUPER_ADMIN_PASS`     | (== username)    | **Set before first boot.** Change after.             |
| `DB_HOST`              | `dash-pgbouncer` | **Never set in `.env`.** Compose injects it.         |
| `DB_PORT`              | `5432`           | Inside Docker. Host map `5433:5432` is debug only.   |
| `DB_USER` / `DB_DATABASE` | `ai` / `ai`   |                                                      |
| `WORKERS`              | `4`              | Uvicorn workers                                      |
| `RATE_LIMIT`           | `500/min`        | Per-IP rate limit                                    |
| `AGNO_DEBUG`           | `False`          | **Keep False in production.**                        |
| `KEYCLOAK_URL` / `_REALM` / `_CLIENT_ID` / `_CLIENT_SECRET` | — | Optional OIDC SSO                |

## Health check

```bash
curl https://<DOMAIN>/health
# {"status":"ok","db":"connected","ml_retrain":{"last_run":"...","last_error":null}}
```

Returns 200 only when DB pool is healthy and ML retrain scheduler has logged a recent `last_run`.

## Migrations

Six SQL migrations apply in order. Idempotent (`IF NOT EXISTS` / `IF EXISTS` guards on every DDL).

```bash
for f in db/migrations/*.sql; do
  docker exec -i dash-db psql -U ai -d ai < "$f"
done
```

| File                              | Adds                                                       |
| --------------------------------- | ---------------------------------------------------------- |
| `001_provider_layer.sql`          | `dash_data_sources.config / scope`, connector tables       |
| `002_self_learning.sql`           | hypothesis, verification, consolidation, agent_iq          |
| `003_cost_ceiling.sql`            | `dash_projects.daily_cost_cap_usd`, learning cost log      |
| `004_hypothesis_lineage.sql`      | `parent_hypothesis` (diff-as-experiment)                   |
| `005_digests.sql`                 | daily learning digest log                                  |
| `006_brain_unique_index.sql`      | unique indexes on `dash_company_brain` (no duplicates)     |

## Backup

```bash
# DB
docker exec dash-db pg_dump -U ai -d ai > backup_$(date +%Y%m%d).sql

# Knowledge files (per-project vector cache, dimensions, doc structure)
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/knowledge_$(date +%Y%m%d).tar.gz /data
```

## Restore

```bash
cat backup_YYYYMMDD.sql | docker exec -i dash-db psql -U ai -d ai
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/knowledge_YYYYMMDD.tar.gz -C /
```

## Safe upgrade (preserves data)

```bash
docker exec dash-db pg_dump -U ai -d ai > backup_$(date +%Y%m%d).sql
git pull origin main
docker compose build dash-app dash-ml
docker compose up -d dash-app dash-ml
curl https://<DOMAIN>/health
```

`docker compose down` is safe. **`docker compose down -v` deletes all data.**

## Production hardening

- `CORS_ORIGINS` set to actual origin (never `*`)
- `AGNO_DEBUG=False`
- scram-sha-256 (default v1.1+)
- `RATE_LIMIT` tuned to traffic
- `BRANDING_DIR` per tenant if multi-tenant
- TLS via Caddy auto-cert OR external load balancer (disable Caddy block in `compose.yaml`)
- `SUPER_ADMIN_PASS` rotated post first-boot

## Multi-tenant

White-label per customer via `BRANDING_DIR`. See `branding/default/README.md`.

For full tenant isolation, deploy one Dash stack per customer (separate DB / namespace). Single-stack multi-project (one DB, many `dash_projects.slug`) covers most cases.

## Frontend rebuild

```bash
cd frontend && rm -rf .svelte-kit build node_modules
npm install && npm run build
cd .. && docker compose up -d --build
```

If new CSS doesn't appear: prune builder cache, `docker image rm dash:latest`, `docker compose build --no-cache`, hard-refresh browser. See `CLAUDE.md` → "Build & Deploy Troubleshooting".

## Common mistakes

| Mistake                                      | Effect                                              |
| -------------------------------------------- | --------------------------------------------------- |
| Setting `DB_HOST=localhost` in `.env`        | App can't reach pgbouncer. **Leave `DB_HOST` out.** |
| Setting `DB_PORT=5433`                       | Wrong inside Docker. **Leave it 5432 or unset.**    |
| `docker compose down -v`                     | **DELETES DB + knowledge + SSL.** Use without `-v`. |
| `CORS_ORIGINS=*` in prod                     | Anyone embeds your auth in their site.              |

## Ports 80/443 already taken

Comment out the Caddy block in `compose.yaml` and front Dash with your existing nginx:

```nginx
server {
    listen 80;
    server_name dash.acme.com;
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
        client_max_body_size 250M;
    }
}
```

```bash
sudo certbot --nginx -d dash.acme.com
sudo systemctl reload nginx
docker compose up -d --build
```

## Troubleshooting

| Symptom                       | Check                                                                |
| ----------------------------- | -------------------------------------------------------------------- |
| App won't start               | `docker compose logs dash-app` — usually missing `OPENROUTER_API_KEY` |
| DB connect refused            | `DB_PASS` mismatch app vs db; `DB_HOST` must be `dash-pgbouncer`     |
| Training fails                | OpenRouter credits, API key valid, `dash_training_runs.error`         |
| Stale frontend                | See "Frontend rebuild" above                                          |
| ML worker idle                | `docker compose logs dash-ml`; check `dash_ml_jobs.status='pending'`  |
| Connection exhaustion         | Verify `NullPool`, pgbouncer up, `DB_HOST=dash-pgbouncer` not `dash-db` |

## Pre-deploy checklist

- [ ] `pg_dump` backup
- [ ] `tar czf` knowledge backup
- [ ] `/health` 200 OK
- [ ] Pull latest, build api+ml only
- [ ] Restart api+ml only (not db)
- [ ] Re-verify `/health`
- [ ] Login + smoke-test a project chat
- [ ] Verify ML retrain scheduler `last_run` recent
- [ ] Verify learning daily cycle ran (Settings → SELF-LEARN)

## Related

- `ARCHITECTURE.md` — system design, 13 context layers, agent topology
- `SECURITY.md` — secrets, RBAC, threat model
- `UPGRADE.md` — version migration steps
- `OPERATIONS.md` — runbooks, incident response
- `DEPLOYMENT_K8S.md` / `_AWS.md` / `_GCP.md` — managed-cloud variants
- `docs/IMPROVE_DASH.md` — make Dash smarter for your data
- `docs/SLACK_CONNECT.md` — Slack notifications + bot
- `docs/TEST_QUESTIONS.md` — validation prompts after training
