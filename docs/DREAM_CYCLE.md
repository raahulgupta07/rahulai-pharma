# Dream Reflection — Deep Dive

> Three-tier self-improving agent memory system for Dash. Inspired by
> [Letta sleep-time compute](https://www.letta.com/blog/sleep-time-compute),
> [Mem0](https://github.com/mem0ai/mem0) 4-op schema,
> [Graphiti](https://github.com/getzep/graphiti) bi-temporal,
> [ExpeL](https://arxiv.org/abs/2308.10144) vote-weighted insight pool,
> [Voyager](https://voyager.minedojo.org/) skill library,
> [Generative Agents](https://arxiv.org/abs/2304.03442) reflection tree,
> [Devin](https://www.cognition.ai/blog/introducing-devin) wiki digest,
> and [HippoRAG](https://arxiv.org/abs/2405.14831) retrieval patterns.

## Overview

Dream Reflection closes the loop between past chat failures and future-prompt
anti-pattern injection. It runs in three tiers — Tier 1 captures every turn
(rule-based, $0), Tier 2 reflects between turns when poignancy crosses a
threshold (~$0.005/cycle), Tier 3 runs a full nightly DEEP synthesis at 02:30
UTC (~$0.13/proj). Outputs land in Context Layers 14 (anti-patterns), 15
(proven skills), and 16 (precompute cache hints), feeding directly back into
the Analyst prompt on the next chat. Distinct from kpt curiosity loop
(`dash/learning/cycle.py` — Layer 5) which explores external hypotheses; Dream
reflects on internal session traces.

## Three tiers

| Tier | Trigger | Budget | LLM | Primary output |
|------|---------|--------|-----|----------------|
| 1 | per-turn (chat hot-path) | $0 | none | `dash_episode_buffer` (rolling LRU 1000/proj) |
| 2 | between-turn (poignancy threshold OR N-step OR idle debounce) | ~$0.005/cycle | LITE_MODEL | `dash_dream_personas` + precompute queue |
| 3 | nightly cron 02:30 UTC | ~$0.13/proj | DEEP_MODEL | findings, insights, anti-patterns, skills, digest, reflection tree |
| A/B | daily cron 04:00 UTC | $0 | none | revert audit (`dash_ab_revert_events`) |

## Tier 1: Per-turn poignancy capture

**Module**: `dash/learning/dream_poignancy.py` (531 LOC).
**Storage**: `dash.dash_episode_buffer` (rolling LRU, 1000 rows/project).
**Cost**: $0 (rule-based only).

After every chat turn, a hot-path hook scores the turn 1–10 on poignancy and
writes a row. Rule-based scoring signals:

| Signal | Weight |
|--------|--------|
| Tool failure (`succeeded=false`) | +3 |
| Judge score ≤2 | +2 |
| User reaction `correction` | +3 |
| User reaction `repeat` (same Q within session) | +2 |
| Long response (>2K chars) | +1 |
| New tool used (first occurrence in session) | +1 |
| User reaction `thanks` | +1 |
| Aggregate score 8+ — high-signal, prioritized for Tier 2 consumption | — |

Storage row:

```sql
INSERT INTO dash.dash_episode_buffer
  (session_id, turn_id, project_slug, user_id, poignancy,
   question, response_summary, tools_used, succeeded, judge_score,
   user_reaction, embedding)
VALUES (...);
```

Tier 2 consumes via `WHERE consumed_at IS NULL ORDER BY poignancy DESC, created_at DESC`,
then UPDATEs `consumed_at=now()` so the same episode isn't double-consumed.

**Recovery batch**: minion `poignancy_capture` runs periodically to backfill
any turns the hot-path hook missed (network blip, container restart). Reads
`agno_sessions.runs` and computes poignancy retroactively.

## Tier 2: Between-turn dream-lite

**Module**: `dash/learning/dream_lite.py` (483 LOC).
**Storage**: `dash.dash_dream_lite_runs`, `dash.dash_dream_personas`,
`dash.dash_dream_precompute_cache`.
**Cost**: ~$0.005/cycle (LITE_MODEL).

**Triggers** (any one fires):
- Poignancy threshold: latest episode score ≥8
- N-step: every 10 turns within a session
- Idle debounce: 30s of no chat activity + unconsumed episodes exist
- Manual: `POST /dream/lite/run-now`

**Pipeline** (3 steps):

1. **Episodes consume** — pull unconsumed episodes for `(project_slug, user_id)` ordered by poignancy.
2. **Persona update** — LITE_MODEL ingests recent episodes + current persona JSON, produces delta. Letta MemoryBlock analog. UPSERT into `dash_dream_personas` (UNIQUE on `project_slug, user_id`), version++.
3. **Precompute queue** — LITE_MODEL predicts top-3 most-likely next questions for the user. Each enqueued via `dash_minions` for `precompute_queries` minion to execute.

```python
# Simplified shape (see dream_lite.py for actual code)
async def run_lite_cycle(slug, user_id, session_id, trigger_reason):
    started = now()
    episodes = consume_unconsumed(slug, user_id, limit=20)
    if not episodes: return
    persona_delta = await lite_call(PERSONA_PROMPT, episodes, current_persona)
    upsert_persona(slug, user_id, merge(current_persona, persona_delta))
    next_qs = await lite_call(PRECOMPUTE_PROMPT, episodes, persona)
    for q in next_qs[:3]:
        enqueue_minion("precompute_queries", {"slug": slug, "user_id": user_id,
                                              "question": q, "session_id": session_id})
    log_run(slug, user_id, session_id, trigger_reason, episodes_consumed=len(episodes),
            precompute_queued=len(next_qs), cost_usd=...)
```

## Tier 3: Nightly reflection cycle (the big one)

**Module**: `dash/learning/dream_reflection.py` (764 LOC).
**Minion**: `reflect_sessions`.
**Cron**: K8s `dream-reflect-nightly` (02:30 UTC daily).
**Cost**: ~$0.13/proj (DEEP synthesis + LITE compaction).

9-step pipeline:

| # | Step | Input | Output | Cost |
|---|------|-------|--------|------|
| 1 | Budget check | `cost_guard.get_status(slug)` | `skipped_budget` row in `dash_dream_runs` if cap hit | $0 |
| 2 | Session pull | `agno_sessions.runs` last 50 sessions for project | session traces (Q, response, tools, judge scores) | $0 |
| 3 | LITE compaction | session traces | compact summary per session (~2K chars) | ~$0.02 |
| 4 | DEEP synthesis | compacted summaries + last week's findings + persona | `findings: [{type, content, confidence, source_session_ids}]` | ~$0.10 |
| 5 | PII scrub | findings | findings w/ PII redacted (reuses `learning/promotion.py _PII_BLOCKERS`) | $0 |
| 6 | Persist | scrubbed findings | rows in `dash_dream_findings`, dedupe via `sha256` `finding_hash` | $0 |
| 7 | Auto-promote ≥0.85 | findings | rows in `dash_dream_insights` (ExpeL pool) + `dash_anti_patterns` (Layer 14) | $0 |
| 8 | Bi-temporal reconcile | new `decision_rule` findings | UPDATE `expired_at=now()` on contradicted brain/KG rows; INSERT supersedes | $0 |
| 9 | Skill lib + reflection tree + wiki digest | findings + last 7 days of insights | `dash_skill_library` rows (Voyager), `dash_dream_reflection_tree` (depth 1+2), `dash_dream_digests` markdown | ~$0.01 |

Run lifecycle (one row in `dash_dream_runs`):

```
running → done            (happy path)
running → failed          (exception caught, error column populated)
running → skipped_budget  (cost_guard daily cap hit)
```

## Bi-temporal facts

**Module**: `dash/learning/bi_temporal.py` (494 LOC).
**Pattern**: Graphiti — never delete, only invalidate/supersede.
**Tables**: `dash_company_brain`, `dash_knowledge_triples` (cols added by migration 067).

4 added columns:

| Column | Type | Meaning |
|--------|------|---------|
| `valid_at` | TIMESTAMPTZ | When this fact became true in the modeled world |
| `invalid_at` | TIMESTAMPTZ | When this fact stopped being true (planned end) |
| `expired_at` | TIMESTAMPTZ | When this row was superseded (system event, NOT same as `invalid_at`) |
| `superseded_by` | BIGINT | FK to the row that replaces this one |

**Invalidation flow** (during nightly Tier 3 step 8):

```
1. DEEP synthesis produces a decision_rule finding:
   {old: "APAC = Asia-Pacific includes India",
    new: "APAC = Asia-Pacific excludes India (per 2026 ops realignment)"}
2. bi_temporal.invalidate_contradicted(slug, finding):
   a. SELECT existing row matching old fact
   b. UPDATE expired_at = now()
   c. INSERT new row w/ valid_at = now(), superseded_by points old → new
3. Active reads filter WHERE expired_at IS NULL → see new fact only
4. Audit reads (admin "show me what brain said last week") drop the filter
```

**Reads filter** (added to all brain/KG read paths):

```python
# Active reads (default)
SELECT ... FROM dash_company_brain
 WHERE project_slug=:s AND expired_at IS NULL

# Historical reads (admin/audit)
SELECT ... FROM dash_company_brain
 WHERE project_slug=:s
   AND valid_at <= :asof
   AND (invalid_at IS NULL OR invalid_at > :asof)
```

**Worked example**: Q3 2025 brain says "fiscal year starts Oct 1". Q1 2026
ops decides fiscal year now starts Jan 1. Tier 3 detects via decision_rule
finding, invalidates the Oct 1 row, inserts Jan 1 row. Chat asking "when does
fiscal year start?" today sees Jan 1. Admin time-travel query for `asof
2025-12-15` still sees Oct 1.

## Skill library

**Module**: `dash/learning/skill_library.py` (586 LOC).
**Pattern**: Voyager — parameterized SQL recipes w/ NL embedding.
**Table**: `dash_skill_library` (migration 067).

**Promotion criteria** (from Tier 3 step 9):

- Query pattern used ≥3 times in the lookback window
- `avg_judge_score >= 4` (out of 5)
- Not already a skill (UNIQUE on `project_slug, name`)

**Parameterization**: LITE_MODEL converts literal SQL into template:

```sql
-- before (raw query_pattern)
SELECT region, SUM(revenue) FROM sales
 WHERE year=2025 AND region IN ('APAC','EMEA')
 GROUP BY region

-- after (skill_template)
SELECT {dim}, SUM({metric}) FROM {table}
 WHERE {date_col} BETWEEN {start_date} AND {end_date}
   AND {dim} IN {dim_values}
 GROUP BY {dim}
```

`params_schema` JSONB documents each param's type, required, and default.

**Retrieval at chat-time** (Context Layer 15, see `dash/instructions.py:1677+`):

```sql
SELECT name, description, success_count, avg_judge_score
  FROM dash_skill_library
 WHERE project_slug=:s AND status='active'
 ORDER BY success_count DESC, avg_judge_score DESC NULLS LAST
 LIMIT 5
```

Top 5 injected as "PROVEN SKILLS (reusable SQL recipes — call these when
applicable)". HippoRAG-style PPR retrieval planned (currently cosine sim on
description embedding).

## ExpeL insight pool

**Pattern**: vote-weighted, bounded growth, drift-resistant.
**Table**: `dash_dream_insights` (migration 066, cap 200/project).
**Endpoints**: `/dream/insights/*`.

5 ops (Mem0 4-op + UPVOTE/DOWNVOTE separation):

| Op | Endpoint | Effect |
|----|----------|--------|
| ADD | auto-promote (Tier 3 step 7) | INSERT w/ `insight_hash=sha256(insight_text)`, ON CONFLICT DO NOTHING |
| UPDATE | re-occurrence in next cycle | `last_used_at=now()`, no dedup churn |
| UPVOTE | `POST /dream/insights/{iid}/upvote` | `upvotes++` |
| DOWNVOTE | `POST /dream/insights/{iid}/downvote` | `downvotes++`; if `downvotes >= upvotes+5` → `status='deprecated'` |
| EDIT | (admin) | edit + reset votes (planned) |

**Drift resistance**: stale insight that no longer fires gets no upvotes →
naturally falls below newer insights in `ORDER BY upvotes DESC` retrieval.
Explicit deprecation only fires when user actively downvotes.

**Cap eviction**: when project exceeds 200 active insights, oldest by
`last_used_at` is auto-deprecated (planned background sweep, not yet shipped).

## Reflection tree

**Module**: `dash/learning/reflection_tree.py` (378 LOC).
**Pattern**: Generative Agents (Park et al. 2023) — insights cite insights.
**Table**: `dash_dream_reflection_tree` (migration 067).

Depth model:

- **depth=0** — leaf, raw finding from Tier 3 step 6
- **depth=1** — abstraction over 3–5 sibling leaves ("users frequently ask about Q4 revenue but Analyst defaults to Q3" — cites 4 depth-0 findings)
- **depth=2** — abstraction over depth-1 nodes ("temporal-context confusion is the dominant failure class" — cites 3 depth-1 nodes)

`evidence_finding_ids BIGINT[]` and `evidence_session_ids TEXT[]` provide
backtrace from any node to source sessions. Recursion cap = depth 2 (no
depth-3+ to avoid infinite abstraction).

## Wiki digest

**Module**: `dash/learning/dream_digest.py` (550 LOC).
**Pattern**: Devin wiki — markdown per nightly run.
**Table**: `dash_dream_digests` (UNIQUE on `project_slug, digest_date`).

Markdown shape:

```markdown
# Dream Digest — 2026-05-17 — proj_fund3

## TL;DR
- Promoted 3 anti-patterns, 2 skills, 1 user persona update
- Invalidated 1 brain fact (APAC region definition)
- 7 new findings pending review

## Anti-patterns added
1. ...

## Skills promoted
1. ...

## Bi-temporal changes
1. ...

## Reflection tree (depth 1+2)
1. ...

## Pending review
- 7 findings under threshold (0.85), awaiting human approve/reject
```

Optional disk artifact at `knowledge/{slug}/dreams/{date}.md`. Slack
webhook (`SLACK_LEARNING_WEBHOOK` env) posts the TL;DR section.

## Anti-patterns + A/B revert

**Promotion flow** (Tier 3 step 7, `dash_anti_patterns` table from migration 066):

```
finding (type=anti_pattern, confidence>=0.85)
   ↓
INSERT INTO dash_anti_patterns
  (project_slug, pattern, why_bad, example_failure, confidence,
   source_dream_finding_id, score_before=<current_judge_avg>)
   ↓
observation_window_started_at = now()  (set by 7d cron sweep)
   ↓
[7 days pass — Analyst sees Layer 14 inject, presumably avoids pattern]
   ↓
ab_revert_check minion (daily 04:00 UTC):
  - SELECT anti_patterns WHERE observation_window_started_at < now() - 7d
                          AND observation_completed_at IS NULL
  - score_after = current judge avg
  - IF score_after < score_before - delta AND sample_size >= N:
      UPDATE status='reverted', reverted_at=now()
      INSERT dash_ab_revert_events (..., decision='reverted', reason='...')
    ELSE:
      UPDATE observation_completed_at=now()
      INSERT dash_ab_revert_events (..., decision='kept')
```

**Layer 14 inject** (Analyst prompt, top-10 active anti-patterns by
`confidence DESC, hit_count DESC`):

```
ANTI-PATTERNS (learned from past failures — DO NOT repeat):
- Pattern1 (why: ...)
- Pattern2 (why: ...)
...
```

Cap 1500 chars to preserve context budget.

## Context Layers added (14, 15, 16)

All injected into `_build_chat_context(slug, ...)` in `dash/instructions.py`.

| # | Layer | Source SQL | Token budget | Truncation order |
|---|-------|-----------|--------------|------------------|
| 14 | Anti-patterns | `SELECT pattern, why_bad FROM dash_anti_patterns WHERE status='active' AND (project_slug=:s OR project_slug IS NULL) ORDER BY confidence DESC, hit_count DESC LIMIT 10` | 1500 chars | dropped first when total ctx > 20K |
| 15 | Skill library | `SELECT name, description, success_count, avg_judge_score FROM dash_skill_library WHERE project_slug=:s AND status='active' ORDER BY success_count DESC, avg_judge_score DESC NULLS LAST LIMIT 5` | ~1000 chars | dropped second |
| 16 | Precompute hints | `SELECT question_text, result_summary FROM dash_dream_precompute_cache WHERE project_slug=:s AND user_id=:u AND ttl_until > now() ORDER BY last_hit_at DESC NULLS LAST LIMIT 3` | ~800 chars | dropped third |

## Endpoints (30+)

All under `/api/projects/{slug}/dream/*` except `cycle-all` (super-admin, no slug).

| Method · Path | Role | Purpose |
|---|---|---|
| `POST /{slug}/dream/run-now` | editor | Manual trigger nightly Tier 3 |
| `POST /dream/cycle-all` | super-admin | Run Tier 3 for all projects (cron target) |
| `GET /{slug}/dream/runs` | viewer | List recent runs (default 30d) |
| `GET /{slug}/dream/findings?status=` | viewer | List findings (pending/approved/rejected/auto_promoted) |
| `POST /{slug}/dream/findings/{fid}/approve` | editor | Approve pending finding |
| `POST /{slug}/dream/findings/{fid}/reject` | editor | Reject pending finding |
| `GET /{slug}/dream/insights` | viewer | List active insights (ExpeL pool) |
| `POST /{slug}/dream/insights/{iid}/upvote` | editor | Upvote insight |
| `POST /{slug}/dream/insights/{iid}/downvote` | editor | Downvote; auto-deprecates at +5 |
| `GET /{slug}/dream/anti-patterns` | viewer | List active anti-patterns |
| `POST /{slug}/dream/anti-patterns/{apid}/revert` | admin | Manual revert |
| `GET /{slug}/dream/digests` | viewer | List recent digests (last 14d) |
| `GET /{slug}/dream/digests/{did}` | viewer | Get one digest's markdown |
| `GET /{slug}/dream/skill-library` | viewer | List proven skills |
| `POST /{slug}/dream/skill-library/{sid}/deprecate` | editor | Deprecate skill |
| `GET /{slug}/dream/personas` | viewer | List per-user persona blocks |
| `GET /{slug}/dream/bi-temporal/invalidated` | viewer | List invalidated facts (audit trail) |
| `POST /{slug}/dream/precompute/run` | editor | Force precompute minion run |
| `GET /{slug}/dream/precompute/cache` | viewer | Inspect cache contents |
| `POST /{slug}/dream/ab-revert/run` | admin | Force A/B revert cycle |
| `GET /{slug}/dream/ab-revert/runs` | viewer | List recent revert cycles |
| `GET /{slug}/dream/ab-revert/events` | viewer | Per-item revert audit |
| `GET /{slug}/dream/reflection-tree` | viewer | Walk depth-1+depth-2 reflections |
| `GET /{slug}/dream/episode-buffer` | viewer | Inspect Tier 1 episode buffer |
| `POST /{slug}/dream/lite/run-now` | editor | Manual Tier 2 trigger |
| `GET /{slug}/dream/lite/runs` | viewer | List Tier 2 run log |

## Minion kinds (5)

| Kind | Trigger | Payload | Handler module |
|------|---------|---------|----------------|
| `reflect_sessions` | nightly cron 02:30 UTC | `{slug, window_hours=24}` | `dream_reflection.py` |
| `dream_lite` | between-turn (debounced) | `{slug, user_id, session_id, trigger_reason}` | `dream_lite.py` |
| `poignancy_capture` | recovery batch | `{slug, session_id, turn_id}` | `dream_poignancy.py` |
| `precompute_queries` | hourly cron :15 | `{slug, user_id, question, session_id}` | `dream_precompute.py` |
| `ab_revert_check` | daily cron 04:00 UTC | `{slug}` | `dream_ab_revert.py` |

All claim work via the standard `dash_minions` atomic-UPDATE pattern (see
`dash/templates/runner.py` for the original pattern).

## Cost model

Per-tier breakdown:

| Tier | Per unit | Per project per day | Per 50 projects per month |
|------|----------|---------------------|---------------------------|
| 1 poignancy | $0 | $0 | $0 |
| 2 dream-lite | ~$0.005/cycle | ~$0.05 (10 cycles/active user) | ~$75 |
| 3 nightly DEEP | ~$0.13/proj | $0.13 | ~$195 |
| 4 precompute | ~$0/SQL | $0 | $0 |
| 5 A/B revert | $0 | $0 | $0 |

**Total per project**: ~$0.18/day, ~$5.40/month (assuming 1 active user, 10
dream-lite cycles/day, 1 nightly DEEP). Cost guard cap is per-project daily,
so runaway is bounded.

## Safety + governance

- **PII scrub**: Tier 3 step 5 reuses `learning/promotion.py._PII_BLOCKERS` regex
  set (69 patterns) + LLM gate. Same defenses as kpt promotion.
- **Budget caps**: `cost_guard.get_status(slug)` consulted at Tier 3 step 1.
  Default $1/proj/day, $5 central; configurable via `dash_cost_caps` table.
- **A/B revert**: every promoted item observed 7 days; auto-revert on judge
  regression. Audit row in `dash_ab_revert_events`.
- **Provenance**: every finding has `source_dream_run_id`, every insight has
  `source_dream_run_id`, every anti-pattern has `source_dream_finding_id`,
  every skill has `source_query_pattern_id` + `source_dream_run_id`.
- **Idempotency**: findings dedup via `sha256(...)` `finding_hash`. Insights
  dedup via `insight_hash`. Skills UNIQUE on `(project_slug, name)`. Precompute
  cache UNIQUE on `(project_slug, question_hash)`.
- **No silent promotion** below 0.85 — sits in `dash_dream_findings` `status='pending'`
  for human review.

## Operator runbook

**Enable per project**: Dream is on by default. Disable via env
`DREAM_REFLECTION_DISABLED=1` (kills all 5 minions + cron) or per-project
`feature_config.dream_reflection.enabled=false`.

**Manual triggers**:

```bash
# Force nightly Tier 3 now
curl -X POST $HOST/api/projects/$SLUG/dream/run-now \
  -H "Authorization: Bearer $T"

# Force Tier 2 dream-lite for current session
curl -X POST $HOST/api/projects/$SLUG/dream/lite/run-now \
  -H "Authorization: Bearer $T" -d '{"session_id":"sess_abc","user_id":42}'

# Force A/B revert sweep
curl -X POST $HOST/api/projects/$SLUG/dream/ab-revert/run \
  -H "Authorization: Bearer $T"
```

**Inspect findings**:

```bash
# Pending findings (need human approve/reject)
curl $HOST/api/projects/$SLUG/dream/findings?status=pending

# Active anti-patterns (Layer 14 contents)
curl $HOST/api/projects/$SLUG/dream/anti-patterns

# Today's digest
curl $HOST/api/projects/$SLUG/dream/digests | jq '.[0]'
```

**Roll back**:

```bash
# Revert a specific anti-pattern
curl -X POST $HOST/api/projects/$SLUG/dream/anti-patterns/123/revert \
  -H "Authorization: Bearer $T"

# Deprecate a skill
curl -X POST $HOST/api/projects/$SLUG/dream/skill-library/45/deprecate \
  -H "Authorization: Bearer $T"

# Downvote an insight (auto-deprecates at downvotes >= upvotes+5)
curl -X POST $HOST/api/projects/$SLUG/dream/insights/789/downvote \
  -H "Authorization: Bearer $T"
```

**Reset bi-temporal state** (admin escape hatch):

```sql
-- Restore a single invalidated brain fact
UPDATE dash.dash_company_brain
   SET expired_at = NULL, superseded_by = NULL
 WHERE id = 12345;

-- Bulk restore from one nightly run
UPDATE dash.dash_company_brain
   SET expired_at = NULL, superseded_by = NULL
 WHERE expired_at >= (
   SELECT started_at FROM dash.dash_dream_runs WHERE id = :run_id
 );
```

## Cron + CronJobs

K8s CronJob templates in `helm/dash/templates/` (planned, mirror existing
`learning-cronjobs.yaml` pattern):

| Schedule | Job | Endpoint |
|----------|-----|----------|
| `30 2 * * *` (02:30 UTC daily) | `dream-reflect-nightly` | POST `/dream/cycle-all` |
| `0 4 * * *` (04:00 UTC daily) | `dream-ab-revert-daily` | POST `/dream/ab-revert/run-all` |
| `15 * * * *` (every hour :15) | `dream-precompute-hourly` | POST `/dream/precompute/cycle` |

In-process scheduler (single-pod / Compose) also wires the same schedules via
existing `dash_agent_schedules` table.

## Failure modes + mitigations

| Failure mode | Symptom | Mitigation |
|--------------|---------|------------|
| Memory poisoning (malicious chat injects fake "facts" Tier 3 promotes) | Bad insight appears in Layer 14 inject | Confidence threshold ≥0.85 + 7d A/B observation + manual downvote endpoint |
| Drift (insight that was true last quarter no longer fires) | Stale Layer 14 hint | Bi-temporal invalidation catches contradiction; A/B revert deprecates on score regression |
| Lost updates (race between Tier 3 and chat-time brain write) | Skipped invalidation | Bi-temporal UPDATE is atomic per row; worst case = 1 cycle of stale state |
| Runaway cost (Tier 3 finds 1000 findings) | Budget cap hit | `cost_guard.get_status()` at step 1 marks `skipped_budget`; finding count cap per run (default 50) |
| Schema drift (brain in `public` not `dash`) | ALTER TABLE fails | Migration 067 schema-detects via DO block, applies to detected schema or RAISE NOTICE |
| Empty episode buffer (Tier 2 fires with nothing to consume) | Wasted LITE call | Guard clause: skip if `consume_unconsumed()` returns empty |

## Research adopted

- **Letta** sleep-time compute + MemoryBlocks for persona blocks — https://www.letta.com/blog/sleep-time-compute
- **Mem0** 4-op schema (ADD / UPDATE / DELETE / SEARCH; Dash adds UPVOTE/DOWNVOTE) — https://github.com/mem0ai/mem0
- **Graphiti** bi-temporal valid_at/invalid_at pattern — https://github.com/getzep/graphiti
- **ExpeL** vote-weighted insight pool — https://arxiv.org/abs/2308.10144
- **Voyager** parameterized skill library w/ NL retrieval — https://voyager.minedojo.org/
- **Generative Agents** (Park et al. 2023) reflection tree — https://arxiv.org/abs/2304.03442
- **Devin** wiki digest markdown — https://www.cognition.ai/blog/introducing-devin
- **HippoRAG** Personalized PageRank retrieval (inspiration for Layer 15) — https://arxiv.org/abs/2405.14831

## Roadmap / not yet built

- Schema-aware skill params (currently LLM-inferred type; planned: column-type-driven)
- Cross-project skill transfer (today: per-project; planned: similar-schema sibling import)
- Proper NER-based PII strip (today: regex; planned: NER model in-process)
- Time-travel queries UI (today: SQL only; planned: Settings → BI-TEMPORAL view w/ `asof` slider)
- HippoRAG Personalized PageRank for Layer 15 (today: cosine sim; planned: PPR graph)
- MCP server expose (planned: Dream subsystem as MCP tools for external agents)
- Auto eviction cron for `dash_dream_insights` > 200 cap (today: relies on manual deprecate)

## Related docs

- `ARCHITECTURE.md` — system layers + data flow (Dream = Layer 4.5)
- `AGENTS.md` — agent inventory + minion kinds (Dream Reflection section)
- `PATTERNS.md` — R21–R25 recipes (Dream cycle, bi-temporal, skill lib, ExpeL, sleep-time)
- `CHANGELOG.md` — Unreleased / Added (Dream Reflection v1)
- `CLAUDE.md` — Session 2026-05-17 build log
- `docs/IMPROVE_DASH.md` — kpt curiosity loop (Layer 5 — sibling subsystem, not Dream)
