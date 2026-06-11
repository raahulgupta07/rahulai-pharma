# Repeated-Common-Question Answer Cache — Build Plan (no code yet)

> Goal: serve the answer to a frequently-asked question in ~30ms with **zero LLM**,
> decided by the **leader agent** (not a hardcoded threshold), and **auto-evicted the
> instant the underlying data changes**. Caches the expensive LLM step, NOT the math —
> SQL still computes truth on a cache miss.

## Locked decisions (2026-06-11)
- **Promotion** = Leader agent decides (background "Cache Curator" loop). Not rules, not admin-only.
- **Matching** = Semantic (embed question → nearest-neighbor in `dash.dash_vectors`, cosine ≥ 0.93).
- **Payload** = Full rendered AnswerCard (KPI/WHY/SQL/evidence/related), served verbatim.

## Reuse — already built (do NOT rebuild)
| Piece | File | Role |
|---|---|---|
| golden corpus | `dash/learning/golden.py` (`_golden.json`) | hand-pinned scalar tier (keep) |
| metric shortcut (serve) | `verified_reward.py:129` `try_metric_shortcut`, wired `app/projects.py:1321` | $0-LLM serve pattern to mirror |
| promote() | `golden.py` | write path (today admin-manual) |
| verified_reward | `verified_reward.py:275` `score_verified` | verify-before-promote truth check |
| golden_drift daemon | `cron/golden_drift.py` (daily 03:30) | re-run SQL → demote drifted (eviction net) |
| question logs | `dash_traces.meta`, `dash_feedback`, `dash_embed_calls.message_text` | frequency source |
| embeddings + pgvector | `dash/tools/embeddings_helper.py` `embed_batch` (text-embedding-3-small, 1536), `dash.dash_vectors` (HNSW cosine `<=>`) | semantic match |
| fingerprint | `app/upload.py:940` `compute_fingerprint`, `dash_table_metadata.fingerprint` | data-version for invalidation |
| auto_evolve | `dash/tools/auto_evolve.py` | the background "agent reviews past Q&A" pattern → **extend into Cache Curator** |
| scope/complexity gate | `dash/scope_classifier.py` | runs before shortcuts; cache check slots right after |

## Data model (the "memory")
- **New table `dash.dash_answer_cache`**: `id, project_slug, question_canonical, canonical_sql,
  answer_payload (jsonb: full AnswerCard tags + prose), source_tables text[],
  fingerprint_at_promote text, confidence numeric, hit_count int, last_served_at,
  promoted_by ('leader'|'admin'), status ('live'|'stale'|'demoted'), created_at`.
- **Question vector** → reuse `dash.dash_vectors` with `namespace='qcache'`, `source_id = cache row id`.
  No new vector store, no new model.
- Keep `_golden.json` for hand-pinned scalars; `dash_answer_cache` = the auto-learned full-answer tier.

## READ path (serve) — new check at `app/projects.py` ~line 1310 (after scope gate, before metric_shortcut)
1. Embed incoming question → NN search `dash_vectors namespace='qcache'`, cosine ≥ 0.93.
2. **Freshness gate**: load candidate's `source_tables` → compare `fingerprint_at_promote`
   vs live `dash_table_metadata.fingerprint`. Mismatch → mark `stale`, SKIP (fall through).
3. HIT (similar + fresh) → return `answer_payload` verbatim, `hit_count++`, emit synthetic
   trace events for UI polish (same trick metric_shortcut uses). $0 LLM, ~30ms.
4. MISS → existing chain: `try_metric_shortcut` → `try_stock_shortcut` → agent.
- Kill switch env `ANSWER_CACHE_DISABLED=1` (mirror `METRIC_SHORTCUT_DISABLED`).

## WRITE path (learn) — Cache Curator daemon (extend `auto_evolve` pattern, leader-gated, nightly)
1. Cluster logged questions (`dash_traces`/`dash_feedback`) by meaning → frequency rank.
2. Take top-N repeats NOT already in `dash_answer_cache`.
3. **Leader agent judges each**: "stable & safe to pin?"
   - YES = aggregates/counts/category-splits that change only on data reload.
   - NO  = volatile/personal/relative ("today", "@customer", "latest", "now"). Never cache.
   This judgment REPLACES the hardcoded ≥0.95 threshold = "agent decides, not hardcode".
4. For YES: run canonical SQL once → capture full answer + SQL + touched tables →
   `score_verified` must pass → `promote` into `dash_answer_cache` + embed question into qcache +
   stamp each source table's current fingerprint + TTL backstop (e.g. 7d).

## Invalidation (analytics safety — non-negotiable)
- Per-row `source_tables[]` + `fingerprint_at_promote`; serve compares vs live fingerprint.
- Mismatch = cache miss (fall to agent), never a wrong/stale number → stale answers structurally
  un-servable. `golden_drift` daemon = daily second net (re-run SQL, demote drift).
- TTL backstop even when fingerprint unchanged.

## Guardrails
- Never cache volatile/personal/relative questions (leader's refusal job).
- Verify-before-promote (`score_verified` pass) + confidence floor.
- Fingerprint gate on every serve. TTL backstop. Kill switch env.
- Worst case = a miss that falls back to the agent. Never a wrong answer.

## Phased rollout (each phase shippable alone)
- **P0** ✅ DONE (2026-06-11) — schema-drift guard on `metric_shortcut`. metric_shortcut re-runs SQL live (numbers already fresh); the real risk is *schema* moving under a pinned SQL (silent wrong-semantic). NEW `dash/learning/schema_guard.py` (`sql_source_tables` FROM/JOIN regex + `live_schema_hash` from `dash_table_metadata.col_hash`); `golden.promote` stamps `source_tables`+`schema_hash` at pin time; `try_metric_shortcut` skips serve when stored hash ≠ live (kill switch `METRIC_SHORTCUT_SCHEMA_GUARD=0`). Legacy/auto-QA pairs (no hash) unaffected. E2E-verified: fresh→serves, drifted→skips→agent, restored→serves. Fail-open (guard error never blocks).
- **P1** ✅ DONE (2026-06-11) — `dash.dash_answer_cache` table (migration 185) + semantic NN serve. NEW `dash/learning/answer_cache.py`: `promote_answer` (stamps source_tables+schema_hash via P0 guard, stores full AnswerCard content as `answer_payload.content`, embeds question → `dash.dash_vectors namespace='qcache'`), `try_answer_cache` (existence check → `embed_text` → cosine NN `<=> CAST(:v AS vector)` → sim ≥ `ANSWER_CACHE_MIN_SIM` (0.93) → schema-drift gate → serve verbatim, hit_count++), `demote_answer`. Wired at `app/projects.py:~1310` BEFORE metric_shortcut (full answer wins over scalar), `await`ed, stream + non-stream, synthetic "Served from answer cache" trace, bg judge+verified-reward. Kill: `ANSWER_CACHE_DISABLED=1`. E2E-verified live: exact 1.0 hit · close paraphrase ("our"↔"the") 0.966 hit · far topic rejected · weak paraphrase 0.637 rejected · schema drift → miss+status=stale · demote drops vector. Real embeddings confirmed (text-embedding-3-small). NOTE: promotion still manual (`promote_answer`) — auto-promotion is P3.
- **P2** ✅ DONE (2026-06-11) — `dash/learning/question_clusters.py` `cluster_questions(slug, *, days=30, min_count=2, limit=50)`. Read-only, deterministic (no LLM/embeddings): unions logged questions from `public.dash_feedback` (question col) + `public.dash_embed_calls.message_text` (JOIN `dash_agent_embeds` for slug — embed_calls has no project_slug), normalizes (`_norm`), groups, returns clusters {representative, norm, count, variants, last_seen, sources}. `dash_traces` skipped (meta stamps only actor/channel/store, NOT the question). E2E: 17 real clusters surfaced.
- **P3** ✅ DONE (2026-06-11) — `dash/learning/cache_curator.py` + `app/cache_curator_api.py` + daemon `dash/cron/cache_curator_daemon.py`. `run_curator(slug, dry_run, max_promote, days, min_count)`: pull clusters → `_is_cached` skip → **leader judge** (`training_llm_call` as lead analyst, fed the REAL schema via `_schema_context` = information_schema listing so it never hallucinates tables; refuses time-relative/personal/clinical) → read-only SQL gate → verify via `verified_reward._run_rows` → `_build_card` (valid AnswerCard tags) → `promote_answer(promoted_by='leader')`. `curator_stats`. API (super-admin, mirrors usage_api auth): `POST /api/projects/{slug}/cache/curate?dry_run=` · `GET .../cache/stats` · `GET .../cache/clusters`. Daemon **DEFAULT OFF** (writes to cache) — opt in `CACHE_CURATOR_ENABLED=1` (+`CACHE_CURATOR_DISABLED=1` hard off, `CACHE_CURATOR_MAX_PROMOTE`), 24h, leader-gated, single-tenant locked slug. Wired main.py (router include + lifespan daemon). **LANDMINE fixed:** without `_schema_context` the leader invents fake tables (`products`/`inventory_t`) → all SQL fails; the real-schema listing is mandatory. E2E: 12 valid candidates, "currently" refused, 2 promoted as leader rows, 3 endpoints 200. Test rows wiped (prod cache empty).
- **P4** ✅ DONE (2026-06-11) — admin tab in the Usage dashboard. New "Answer Cache" rail tab in `frontend/src/lib/admin/UsagePanel.svelte`: KPI tiles (live rows / total hits / leader-made / stale), **Frequent questions** list w/ per-row **Cache this →** (targeted promote), **Cached answers** table (question · hits · source · schema ✓/⚠drift · by · **Evict**), **Run curation preview** → leader-judged candidates + **Promote all**. Slug from `/api/flags.locked_slug`. New backend endpoints in `app/cache_curator_api.py`: `GET /cache/list` (`list_cached` — rows + live schema-fresh flag, excludes demoted), `POST /cache/promote` ({question} → `curate_one`), `POST /cache/{id}/evict` (`demote_answer`). Refactored `cache_curator.curate_one` (single-question judge→verify→promote, reused by `run_curator`). E2E-verified: promote→list shows row→evict→list back to 0 (demoted excluded); time-relative "currently" still refused. Frontend builds clean. Note: daemon enable stays env-only (`CACHE_CURATOR_ENABLED=1`) — UI shows state + manual promote, can't flip env live.

## STATUS: P0–P4 ALL COMPLETE (2026-06-11). Full self-learning answer cache live. Daemon default OFF.

## Honest caveats
- Helps REPEAT questions only; first-ask still pays the agent (fine — ~50 Qs asked 100×/day → high hit-rate).
- NOT vector-replaces-SQL: cache stores the SQL's answer; SQL still computes truth on a miss.
- Deploy = image rebuild (no hot-copy). New table → migration + fold into baseline seed for cold installs.
