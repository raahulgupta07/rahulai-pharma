# TESTING.md

> Test harness, eval pipeline, smoke checks, and coverage gaps for Dash.

## Run tests

```bash
python3 -m pytest tests/ -v
# Expected: 153 passed, 12 skipped (as of v1.1.0)
```

## Layout

```
tests/
  conftest.py                 sys.path setup + lightweight stubs
  test_providers.py           ~58 tests — provider abstraction
  test_column_classifier.py   ~16 tests — classifier core
  test_classifier_step.py     classifier trainer integration
  test_self_learning.py       ~60 tests — full learning loop
  test_xmla_pull.py           ~16 tests — XMLA puller
evals/
  __main__.py                 Entry: python -m evals.<command>
  run.py                      Execute eval cases against a project
  smoke.py                    Smoke checks (auth + chat + upload)
  improve.py                  Self-improvement loop
  cases/                      YAML / JSON eval definitions
stress_test.sh                200 concurrent × 5 endpoints
docs/TEST_QUESTIONS.md        Catalogue of known-good questions
```

## Test inventory (current)

| File | Tests | Scope |
|---|---|---|
| `tests/test_providers.py` | ~58 | provider abstraction (postgres_local/remote, mysql, fabric, sharepoint, onedrive, gdrive, registry) |
| `tests/test_column_classifier.py` | ~16 | 5 detectors, 7 PII strategies, conservative-rank collisions |
| `tests/test_classifier_step.py` | small | classifier trainer pipeline integration |
| `tests/test_self_learning.py` | ~60 | curiosity, researcher, hypothesis, verifier, consolidator, forgetting, promotion, cost_guard, lineage, digest, agent_iq |
| `tests/test_xmla_pull.py` | ~16 | Power BI XMLA puller |
| `tests/conftest.py` | — | Python 3.9 compat shims (db.session, agno.tools `@tool`) |

## Mock strategy

- `db.session` stubbed in `conftest.py` so DB engine isn't required.
- `agno.tools` stubbed with lightweight `@tool` decorator (Python 3.9 compat).
- LLM calls always mocked — no real OpenRouter cost in CI.
- Real engines mocked via `MagicMock(spec=Engine)`.
- File-system writes restricted to `tmp_path` fixture.
- Web search, FRED, Slack webhooks all mocked; tests never touch real APIs.

## Eval pipeline (matches OpenAI architecture)

```
Q&A eval pairs → Generation (LLM generates SQL from question)
  → Execute both generated + expected SQL
  → DataFrame result comparison + SQL comparison
  → LLM grading → score 1-5 + match type (exact / partial / none) + reasoning
  → PASS (4-5) / PARTIAL (2-3) / FAIL (1)
```

Per-eval history saved to `dash_eval_history`. Run summaries in `dash_eval_runs`.

### Eval case format

`evals/cases/*.yaml`:

```yaml
project: fund3
question: "What was Q3 revenue by region?"
expected_sql: |
  SELECT region, SUM(amount) AS revenue
  FROM sales
  WHERE quarter = 3
  GROUP BY region;
expected_result_shape: [4, 2]
expected_columns: [region, revenue]
acceptance:
  min_score: 4
  match_type: [exact, partial]
notes: "Verifies basic GROUP BY + SUM with quarter filter."
```

### Eval commands

```bash
python -m evals.smoke                              # login + chat + upload
python -m evals.run --slug fund3                   # all evals one project
python -m evals.improve --slug fund3 --max-iterations 3
./stress_test.sh                                   # 200 × 5 endpoints
```

### Self-evaluation (in-app)

```bash
curl -X POST https://your-domain.com/api/{slug}/self-evaluate \
  -H "Authorization: Bearer $TOKEN"
```

Returns `pass_rate_delta`, `regressed_cases[]`, `recovered_cases[]`, LLM-generated `summary`.

## Smoke checks

`evals/smoke.py` performs:

1. `POST /api/auth/login` → expect 200 + token
2. `GET /api/projects` → expect non-empty list
3. `POST /api/{slug}/chat` with known-good question → expect SSE stream + final response
4. `GET /health` → expect `{"status":"ok"}`
5. `POST /api/upload` with small CSV → expect 200 + table created

Run after every deploy.

## Stress test

```bash
./stress_test.sh
```

Validates: 200 concurrent users × 5 endpoints = 1000 simultaneous.

Pass criteria:
- 100 % pass rate
- 81 stable PostgreSQL connections (no exhaustion)
- p95 latency < 3 s

If pass rate drops:
- Verify `poolclass=NullPool` on every `create_engine()`.
- Verify `DB_HOST=dash-pgbouncer`.
- Check PgBouncer `default_pool_size` and PostgreSQL `max_connections`.

## Background-agent verification

```bash
docker compose exec dash-db psql -U ai -d ai -c "
SELECT
  (SELECT MAX(created_at) FROM dash_quality_scores)        AS judge,
  (SELECT MAX(created_at) FROM dash_suggested_rules)       AS rules,
  (SELECT MAX(created_at) FROM dash_proactive_insights)    AS insights,
  (SELECT MAX(created_at) FROM dash_query_plans)           AS query_plans,
  (SELECT MAX(created_at) FROM dash_meta_learnings)        AS meta,
  (SELECT MAX(created_at) FROM dash_evolved_instructions)  AS evolved,
  (SELECT MAX(created_at) FROM dash_knowledge_triples)     AS triples,
  (SELECT MAX(created_at) FROM dash_memories WHERE source='auto_learned') AS auto_mem,
  (SELECT MAX(updated_at) FROM dash_user_preferences)      AS user_pref,
  (SELECT MAX(created_at) FROM dash_memories WHERE source='episodic') AS episodic;
"
```

All 10 columns should show timestamps within 5 min of a chat.

## Self-learning verification (v1.0+)

```bash
docker compose exec dash-db psql -U ai -d ai -c "
SELECT
  (SELECT MAX(created_at) FROM dash_self_learning_runs)     AS last_run,
  (SELECT MAX(created_at) FROM dash_curiosity_questions)    AS last_question,
  (SELECT MAX(created_at) FROM dash_hypotheses)             AS last_hypothesis,
  (SELECT MAX(created_at) FROM dash_promotion_log)          AS last_promotion,
  (SELECT MAX(created_at) FROM dash_digests)                AS last_digest;
"
```

Expect each within last 24 h on a project that's seen learning cycles.

## ML model + KG spot-checks

```bash
docker compose exec dash-db psql -U ai -d ai -c "
SELECT project_slug, model_type, status, accuracy, created_at
FROM dash_ml_models WHERE status='active'
ORDER BY created_at DESC LIMIT 20;
"

docker compose exec dash-db psql -U ai -d ai -c "
SELECT subject, predicate, object, source_type, confidence
FROM dash_knowledge_triples
WHERE project_slug = 'fund3'
ORDER BY created_at DESC LIMIT 20;
"
```

## Embedding cascade

```bash
curl https://your-domain.com/health | jq '.embeddings'
# Expect: {"primary":"google/gemini-embedding-2-preview","status":"ok","fallback_used":false}
```

If `fallback_used: true`, check OpenRouter quota for primary model.

## Frontend tests (manual smoke)

1. Login + Keycloak button.
2. Project list.
3. Chat: SSE stream → 5 tabs (ANALYSIS / DATA / QUERY / CHART / SOURCES).
4. Settings: 15 tabs render.
5. Upload: drag CSV → progress → trained.
6. Dashboard: PIN chart → side panel → SAVE → appears.
7. Brain page: 7 tabs, 51 seed entries.
8. Command Center: 9 tabs, ARCHITECTURE diagram renders.

No Playwright harness yet (TIER 7).

## What's NOT tested yet (TIER 7 backlog)

- `/api/learning/*` endpoints
- Scheduler cron + Sunday canary
- Promotion pipeline end-to-end
- Lineage walk
- Digest summary
- Cost-guard cap pre-flight
- E2E (real DB + provider + full cycle)
- Frontend Playwright

See `FUTUREPLAN.md` TIER 7 for full list.

## CI/CD recommendations

- Pre-commit hooks: `ruff` (lint + format), `mypy`, `gitleaks`, ESLint.
- Trivy CI scan on every image build.
- 24 h soak test (memory leak detection) before production cuts.
- Re-run 200-user load test after major changes (last run: v1.0.0).

## Coverage targets

- Eval pass rate: ≥ 85 % per project after training.
- Smoke: 100 % green.
- Stress test: 100 % at 200 concurrent.
- Background agents: all 11 firing within 5 min of a chat.
- Connection count: ≤ 100 stable under stress.
- Estimated line coverage: ~75 %. Run `pytest --cov=` to confirm before
  claiming production readiness.

## Pre-commit gates (planned)

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Hooks: `ruff`, `mypy`, `gitleaks`, ESLint (TS / Svelte).

## Related docs

- `AGENTS.md` — coding rules
- `ARCHITECTURE.md` — agent layout, eval pipeline detail
- `FUTUREPLAN.md` — TIER 7 testing backlog
- `docs/TEST_QUESTIONS.md` — known-good questions per project type
- `docs/IMPROVE_DASH.md` — self-improvement loop deep dive
