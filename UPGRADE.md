# UPGRADE.md

> Migration playbook for Dash. Walk through version bumps, schema changes, breaking changes.

## Pre-upgrade ritual (every upgrade)

```bash
# 1. Pin current version
git rev-parse HEAD > /tmp/dash-pre-upgrade-rev
docker images | grep dash > /tmp/dash-pre-upgrade-images

# 2. Backup DB + knowledge
docker exec dash-db pg_dump -U ai -d ai > backup_pre_upgrade_$(date +%Y%m%d_%H%M).sql
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/knowledge_pre_upgrade_$(date +%Y%m%d_%H%M).tar.gz /data

# 3. Health check baseline
curl https://your-domain.com/health | tee /tmp/dash-pre-upgrade-health.json

# 4. Snapshot row counts (for post-upgrade diff)
docker exec dash-db psql -U ai -d ai -c "
  SELECT schemaname, tablename, n_live_tup
  FROM pg_stat_user_tables
  ORDER BY schemaname, tablename;
" > /tmp/dash-pre-upgrade-counts.txt
```

## v1.0.x → v1.1.0

### Database migrations to apply

```bash
docker exec -i dash-db psql -U ai -d ai < db/migrations/001_provider_layer.sql
docker exec -i dash-db psql -U ai -d ai < db/migrations/002_self_learning.sql
docker exec -i dash-db psql -U ai -d ai < db/migrations/003_cost_ceiling.sql
docker exec -i dash-db psql -U ai -d ai < db/migrations/004_hypothesis_lineage.sql
docker exec -i dash-db psql -U ai -d ai < db/migrations/005_digests.sql
docker exec -i dash-db psql -U ai -d ai < db/migrations/006_brain_unique_index.sql
```

All migrations are idempotent (`IF NOT EXISTS`); safe to re-run.

### Brain unique-index pre-flight (migration 006)

If duplicate `(project_slug, name)` rows exist in `dash_company_brain`,
migration 006 will fail. Pre-check:

```sql
SELECT project_slug, name, COUNT(*)
FROM dash_company_brain
GROUP BY project_slug, name
HAVING COUNT(*) > 1;
```

Resolve duplicates manually (keep newest, drop older) before applying 006.

### Breaking changes

- `DEPLOYMENT_CFC.md` → renamed `DEPLOYMENT_K8S.md` (update bookmarks/scripts).
- `values-cfc.yaml` → renamed `values-prod.yaml` (update Helm install commands).
- In-process scheduler auto-disables on K8S to avoid multi-pod race. Install
  the K8S CronJobs (`k8s/70-*.yaml` / `helm/dash/templates/learning-cronjobs.yaml`)
  before upgrade, OR set `LEARNING_SCHEDULER_FORCE_INPROCESS=1` to keep
  the daemon (single-pod deploys only).

### New env vars (all optional)

```bash
# Web search providers (curiosity researcher)
TAVILY_API_KEY=
BRAVE_API_KEY=
PERPLEXITY_API_KEY=

# External data
FRED_API_KEY=
ALPHA_VANTAGE_API_KEY=

# Notifications
SLACK_LEARNING_WEBHOOK=

# Branding
BRANDING_DIR=branding   # contains <tenant>/ subdirs

# Scheduler overrides
LEARNING_SCHEDULER_DISABLED=          # opt out entirely
LEARNING_SCHEDULER_FORCE_INPROCESS=   # force daemon on K8S
```

### Rollback (v1.1.0 → v1.0.x)

```bash
# Drop new tables
docker exec -i dash-db psql -U ai -d ai <<'SQL'
DROP TABLE IF EXISTS dash_curiosity_questions CASCADE;
DROP TABLE IF EXISTS dash_hypotheses CASCADE;
DROP TABLE IF EXISTS dash_self_learning_runs CASCADE;
DROP TABLE IF EXISTS dash_promotion_log CASCADE;
DROP TABLE IF EXISTS dash_external_facts CASCADE;
DROP TABLE IF EXISTS dash_digests CASCADE;
SQL
```

Each migration file's bottom comment carries an explicit `-- ROLLBACK` block;
follow that for column-level reversal. `git checkout <pre-upgrade-rev>` and
restart containers to revert app code.

## Upgrade procedure (no schema change)

```bash
git pull origin main
docker compose build dash-app
docker compose up -d dash-app

# Verify
curl https://your-domain.com/health
docker compose logs dash-app | tail -50
```

## Upgrade procedure (with schema change)

```bash
# 1. Pre-upgrade ritual (above)
# 2. Pull
git pull origin main
# 3. Read CHANGELOG.md for the new version's "Migration" notes
# 4. Build
docker compose build dash-app dash-ml
# 5. Run migrations (auto-detected on app boot via SQLAlchemy models;
#    explicit SQL files in db/migrations/ for v1.0+ ship via psql)
docker compose up -d dash-app dash-ml
# 6. Tail logs for migration confirmation
docker compose logs -f dash-app | grep -i "migrat\|alter\|create table"
# 7. Diff row counts
docker exec dash-db psql -U ai -d ai -c "..." > /tmp/dash-post-upgrade-counts.txt
diff /tmp/dash-pre-upgrade-counts.txt /tmp/dash-post-upgrade-counts.txt
```

## Schema migration policy

- v1.0+ ships explicit SQL files in `db/migrations/00X_*.sql`.
- Older paths used eager DDL on app boot — `db/models.py` declares tables and
  `Base.metadata.create_all()` runs at startup. Idempotent for
  `CREATE TABLE IF NOT EXISTS`. Column adds use `_ensure_*_table` helpers in
  `db/session.py` with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- All new migrations must be idempotent and ship a `-- ROLLBACK` comment block.

**Adding a column to an existing table:**
1. Update `db/models.py`.
2. Add `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` to a new migration file.
3. Always use `IF NOT EXISTS` for forward + backward compat.

**Dropping a column:**
1. Add deprecation flag in code (read but don't write for one release).
2. Wait one release.
3. Drop in migration.

**Renaming a column:**
1. Add new column.
2. Backfill: `UPDATE ... SET new_col = old_col WHERE new_col IS NULL`.
3. Switch reads (one release).
4. Switch writes (one release).
5. Drop old column.

## Rollback

### Rollback code only

```bash
git checkout <pre-upgrade-rev>
docker compose build dash-app
docker compose up -d dash-app
curl https://your-domain.com/health
```

### Rollback DB

```bash
# Stop app first
docker compose stop dash-app dash-ml

# Restore
cat backup_pre_upgrade_YYYYMMDD_HHMM.sql | docker exec -i dash-db psql -U ai -d ai

# Restore knowledge
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/knowledge_pre_upgrade_YYYYMMDD_HHMM.tar.gz -C /

# Restart
docker compose up -d dash-app dash-ml
```

## Specific upgrade paths

### From 2-model → 3-model architecture

```bash
# .env additions
CHAT_MODEL=google/gemini-3-flash-preview
DEEP_MODEL=openai/gpt-5.4-mini
LITE_MODEL=google/gemini-3.1-flash-lite-preview
# Keep TRAINING_MODEL for backward compat
```

Restart `dash-app`. No DB migration needed.

### From `text-embedding-3-small` → Gemini Embedding 2

`EMBEDDING_MODEL` change auto-cascades. Old vectors stay valid (both 1536-dim
via Matryoshka). Re-embed everything:

```bash
docker compose exec dash-app python -m scripts.reindex_all_projects
```

### Adding ML Worker (new in 2026-04 release)

1. Pull latest `compose.yaml` — confirms `dash-ml` block.
2. Verify `compose.yaml` has `depends_on: [dash-pgbouncer]` for `dash-ml`.
3. `docker compose build dash-ml`
4. `docker compose up -d dash-ml`
5. Tail: `docker compose logs dash-ml | tail -20`
6. New table `dash_ml_jobs` auto-created on first poll.

## Breaking changes log

### v1.1.0 — File renames + scheduler topology
- **Action:** rename `DEPLOYMENT_CFC.md` references, switch Helm to
  `values-prod.yaml`, install K8S CronJobs.
- **Risk:** scheduler stops firing if neither CronJob nor force flag set.

### v1.0.0 — Migration files now external (db/migrations/)
- **Action:** apply 001–005 explicitly via `psql` (see above).
- **Risk:** missed migration → providers/learning subsystems missing tables.

### 2026-04 — ML Worker container
- **Action:** Add `dash-ml` to compose, `docker compose up -d --build dash-ml`.

### 2026-04 — PgBouncer mandatory
- **Action:** Verify `DB_HOST=dash-pgbouncer`, all engines use `NullPool`.

### 2026-03 — 3-model architecture
- **Action:** Add `CHAT_MODEL`, `DEEP_MODEL`, `LITE_MODEL` env vars.

### 2026-03 — `dash_company_brain` columns added (`project_slug`, `user_id`)
- **Action:** App boot auto-runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

### 2026-02 — `dash_evolved_instructions` table added
- **Action:** None — auto-created.

### 2026-01 — `_source_file` and `_source_sheet` columns
- **Action:** Re-upload old tables to gain lineage.

## Verifying an upgrade

```bash
# 1. Health
curl https://your-domain.com/health | jq

# 2. Background-agent firing
docker compose exec dash-db psql -U ai -d ai -c "
  SELECT MAX(created_at) FROM dash_quality_scores;
  SELECT MAX(created_at) FROM dash_query_plans;
  SELECT MAX(created_at) FROM dash_proactive_insights;
"
# All < 5 min after a chat
```

Manual smoke (login → chat → train → ML → connector sync) covered in
`TESTING.md`.

## Production tunables

| Symptom | Tunable | Default | Adjust |
|---------|---------|---------|--------|
| Chat latency p95 high | `WORKERS` | 4 | 8 → 16 (linear with CPU) |
| 429 from rate limiter | `RATE_LIMIT` | 500/min | 1000/min |
| OOM in app | Docker memory cap | unlimited | Set explicit cap, scale horizontally |
| ML worker stuck | row limit + SIGALRM | 100K + 5 min | `ML_ROW_LIMIT`, `ML_TIMEOUT_SEC` |
| PgBouncer pool exhausted | `default_pool_size` | 25 | 50 (verify Postgres `max_connections`) |
| Learning cycle over budget | `LEARNING_DAILY_COST_USD` | 1.0 / 5.0 | raise per-project / central |

## Related docs

- `DEPLOYMENT.md` — daily deploy + Docker Compose
- `DEPLOYMENT_K8S.md` — K8S production deploy (renamed from `_CFC.md`)
- `ARCHITECTURE.md` — system layout
- `SECURITY.md` — secrets rotation, threat model
- `CHANGELOG.md` — release-by-release changes
