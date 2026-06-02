# Contributing to Dash

Thanks for your interest. This guide covers dev setup, code style, how to add a connector or learning step, and PR rules.

## Dev setup

```bash
git clone <fork-url>
cd dash
cp .env.example .env
# Edit .env — minimum: OPENROUTER_API_KEY, DB_PASS, DOMAIN

./scripts/venv_setup.sh && source .venv/bin/activate
docker compose up -d --build

# Optional: seed a demo project
python scripts/generate_data.py
python scripts/load_knowledge.py
```

Login at `https://localhost` with `SUPER_ADMIN` / `SUPER_ADMIN_PASS`.

## Project structure

```
app/         FastAPI routers (main, auth, projects, upload, learning, brain, ...)
dash/        Agno team, providers, learning subsystem, tools, instructions
  agents/    Leader, Analyst, Engineer, Researcher, Data Scientist, Router, Scratchpad...
  providers/ Connector abstraction (postgres_local, fabric, sharepoint, ...)
  learning/  17 self-learning modules (curiosity, hypothesis, verifier, ...)
  tools/     Agent tools (build, introspect, visualizer, semantic_search, ...)
frontend/    SvelteKit 2 + Svelte 5 + Tailwind v4
db/          models, session, vault, migrations/
ml_worker/   Isolated ML training container
k8s/         K8S manifests
helm/        Helm chart (values-prod.yaml, learning-cronjobs.yaml, ...)
branding/    Per-tenant white-label (gitignored except default/)
knowledge/   Per-project KB / docs / structures (gitignored)
tests/       pytest suite (153 passing, 12 skipped)
docs/        Operational deep-dives
evals/       Eval harness + cases
```

## Workflow

1. Fork + branch from `main`.
2. Make change.
3. Run checks:
   ```bash
   ./scripts/format.sh     # ruff format + import sorting
   ./scripts/validate.sh   # ruff lint + mypy
   python3 -m pytest tests/ -v
   ```
4. Run evals if change touches agent behaviour:
   ```bash
   python -m evals --verbose
   ```
5. Open PR against `main`.

## Code style

- Python 3.12, ruff (line length 120), Black-formatted.
- Type hints everywhere; `mypy` with `check_untyped_defs` and `no_implicit_optional`.
- Imports at top of file. No inline imports unless circular.
- All exceptions caught at boundary; never raise from background tasks.
- Wrap LLM calls in `try/except` + cost tracking via `cost_guard`.
- CI runs format check, lint, type check, tests on every push.

## Architecture invariants

- `public` schema is read-only — never modified by agents.
- `dash` schema is owned by the Engineer agent.
- Tool functions use closure / factory pattern (`create_*_tool()` in `dash/tools/`).
- Instructions are composed dynamically in `dash/instructions.py`.
- All `create_engine()` calls use `poolclass=NullPool` — PgBouncer owns pooling.
- `DB_HOST=dash-pgbouncer` (never direct to `dash-db`).

## Adding a connector (provider)

1. Subclass `BaseProvider` in `dash/providers/<name>.py`.
2. Implement: `setup`, `teardown`, `introspect`, `emit_tools`, `health_check`, `dialect_overlay`.
3. Register: `register_provider_class("<name>", <YourClass>)` at module bottom.
4. Add row in `registry._fetch_data_source_rows` mapping if needed.
5. Optional: add a trainer step in `dash/providers/training_steps_v2.py`.
6. Add `tests/test_providers.py::test_<name>_*` covering setup/health/introspect.

## Adding a learning step

1. Create module under `dash/learning/<step>.py`.
2. Plug into `LearningCycle.run()` in `dash/learning/cycle.py`.
3. Wrap in `try/except` — never break the cycle.
4. Honour `cost_guard` budget + `verifier` 110 s timeout.
5. Add tests in `tests/test_self_learning.py`.

## Adding an eval category

1. Create case file in `evals/cases/`.
2. Register in `evals/__init__.py`.
3. `python -m evals --category <name>` to verify.

## Migration workflow

```bash
# Add db/migrations/00X_<name>.sql (idempotent, IF NOT EXISTS)
docker exec -i dash-db psql -U ai -d ai < db/migrations/00X_<name>.sql

# Verify
docker exec dash-db psql -U ai -d ai -c "\dt"
docker exec dash-db psql -U ai -d ai -c "\d <table>"

# Update CHANGELOG.md (Migration section) + UPGRADE.md (if breaking)
```

Always include a `-- ROLLBACK` comment block at the bottom of the migration file.

## Running tests

```bash
python3 -m pytest tests/ -v
# 153 passing, 12 skipped (v1.1.0)
```

Quick smoke (no DB):

```bash
python3 -m pytest tests/test_providers.py tests/test_column_classifier.py -v
```

## PR checklist

- [ ] `pytest tests/` passes (153 passing baseline)
- [ ] `./scripts/format.sh` clean
- [ ] `./scripts/validate.sh` clean (ruff + mypy)
- [ ] Evals run if agent behaviour touched
- [ ] Migration idempotent + has ROLLBACK comment
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `UPGRADE.md` noted if breaking
- [ ] No customer / tenant name hard-coded
- [ ] AST validates: `python -m py_compile <changed_file>.py`
- [ ] `branding/<tenant>/` not committed (only `branding/default/`)
- [ ] No secrets in diff (`gitleaks` check)

## PR guidelines

- Keep PRs focused — one concern per PR.
- Short description: **what** changed and **why**.
- If change affects agent behaviour, include sample queries + expected output.
- Make sure CI is green before requesting review.

## Branding

Per-tenant branding lives in `branding/<tenant>/` (gitignored).
`branding/default/` is committed and shipped with the image.
Set `BRANDING_DIR` env var to point at a host-mounted dir for tenant overrides.

## Reporting issues

GitHub Issues. Include:
- Steps to reproduce
- Expected vs. actual behaviour
- Environment (OS, Python, Docker version)

## License

By contributing, you agree contributions are licensed under [Apache License 2.0](LICENSE).

## Related docs

- `CLAUDE.md` — full project structure + agent layout
- `ARCHITECTURE.md` — system design
- `AGENTS.md` — coding rules
- `TESTING.md` — test harness + eval pipeline
- `SECURITY.md` — auth, RBAC, secrets
- `UPGRADE.md` — migration playbook
- `FUTUREPLAN.md` — what's next
