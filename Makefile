.PHONY: rebuild rebuild-fast rebuild-frontend rebuild-raw up down logs ps health migrate-status test-edge-cases test-rls test-rate-limit check-drift

# Issue #11: deterministic rebuild that always busts cache and recreates the
# container. Use this when frontend/src/*.svelte changes don't appear after
# a plain `docker compose up -d --build`.
rebuild:
	docker compose build --no-cache dash-api
	docker compose up -d --force-recreate dash-api
	@echo "[rebuild] dash-api image age:"
	@docker images dash:latest --format "{{.CreatedSince}}" 2>/dev/null || true

# Fast path: respects cache. Frontend src layer will still bust because of
# the COPY ordering in Dockerfile (Issue #11 fix).
rebuild-fast:
	docker compose build dash-api
	docker compose up -d --force-recreate dash-api

# Rebuild only the frontend locally (host), then docker rebuild uses prebuilt
rebuild-frontend:
	cd frontend && rm -rf .svelte-kit build && npm install && npm run build
	docker compose build dash-api
	docker compose up -d --force-recreate dash-api

# Hot-deploy frontend WITHOUT rebuilding image. ~3s vs ~3min.
# Use after svelte/CSS edits when no backend change.
# CRITICAL: `docker cp` only ADDS files — never removes. Stale bundle hashes
# pile up under _app/immutable/nodes + chunks. Browser may load any of them
# depending on cache → user sees old UI after deploy. We nuke those two dirs
# (as root, since container runs non-root) then recopy fresh + HUP.
# Preserves /assets/, /brand/, fonts (different layer, non-root-owned).
.PHONY: hot-frontend
hot-frontend:
	@echo "[hot-frontend] building..."
	@cd frontend && npm run build 2>&1 | tail -3
	@echo "[hot-frontend] nuking stale bundle dirs in container..."
	@# NOTE: busybox sh in alpine doesn't expand braces — must list each dir explicitly
	@docker exec -u 0 dash-api sh -c "rm -rf /app/frontend/build/_app/immutable/nodes && rm -rf /app/frontend/build/_app/immutable/chunks && rm -rf /app/frontend/build/_app/immutable/entry && rm -rf /app/frontend/build/_app/immutable/assets && mkdir -p /app/frontend/build/_app/immutable/nodes /app/frontend/build/_app/immutable/chunks /app/frontend/build/_app/immutable/entry /app/frontend/build/_app/immutable/assets"
	@echo "[hot-frontend] copying fresh bundle..."
	@# Use trailing /. to copy CONTENTS not directory itself (avoids merge w/ existing)
	@docker cp frontend/build/_app/immutable/nodes/.  dash-api:/app/frontend/build/_app/immutable/nodes/
	@docker cp frontend/build/_app/immutable/chunks/. dash-api:/app/frontend/build/_app/immutable/chunks/
	@docker cp frontend/build/_app/immutable/entry/.  dash-api:/app/frontend/build/_app/immutable/entry/
	@docker cp frontend/build/_app/immutable/assets/. dash-api:/app/frontend/build/_app/immutable/assets/
	@docker cp frontend/build/index.html              dash-api:/app/frontend/build/index.html
	@docker cp frontend/build/_app/version.json       dash-api:/app/frontend/build/_app/version.json
	@echo "[hot-frontend] reloading dash-api..."
	@docker exec dash-api kill -HUP 1
	@echo "[hot-frontend] done. Hard-refresh browser: Cmd+Shift+R"
	@echo "[hot-frontend] verify entry hash matches index.html:"
	@docker exec dash-api grep -oE '(app|start)\.[A-Za-z0-9_-]+\.js' /app/frontend/build/index.html | sort -u | sed 's/^/  /'

# O4: rtk shell wrapper occasionally swallows docker output silently (logs
# show empty stream → background notification fires before image is actually
# done building). Use rebuild-raw when `make rebuild` returns "success" but
# `docker images dash:latest` shows old CreatedSince. Direct path bypasses
# every shell wrapper by exec'ing through env -i with absolute docker bin.
# Required after any session where rebuild appears to no-op.
rebuild-raw:
	@echo "[rebuild-raw] bypassing rtk wrapper; using /usr/bin/env docker direct"
	/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$$HOME docker compose build --no-cache dash-api
	/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=$$HOME docker compose up -d --force-recreate dash-api
	@/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin docker images dash:latest --format "image age: {{.CreatedSince}}" 2>/dev/null || true

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f dash-api

ps:
	docker compose ps

health:
	curl -fsS http://localhost:8000/health | python3 -m json.tool || true

migrate-status:
	curl -fsS http://localhost:8000/api/admin/migrations/status | python3 -m json.tool || true

# Phase 5: E2E edge-case harness. Walks every CSV in
# tests/fixtures/edge_cases/ through upload → retrain → poll → verify.
# Skipped automatically if the container is not reachable on /api/health.
test-edge-cases:
	pytest tests/test_e2e.py -v --tb=short -m e2e

# Track C1 — RLS cross-tenant isolation. Bootstraps user-A + user-B, asserts
# user-A cannot read/chat/upload/query user-B's project (403/404 expected).
# Skipped automatically if the container is not reachable on /api/health.
test-rls:
	pytest tests/test_rls.py -v --tb=short -m rls_isolation

# Track C3 — Rate-limit middleware regression. 61st req in 60s on a rate-
# limited route must return 429 with Retry-After header.
test-rate-limit:
	pytest tests/test_rate_limit.py -v --tb=short

# Migration drift gate (CI mirror). Exits 0 if every `FROM dash.X` /
# `JOIN public.X` ref in app/ + dash/ + ml_worker/ has a matching
# CREATE TABLE in db/migrations/*.sql (or is allowlisted in
# scripts/drift_allowlist.txt). Run before any PR that adds new DB refs.
check-drift:
	@python3 scripts/check_migration_drift.py --report
