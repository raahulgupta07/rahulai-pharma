# Training Profile

## Run metadata

- Date: 2026-05-20 12:52
- Rows: {'dim_articles': 500, 'dim_stores': 56, 'fact_sales': 20000}
- DB: postgresql+psycopg://ai:ai@pg-profile:5432/ai
- slug: profile_pharma

## Ranked report (cold run)

```
============================================================
TRAINING PROFILE REPORT  [run: cold]
============================================================
RANK  STEP                      PHASE  CALLS    SEC  %WALL  LLM  TOKENS       $  NOTE
----  ------------------------  -----  -----  -----  -----  ---  ------  ------  -----------
   1  _run_auto_training        -          3  201.5  73.1%    0       0  0.0000
   2  _llm_generate_training    -          3   21.0   7.6%    0       0  0.0000
   3  _llm_deep_analysis        -          3   20.6   7.5%    0       0  0.0000
   4  _llm_generate_persona     -          3   10.8   3.9%    0       0  0.0000
   5  _run_evals_for_slug       -          1   10.7   3.9%    0       0  0.0000
   6  _discover_relationships   -          3    8.6   3.1%    0       0  0.0000
   7  auto_create_models        -          4    1.8   0.7%    0       0  0.0000
   8  _detect_hierarchies       -          3    0.2   0.1%    0       0  0.0000
   9  _enqueue_vector_backfill  -          1    0.1   0.0%    0       0  0.0000
  10  build_knowledge_graph     -          1    0.1   0.0%    0       0  0.0000
  11  _sql_profile_columns      -          3    0.1   0.0%    0       0  0.0000
  12  _build_dimension_catalog  -          3    0.1   0.0%    0       0  0.0000
  13  derive_scope              -          1    0.0   0.0%    0       0  0.0000  cached/skip
  14  derive_goals              -          1    0.0   0.0%    0       0  0.0000  cached/skip
  15  _smart_sample_rows        -          3    0.0   0.0%    0       0  0.0000  cached/skip
  16  classify_vertical         -          1    0.0   0.0%    0       0  0.0000  cached/skip
------------------------------------------------------------
TOTAL wall:       275.5s  (summed from steps; no wall clock given)
TOTAL $:          $0.0000
TOTAL llm_calls:  0
Top-3 time sinks: _run_auto_training, _llm_generate_training, _llm_deep_analysis
Steps at ~0s:     4  (likely cached/skipped)
============================================================
```

## Cold vs warm vs mock

```
============================================================
RUN COMPARISON (seconds per step)
============================================================
                    STEP  cold (s)  mock (s)
------------------------  --------  --------
      _run_auto_training  201.5     201.5
  _llm_generate_training  21.0      21.0
      _llm_deep_analysis  20.6      20.6
   _llm_generate_persona  10.8      10.8
     _run_evals_for_slug  10.7      10.7
 _discover_relationships  8.6       8.6
      auto_create_models  1.8       1.8
     _detect_hierarchies  0.2       0.2
_enqueue_vector_backfill  0.1       0.1
   build_knowledge_graph  0.1       0.1
    _sql_profile_columns  0.1       0.1
_build_dimension_catalog  0.1       0.1
            derive_scope  0.0       0.0
            derive_goals  0.0       0.0
      _smart_sample_rows  0.0       0.0
       classify_vertical  0.0       0.0
------------------------------------------------------------
VERDICT
------------------------------------------------------------
Cache value: need both 'cold' and 'warm' runs.
LLM share: real cold 275.5s vs mock 275.5s  -> LLM time ~0.0s (0.0% of cold)
------------------------------------------------------------
CLASSIFICATION (top steps)
  _run_auto_training              201.5s  [STUCK]
  _llm_generate_training           21.0s  [OK]
  _llm_deep_analysis               20.6s  [OK]
  _llm_generate_persona            10.8s  [OK]
  _run_evals_for_slug              10.7s  [OK]
  _discover_relationships           8.6s  [OK]
  auto_create_models                1.8s  [OK]
  _detect_hierarchies               0.2s  [CHEAP]
============================================================
```

## Bottlenecks + recommended optimizations

| # | Step | Sec | Class | Recommendation |
|---|------|-----|-------|----------------|
| 1 | _run_auto_training | 201.5 | STUCK | single call ~67.2s — add timeout/retry, stream, or split the prompt; called 3x — parallelize across tables |
| 2 | _llm_generate_training | 21.0 | OK | called 3x — parallelize across tables |
| 3 | _llm_deep_analysis | 20.6 | OK | called 3x — parallelize across tables |
| 4 | _llm_generate_persona | 10.8 | OK | called 3x — parallelize across tables |
| 5 | _run_evals_for_slug | 10.7 | OK | monitor; no obvious win |
| 6 | _discover_relationships | 8.6 | OK | called 3x — parallelize across tables |
| 7 | auto_create_models | 1.8 | OK | called 4x — parallelize across tables |
| 8 | _detect_hierarchies | 0.2 | CHEAP | called 3x — parallelize across tables |
