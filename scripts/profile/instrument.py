"""Standalone training profiler instrumentation.

Maps the 35 V2 training steps (docs/TRAINING_PIPELINE_V2.md) to the REAL
underlying Python functions, and provides a monkeypatch-based timing + LLM-cost
instrumentation context manager so a profiler can run the real pipeline and
collect per-step wall-time + LLM calls/tokens/$.

Pure stdlib + `dash.settings` import. Read-only on the rest of the codebase —
nothing here mutates the pipeline; it only wraps functions for the duration of
a `profile_calls(...)` context.

------------------------------------------------------------------------------
COVERAGE NOTES (read before trusting per-step numbers)
------------------------------------------------------------------------------
The live pipeline is NOT 35 cleanly-separated functions. It is:

  1. A per-table function `app.upload:_run_auto_training` that INLINES most of
     phases A, B, D, F (steps 1-12, 17-22, 30 etc.) as straight-line code with
     no separate callable. Those steps are NOT individually wrappable — wrapping
     `_run_auto_training` captures them as ONE bundled timing.
     The genuinely-separate helpers it DOES call (and which ARE wrappable):
       _sql_profile_columns, _build_dimension_catalog, _detect_hierarchies,
       _smart_sample_rows, _llm_deep_analysis, _llm_generate_training,
       _llm_generate_persona, _discover_relationships, _reload_project_knowledge,
       _detect_data_drift, _langextract_facts.

  2. A master TRAIN ALL tail in `app.upload:_bg` (~line 9471) that calls the
     project-wide steps via separate, individually-wrappable functions:
       build_knowledge_graph, derive_scope, derive_goals, auto_create_models,
       _generate_project_evals, classify_vertical, synthesize_subagents,
       _enqueue_vector_backfill.

So: phases C/E and the project-wide F steps map to real wrappable functions;
phases A/B/D and several F steps are inlined in `_run_auto_training` and can only
be measured as a bundle. See each STEP_MAP entry's `wrappable` + `note`.
"""

import time
import importlib
import threading
import contextlib
import logging

__all__ = [
    "STEP_MAP",
    "ALL_TARGETS",
    "profile_calls",
    "get_timings",
    "reset_timings",
]

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — MAP: 35 V2 steps → real "module:function" targets.
# Ordered by step_no 1..35.
# ─────────────────────────────────────────────────────────────────────────────
STEP_MAP: list[dict] = [
    # ── PHASE A — Ingest & profile (per table, mostly inlined) ──────────────
    {"step_no": 1, "name": "load_table", "phase": "A", "target": None,
     "wrappable": False,
     "note": "inlined: pd.read_sql / COPY ingest happens before training; no separate fn in _run_auto_training"},
    {"step_no": 2, "name": "fingerprint_table", "phase": "A",
     "target": "app.upload:check_fingerprint_changed", "wrappable": True,
     "note": "called in _bg._train_one before training; save_fingerprint writes it"},
    {"step_no": 3, "name": "profile_columns", "phase": "A",
     "target": "app.upload:_sql_profile_columns", "wrappable": True,
     "note": "separate fn; SQL column profiling ($0)"},
    {"step_no": 4, "name": "classify_columns", "phase": "A", "target": None,
     "wrappable": False,
     "note": "inlined in _run_auto_training: dim/measure/id split derived from _sql_profile_columns output (list comprehensions, no fn)"},
    {"step_no": 5, "name": "dimension_catalog", "phase": "A",
     "target": "app.upload:_build_dimension_catalog", "wrappable": True,
     "note": "separate fn; DISTINCT value catalog ($0)"},
    {"step_no": 6, "name": "detect_hierarchy", "phase": "A",
     "target": "app.upload:_detect_hierarchies", "wrappable": True,
     "note": "separate fn; parent→child detection ($0)"},
    {"step_no": 7, "name": "smart_sample", "phase": "A",
     "target": "app.upload:_smart_sample_rows", "wrappable": True,
     "note": "separate fn; 20 diverse rows ($0)"},

    # ── PHASE B — Per-table understanding (LITE) ────────────────────────────
    {"step_no": 8, "name": "describe_columns", "phase": "B",
     "target": "app.upload:_llm_deep_analysis", "wrappable": True,
     "note": "bundled: covers 8+9 — _llm_deep_analysis returns column_descriptions AND table purpose/grain/PK/FK in one LLM call"},
    {"step_no": 9, "name": "describe_table", "phase": "B",
     "target": "app.upload:_llm_deep_analysis", "wrappable": True,
     "note": "bundled: covers 8+9 — same single _llm_deep_analysis call as step 8"},
    {"step_no": 10, "name": "gen_qa_pairs", "phase": "B",
     "target": "app.upload:_llm_generate_training", "wrappable": True,
     "note": "separate fn; Q&A + SQL generation (LITE)"},
    {"step_no": 11, "name": "verify_qa", "phase": "B", "target": None,
     "wrappable": False,
     "note": "inlined in _run_auto_training (~2674): runs each generated SQL against real DB; no separate fn"},
    {"step_no": 12, "name": "gen_business_rules", "phase": "B", "target": None,
     "wrappable": False,
     "note": "inlined: business rules come from _llm_deep_analysis output + _generate_business_rules pre-pass; no dedicated step fn inside training"},

    # ── PHASE C — Cross-table (project) ─────────────────────────────────────
    {"step_no": 13, "name": "discover_relationships", "phase": "C",
     "target": "app.upload:_discover_relationships", "wrappable": True,
     "note": "separate fn; cross-table FK joins (LITE+SQL)"},
    {"step_no": 14, "name": "build_kg_triples", "phase": "C",
     "target": "dash.tools.knowledge_graph:build_knowledge_graph", "wrappable": True,
     "note": "bundled: covers 14+15 — build_knowledge_graph also standardizes entities internally (_standardize_entities). Runs once in _bg tail (~9748)"},
    {"step_no": 15, "name": "standardize_entities", "phase": "C",
     "target": "dash.tools.knowledge_graph:build_knowledge_graph", "wrappable": True,
     "note": "bundled: covers 14+15 — _standardize_entities is called inside build_knowledge_graph, not separately from the pipeline"},
    {"step_no": 16, "name": "gen_persona", "phase": "C",
     "target": "app.upload:_llm_generate_persona", "wrappable": True,
     "note": "separate fn; persona generation (LITE). Note: persona_enrich later re-generates inline (step ~30-adjacent, not separately wrappable)"},

    # ── PHASE D — Domain knowledge (6 LITE calls, ALL inlined) ──────────────
    {"step_no": 17, "name": "gen_glossary", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined in _run_auto_training 'domain_knowledge' step (~3239): training_llm_call for glossary. Not a separate fn — only measurable via LLM observer attribution to _run_auto_training bundle"},
    {"step_no": 18, "name": "gen_formulas", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined (~3286): calculation rules training_llm_call inside _run_auto_training"},
    {"step_no": 19, "name": "gen_value_maps", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined (~3314): value mappings training_llm_call inside _run_auto_training"},
    {"step_no": 20, "name": "gen_kpis", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined (~3340): KPI definitions training_llm_call inside _run_auto_training"},
    {"step_no": 21, "name": "gen_quality_notes", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined (~3393): data quality rules training_llm_call inside _run_auto_training"},
    {"step_no": 22, "name": "gen_neg_examples", "phase": "D", "target": None,
     "wrappable": False,
     "note": "inlined (~3422): negative examples training_llm_call inside _run_auto_training"},

    # ── PHASE E — Vertical config (auto_configurator + phase_e) ─────────────
    {"step_no": 23, "name": "detect_vertical", "phase": "E",
     "target": "dash.learning.auto_configurator:classify_vertical", "wrappable": True,
     "note": "separate fn; vertical detection. Runs in _bg tail _task_auto_configure (~9971)"},
    {"step_no": 24, "name": "gen_template_kpis", "phase": "E",
     "target": "dash.learning.auto_apply:auto_apply_vertical", "wrappable": True,
     "note": "bundled: covers 24-28 — when confidence>=0.5, auto_apply_vertical applies the whole vertical pack (template KPIs/workflows/lexicon/tabs+tools+agents/seed) in one call (~9978). V2 path uses dash.training.phase_e:run_phase_e instead"},
    {"step_no": 25, "name": "gen_template_workflows", "phase": "E",
     "target": "dash.learning.auto_apply:auto_apply_vertical", "wrappable": True,
     "note": "bundled: covers 24-28 — same auto_apply_vertical call as step 24"},
    {"step_no": 26, "name": "gen_template_lexicon", "phase": "E",
     "target": "dash.learning.auto_apply:auto_apply_vertical", "wrappable": True,
     "note": "bundled: covers 24-28 — same auto_apply_vertical call"},
    {"step_no": 27, "name": "pick_tabs_tools_agents", "phase": "E",
     "target": "dash.learning.auto_apply:auto_apply_vertical", "wrappable": True,
     "note": "bundled: covers 24-28 — same auto_apply_vertical call ($0 rules portion)"},
    {"step_no": 28, "name": "apply_template", "phase": "E",
     "target": "dash.learning.auto_apply:auto_apply_vertical", "wrappable": True,
     "note": "bundled: covers 24-28 — same auto_apply_vertical call (seed brain/rules/wf DB writes)"},

    # ── PHASE F — Guardrails, index, finalize ───────────────────────────────
    {"step_no": 29, "name": "derive_scope", "phase": "F",
     "target": "dash.scope_deriver:derive_scope", "wrappable": True,
     "note": "separate fn; scope guardrail (LITE). Runs in _bg tail _task_scope (~9839)"},
    {"step_no": 30, "name": "derive_goals", "phase": "F",
     "target": "dash.learning.goals_deriver:derive_goals", "wrappable": True,
     "note": "separate fn; learning_goals.md. Runs in _bg tail _task_goals (~9881)"},
    {"step_no": 31, "name": "index_vectors", "phase": "F",
     "target": "app.upload:_enqueue_vector_backfill", "wrappable": True,
     "note": "separate fn; enqueues embeddings backfill (~9805). Actual embedding is async in VECTOR_SYNC worker — wrapper times only the enqueue, not the embed work. Note: _reload_project_knowledge (~3002) also re-indexes per-table inline"},
    {"step_no": 32, "name": "train_ml_models", "phase": "F",
     "target": "dash.tools.ml_models:auto_create_models", "wrappable": True,
     "note": "separate fn; sklearn forecast/anomaly ($0). Runs in _bg tail _task_ml (~9899). Heavy models may queue to dash-ml worker (async, not timed here)"},
    {"step_no": 33, "name": "run_evals", "phase": "F",
     "target": "app.learning:_run_evals_for_slug", "wrappable": True,
     "note": "bundled: covers 33 (+ eval-gen) — _task_evals first calls _generate_project_evals (app.upload:_generate_project_evals, separately wrappable) then _run_evals_for_slug (LITE×N). Both run in _bg tail (~9931-9939)"},
    {"step_no": 34, "name": "activate_workflows", "phase": "F", "target": None,
     "wrappable": False,
     "note": "inlined/side-effect: workflow activation happens inside auto_apply_vertical (step 28) DB writes; no separate activate fn in the live pipeline"},
    {"step_no": 35, "name": "mark_done", "phase": "F", "target": None,
     "wrappable": False,
     "note": "inlined in _bg tail (~10068): UPDATE dash_training_runs SET status='done'; no separate fn"},
]


# Convenience: deduped list of wrappable real targets.
ALL_TARGETS: list[str] = []
for _m in STEP_MAP:
    _t = _m.get("target")
    if _m.get("wrappable") and _t and _t not in ALL_TARGETS:
        ALL_TARGETS.append(_t)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — INSTRUMENTATION
# ─────────────────────────────────────────────────────────────────────────────
# Each record: {name, target, seconds, llm_calls, tokens_in, tokens_out, cost_usd}
_TIMINGS: list[dict] = []
_TIMINGS_LOCK = threading.Lock()

# Threadlocal stack of (target, llm_accumulator_dict) frames. The innermost
# wrapped fn currently executing on THIS thread is the top of the stack. The LLM
# observer reads the top frame and attributes the call to it (innermost wins →
# recursion/re-entrancy safe; a wrapped fn calling another wrapped fn → both are
# timed, the LLM call lands on the inner one).
_CALLSTACK = threading.local()


def _stack() -> list:
    s = getattr(_CALLSTACK, "frames", None)
    if s is None:
        s = []
        _CALLSTACK.frames = s
    return s


def get_timings() -> list[dict]:
    """Return a copy of all recorded per-call timing/LLM records."""
    with _TIMINGS_LOCK:
        return list(_TIMINGS)


def reset_timings() -> None:
    """Clear all recorded timings."""
    with _TIMINGS_LOCK:
        _TIMINGS.clear()


def _parse_target(target: str):
    """'module:func' → (module_path, func_name). Raises ValueError on bad form."""
    if ":" not in target:
        raise ValueError(f"target must be 'module:function', got {target!r}")
    mod, fn = target.split(":", 1)
    return mod, fn


def _make_wrapper(target: str, original):
    """Build a timing wrapper around `original` for `target`.

    On entry: push a frame onto this thread's callstack so the LLM observer
    attributes any LLM calls made during `original` to this target (innermost).
    On exit: pop the frame, compute elapsed, fold the frame's LLM accumulator
    into the inner attribution, and append a _TIMINGS record. The popped frame's
    LLM stats are subtracted-by-design via per-frame accumulation: the observer
    only ever increments the TOP frame, so each frame's accumulator already holds
    exactly the calls attributable to that (innermost) target.
    """

    def _wrapper(*args, **kwargs):
        frame = {
            "target": target,
            "llm_calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
        }
        st = _stack()
        st.append(frame)
        t0 = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - t0
            # Pop our frame (defensively find it in case of imbalance).
            try:
                st.pop()
            except IndexError:
                pass
            rec = {
                "name": target.rsplit(":", 1)[-1],
                "target": target,
                "seconds": round(elapsed, 6),
                "llm_calls": frame["llm_calls"],
                "tokens_in": frame["tokens_in"],
                "tokens_out": frame["tokens_out"],
                "cost_usd": round(frame["cost_usd"], 6),
            }
            with _TIMINGS_LOCK:
                _TIMINGS.append(rec)

    _wrapper.__name__ = getattr(original, "__name__", "wrapped")
    _wrapper.__doc__ = getattr(original, "__doc__", None)
    _wrapper.__wrapped__ = original
    return _wrapper


def _llm_observer(stats: dict):
    """Registered via dash.settings.set_llm_observer.

    Attributes the LLM call to the innermost wrapped target currently on THIS
    thread's callstack. If no wrapped fn is on the stack (LLM call outside any
    wrapped target), the call is ignored for attribution (it's still recorded in
    the project cost ledger by settings itself)."""
    st = getattr(_CALLSTACK, "frames", None)
    if not st:
        return
    frame = st[-1]
    try:
        frame["llm_calls"] += 1
        frame["tokens_in"] += int(stats.get("tokens_in", 0) or 0)
        frame["tokens_out"] += int(stats.get("tokens_out", 0) or 0)
        frame["cost_usd"] += float(stats.get("cost_usd", 0.0) or 0.0)
    except Exception:
        # Never let observer accounting break the pipeline.
        pass


@contextlib.contextmanager
def profile_calls(targets: list[str]):
    """Monkeypatch each 'module:func' target with a timing wrapper for the
    duration of the context, then restore originals.

    - Records each call into _TIMINGS (one record per invocation).
    - Installs an LLM observer (dash.settings.set_llm_observer) that attributes
      tokens/$ to whichever wrapped target is innermost on the call stack.
    - Restores ALL original attributes in a finally (even on exception).
    - Fail-soft: a target that can't be imported/patched is skipped (logged),
      not fatal.
    - Re-entrancy / recursion safe via the threadlocal frame stack.
    """
    patched: list[tuple] = []  # (module_obj, func_name, original_callable)
    obs_tag = None

    try:
        # Install the LLM observer first so calls during patched fns are caught.
        try:
            from dash.settings import set_llm_observer
            obs_tag = set_llm_observer(_llm_observer, tag="profile:instrument")
        except Exception as e:
            _log.warning("profile_calls: could not install LLM observer: %s", e)
            obs_tag = None

        for target in (targets or []):
            try:
                mod_path, fn_name = _parse_target(target)
                module = importlib.import_module(mod_path)
                original = getattr(module, fn_name)
                if not callable(original):
                    _log.warning("profile_calls: %s is not callable — skipped", target)
                    continue
                # Avoid double-wrapping if the same target appears twice.
                if getattr(original, "__wrapped__", None) is not None and \
                        getattr(original, "__name__", "") == fn_name and \
                        any(p[0] is module and p[1] == fn_name for p in patched):
                    continue
                wrapper = _make_wrapper(target, original)
                setattr(module, fn_name, wrapper)
                patched.append((module, fn_name, original))
            except Exception as e:
                _log.warning("profile_calls: could not patch %s — skipped: %s", target, e)
                continue

        yield

    finally:
        # Restore ALL originals, even if patching/yield raised.
        for module, fn_name, original in patched:
            try:
                setattr(module, fn_name, original)
            except Exception as e:
                _log.warning("profile_calls: could not restore %s:%s — %s",
                             getattr(module, "__name__", "?"), fn_name, e)
        # Remove the observer.
        if obs_tag is not None:
            try:
                from dash.settings import reset_llm_observer
                reset_llm_observer(obs_tag)
            except Exception as e:
                _log.warning("profile_calls: could not reset LLM observer: %s", e)
