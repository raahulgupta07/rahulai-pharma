# Training Pipeline V2 — Atomic, Fingerprint-Cached, Resumable

Status: PLAN (not built). Reference for redesigning TRAIN ALL.

## 1. Why a V2

Current TRAIN ALL (`app/upload.py::_bg`) is coarse: ~17 big steps, per-table caching only,
one 60s monolithic LLM template call that re-runs every retrain, current_step that freezes
during the concurrent tail. Problems:

- Delta retrain still pays full LLM cost even when little changed.
- One slow/failed step forces redoing a whole block.
- `generate_template` (DEEP_MODEL, ~60s) had its cache write blocked → regenerated every run.
- No per-step observability — UI froze on `scope_derivation` while other steps ran.

## 2. Design principles

1. **Atomic steps.** Each step does ONE thing, has ONE input fingerprint, ONE output.
2. **Fingerprint cache.** `fp = hash(step's real inputs)`. If unchanged → skip + load cached output.
3. **Resumable.** Every step is a row with status; a run re-runs only `queued`/`failed` steps.
4. **Right-sized model.** LITE everywhere; DEEP only where genuinely needed. Rules/SQL where no LLM needed.
5. **Parallel where independent**, serial only where there's a real data dependency.
6. **Fail-soft.** A step failing records `failed` + continues; never hard-crashes the run.

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS public.dash_training_steps (
  id            BIGSERIAL PRIMARY KEY,
  run_id        BIGINT NOT NULL,             -- FK dash_training_runs.id
  project_slug  TEXT   NOT NULL,
  step_no       INT    NOT NULL,             -- 1..N stable ordering
  name          TEXT   NOT NULL,             -- e.g. 'profile_columns'
  scope         TEXT   NOT NULL,             -- 'table:<name>' | 'project' | 'cross_table'
  status        TEXT   NOT NULL DEFAULT 'queued', -- queued|running|done|skipped|failed
  fp            TEXT,                         -- fingerprint of inputs at run time
  output_ref    TEXT,                         -- where result lives (table/json key)
  elapsed_ms    INT,
  error         TEXT,
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  UNIQUE (project_slug, name, scope)          -- one cache row per step per scope
);
CREATE INDEX IF NOT EXISTS idx_training_steps_run ON public.dash_training_steps (run_id);
CREATE INDEX IF NOT EXISTS idx_training_steps_lookup ON public.dash_training_steps (project_slug, name, scope);
```

The `UNIQUE(project_slug, name, scope)` row IS the cache: it stores the last successful `fp`
and `output_ref`. The runner upserts it.

## 4. Fingerprint strategy

`fp = sha256(canonical_json(inputs))[:16]`. Inputs are the MINIMAL set the step depends on:

| Step kind | fp inputs |
|---|---|
| schema-dependent | sorted (column_name, pg_type) list |
| value-dependent | sorted distinct values / counts |
| derived-from-step-X | the X step's stored `fp` (chain) |
| project-wide | concat of all tables' relevant fps |
| vertical | vertical name + column-name set |

Chaining: a downstream step's fp includes its upstream step's fp, so a change cascades only
down the dependency edge, not across the whole pipeline.

## 5. Runner loop (per step)

```python
def run_step(run_id, slug, step):
    fp = step.compute_fp()                       # hash real inputs
    prev = load_step_row(slug, step.name, step.scope)
    if prev and prev.status == 'done' and prev.fp == fp:
        mark(run_id, step, 'skipped', fp, prev.output_ref)   # cache hit, $0
        return prev.output
    mark(run_id, step, 'running', fp)
    t = perf_counter()
    try:
        out = step.execute()                     # the actual work
        store_output(step, out)
        mark(run_id, step, 'done', fp, ref(out), elapsed=ms(t))
        return out
    except Exception as e:
        mark(run_id, step, 'failed', fp, error=str(e)[:500], elapsed=ms(t))
        return None                              # fail-soft, never raise
```

## 6. The steps (1..N)

Legend: model = $0 (SQL/rules) | LITE | DEEP | embed. par = parallel group.

### PHASE A — Ingest & profile (per table, $0, parallel)
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 1 | load_table | table | file hash | rows in PG | $0 | A |
| 2 | fingerprint_table | table | row_count+cols | fp row | $0 | A |
| 3 | profile_columns | table | schema | per-col SQL stats | $0 | A |
| 4 | classify_columns | table | #3 fp | dim/measure/id/text | $0 | A |
| 5 | dimension_catalog | table | distinct vals | top-100 values | $0 | A |
| 6 | detect_hierarchy | table | dims | parent→child | $0 | A |
| 7 | smart_sample | table | schema | 20 diverse rows | $0 | A |

### PHASE B — Per-table understanding (LITE, parallel per table)
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 8 | describe_columns | table | #3+#7 | col meanings | LITE | B |
| 9 | describe_table | table | #8 | purpose/grain/PK | LITE | B |
| 10 | gen_qa_pairs | table | #9 | Q&A + SQL | LITE | B |
| 11 | verify_qa | table | #10 | passing Q&A | $0 SQL | B |
| 12 | gen_business_rules | table | #9 | rules | LITE | B |

### PHASE C — Cross-table (project, after B)
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 13 | discover_relationships | cross_table | all #9 + overlap | FK joins | LITE+SQL | C |
| 14 | build_kg_triples | project | tables+docs | SPO triples | LITE | C |
| 15 | standardize_entities | project | #14 | merged aliases | LITE | C |
| 16 | gen_persona | project | #9+#13 | persona | LITE | C |

### PHASE D — Domain knowledge (6 small LITE, parallel — was 1 monolith)
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 17 | gen_glossary | project | #9+samples | terms | LITE | D |
| 18 | gen_formulas | project | measures | calc defs | LITE | D |
| 19 | gen_value_maps | project | dim catalog | code→meaning | LITE | D |
| 20 | gen_kpis | project | #9+#18 | KPI list | LITE | D |
| 21 | gen_quality_notes | project | profile | caveats | LITE | D |
| 22 | gen_neg_examples | project | #10 fails | mistakes | LITE | D |

### PHASE E — Vertical config (replaces the 60s monolith)
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 23 | detect_vertical | project | #9+#17 | vertical+conf | LITE | — |
| 24 | gen_template_kpis | project | vertical+#9 | domain KPIs | LITE | E |
| 25 | gen_template_workflows | project | vertical+#13 | workflows | LITE | E |
| 26 | gen_template_lexicon | project | vertical | glossary adds | LITE | E |
| 27 | pick_tabs_tools_agents | project | vertical | enable list | $0 rules | E |
| 28 | apply_template | project | #24-27 | seed brain/rules/wf | $0 DB | — |

Gate: run 24-28 only if `#23 confidence >= THRESHOLD` (configurable, default 0.50; raise to
0.70 to reduce surprise auto-config). Static vertical packs in `dash/verticals/<v>/` can
short-circuit 24-26 entirely ($0, no LLM) when a pack exists.

### PHASE F — Guardrails, index, finalize
| # | name | scope | inputs (fp) | output | model | par |
|---|---|---|---|---|---|---|
| 29 | derive_scope | project | #16+#17+#9 | allowed/denied | LITE | F |
| 30 | derive_goals | project | #20 | learning_goals.md | LITE | F |
| 31 | index_vectors | project | knowledge+brain+kg | embeddings | embed | F |
| 32 | train_ml_models | project | numeric tables | forecast/anomaly | $0 sklearn | F |
| 33 | run_evals | project | #10 | pass/fail | LITE×N | F |
| 34 | activate_workflows | project | #25 | pending→active | $0 DB | — |
| 35 | mark_done | project | — | run done | $0 | — |

Ordering: F-parallel group {29,30,31,32,33} → then 34 (must follow 25/28) → then 35.

## 7. Execution / orchestration

```
PHASE A   parallel per table       (all $0)
PHASE B   parallel per table       (LITE, depends on A of same table)
PHASE C   project, after all B
PHASE D   6 tasks parallel, after C
PHASE E   detect(23) → {24,25,26,27} parallel → apply(28)
PHASE F   {29,30,31,32,33} parallel → activate(34) → mark_done(35)
```

- Worker pools: per-phase `ThreadPoolExecutor(max_workers=4)`.
- LLM budget gate (`set_llm_project`) wraps the whole run (existing).
- Concurrent-LLM cap: keep max_workers small (4) to avoid OpenRouter backoff.
- Cancel flag checked between phases + before each step submit.

## 8. Failure & resume

- Step fails → row `failed` + error, run continues (fail-soft).
- Re-run a project → runner loads step rows; `done` with matching fp → skip; `failed`/`queued`/fp-changed → run.
- A run is "done" when all non-skipped steps are terminal. Partial = some `failed` but run still completes (degraded), surfaced in UI.

## 9. Observability

- `current_step` always = the step currently `running` (no freeze; phases update it).
- UI reads `dash_training_steps` for the run → renders 35 rows with status + elapsed_ms.
- Per-step `elapsed_ms` makes the next bottleneck obvious without guessing.

## 10. Migration from current pipeline (incremental, keep old working)

Don't rewrite `_bg` in one shot. Strangler approach:

1. Add `dash_training_steps` table (migration, idempotent).
2. Add a `StepRunner` helper (`dash/training/runner.py`) implementing §5.
3. Wrap EXISTING steps one-by-one in `runner.run_step(...)` — behavior identical, now cached + tracked. No new logic yet.
4. Once wrapped, split the monoliths:
   - `generate_template` → steps 24/25/26/27 (Phase E).
   - domain_knowledge blob → steps 17-22 (Phase D).
5. Switch per-table fingerprint to per-step fingerprint (steps 3-12).
6. Delete dead old code paths after parity confirmed.

Each migration step is independently shippable + reversible.

## 11. Build order (recommendation)

| Priority | What | Why |
|---|---|---|
| P0 | `dash_training_steps` + StepRunner | foundation for everything |
| P0 | wrap PHASE E (23-28) in runner + fingerprint cache | kills the 60s bottleneck structurally |
| P1 | split domain_knowledge → D (17-22) | smaller cached calls |
| P1 | per-step fingerprint for B (8-12) | delta retrain only changed table-steps |
| P2 | UI reads dash_training_steps (35-row progress) | observability |
| P2 | static vertical-pack short-circuit for E | $0 when pack exists |

## 12. Old vs New

| | OLD | NEW V2 |
|---|---|---|
| granularity | ~17 coarse steps | 35 atomic |
| caching | per-table only | per-step fingerprint |
| template gen | 1×60s DEEP every run | 4 LITE, cached → ms on hit |
| domain knowledge | 1 multi-output call | 6 small cached calls |
| change 1 table | reruns its whole block | only changed steps |
| step failure | redo block | retry one step |
| delta retrain cost | full LLM | ~$0 if unchanged |
| observability | current_step freezes | 35 status rows + ms |
| models | DEEP for template | LITE default, DEEP only where needed |

## 13. Risks

- Step explosion → orchestration complexity. Mitigate: phases + small runner, no per-step bespoke code.
- Fingerprint too broad → never caches; too narrow → stale. Mitigate: keep fp inputs minimal + chained.
- Concurrent LLM backoff. Mitigate: max_workers=4, LITE models.
- `dash_training_steps` growth. Mitigate: cache rows are upserted (one per name+scope); run-history rows pruned >90d.
- Migration parity bugs. Mitigate: strangler — wrap before split, confirm parity per step.
