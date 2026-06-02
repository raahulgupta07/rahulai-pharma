# BrainBench Regression Testing

BrainBench captures real chat sessions (Q + frozen 16-layer context + tools
called + original judge score) into `dash.dash_brainbench_corpus`. The
regression gate replays those captured sessions through the **current** team
and compares judge scores — catching silent context/skill/brain regressions
before they ship.

## When to run

Run before merging changes to any of:

* `dash/learning/` (any module — dream-cycle, curiosity, promotion, skills,
  bi-temporal, precompute, A/B revert)
* `dash/instructions.py` (any context layer 1–16)
* `dash/agents/` (Analyst / Researcher / Engineer / Data Scientist / Leader)
* `dash/tools/` (especially `context_loader`, `knowledge_graph`,
  `semantic_search`, `skill_refinery`)
* `app/brain.py`, `app/learning.py` (Brain CRUD or learning endpoints)
* Embedding model swap (`EMBEDDING_MODEL` env / cascade order)
* Any DB migration touching `dash_company_brain`, `dash_knowledge_triples`,
  `dash_skill_library`, `dash_anti_patterns`, `dash_dream_*`, `dash_memories`

Skip for: frontend-only, docs-only, infra-only, non-prompt agent additions.

## How to run locally

```bash
# Bash variant — fastest, no Python deps beyond curl + psql
export DASH_API_URL=http://localhost:8000
export DASH_API_TOKEN="$(cat ~/.dash_admin_token)"
export DASH_DB_URL='postgresql://ai:ai@localhost:5432/ai'
./scripts/brainbench_regression.sh

# Python variant — richer table, JUnit XML, Slack notification
export SLACK_WEBHOOK_URL="${SLACK_LEARNING_WEBHOOK}"
python3 scripts/brainbench_regression.py
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Pass (no regression, or empty corpus) |
| 1 | Run start failed for one or more projects (infra issue) |
| 2 | **Regression detected** — block merge |
| 3 | Config error (missing env, fatal DB) |

## One-shot admin trigger (UI / API)

Super-admin can fire the same gate from the API:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  $DASH_API_URL/api/admin/brainbench/regression-check
```

Returns the same summary as the script (per-project + totals), and a
`failed: true|false` flag using the same `regressions > 0 AND avg_Δ <
FAIL_DELTA` rule.

The Command Center → STATS tab also surfaces this as a card with a
**RUN NOW** button.

## How to capture new sessions for the corpus

The corpus is *seeded automatically* by the nightly auto-capture cron
(`auto_capture_high_quality_sessions`): every session in the last 24h with
judge ≥ 4.5 is captured, capped at 50/project/day.

Manual capture (e.g. seed a freshly-merged scenario):

```bash
# Single session
curl -X POST -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_…","tags":["smoke"]}' \
  $DASH_API_URL/api/projects/$SLUG/brainbench/capture

# Bulk: last N days, judge ≥ X
curl -X POST -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" \
  -d '{"days":7,"min_judge":4.5,"limit":50}' \
  $DASH_API_URL/api/projects/$SLUG/brainbench/capture-recent
```

Aim for ≥30 captured sessions per project before relying on the gate —
smaller corpora are too noisy to detect signal.

## Acceptable regression bands

Judge scores are 1–5 with measurement noise ~±0.3 per replay (LLM
non-determinism, time-of-day model drift, retrieval ordering jitter).

| Δ band (avg_score_delta) | Interpretation | Action |
|--------------------------|---------------|--------|
| Δ ≥ +0.3 | **Win** — measurable improvement | Ship it |
| −0.3 < Δ < +0.3 | **Noise** — within measurement band | Ship it (still log) |
| −0.5 < Δ ≤ −0.3 | **Soft regression** | Investigate, may ship w/ reason |
| Δ ≤ −0.5 with regressions>0 | **Hard regression** | **Block merge** |

The CI gate uses `FAIL_DELTA=-0.3` by default; tighten via env if you have a
larger / less noisy corpus.

Per-item `score_delta` thresholds are set inside `dash/learning/brainbench.py`
(`_TIE_BAND = 0.5`) — items within ±0.5 are classified as ties.

## Cost estimate per run

Per replay session (1 corpus row) we re-execute the full team round-trip
plus judge call. Typical:

* Team chat: ~$0.15–$0.30 (DEEP if complex, FAST if direct)
* Judge LLM call (LITE): ~$0.001
* Tool calls (SQL/KG/Brain reads): ~$0
* Brain context layer rebuild: $0 (cached)

**~$0.20 × 10 sessions = $2 per run** at the default `TOP_N=10`. Worst-case
on heavy projects with many DEEP fan-outs: ~$5.

The PR-triggered workflow is gated on a `needs-brainbench` label to keep
spend bounded; the nightly cron runs unconditionally (~$2/day).

## GitHub Actions wiring

See `.github/workflows/brainbench.yml`. Triggers:

* PR labeled `needs-brainbench`
* Daily 06:00 UTC

Posts a comment to the PR with the per-project summary table and the
overall pass/fail. Check status is required for merge on protected
branches (configure in repo settings → branch protection).

## Troubleshooting

* **Empty corpus → exit 0:** expected on fresh installs. Seed via
  `capture-recent` or wait for auto-capture cron.
* **Run stuck "running":** check `dash-api` logs for
  `brainbench background replay failed`. Increase `POLL_TIMEOUT`.
* **All scores 0 / errors:** judge LLM key / OpenRouter outage. Check
  `dash_quality_scores` for fresh rows. Gate falls through as ties.
* **High variance run-to-run:** corpus too small. Capture more sessions
  per project (target 30+).
