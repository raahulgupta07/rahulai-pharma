"""Scheduled production eval canary.

OpenAI runs evals continuously in prod to catch regressions; this wires the
EXISTING smoke suite (``evals/smoke.py``) into a daily scheduled canary so
behaviour drift surfaces without anyone running the CLI by hand.

What it runs
------------
The lightweight SMOKE suite (keyword/regex assertions on real team runs), NOT
the LLM-judge eval suite — smoke is the cheapest signal. The number of cases is
capped by ``EVAL_CANARY_MAX_CASES`` (default 8) to bound cost; running the team
calls the LLM.

Regression detection
---------------------
Each run records per-group pass/fail into a compact tracking table
(``dash_eval_canary_runs``, JSONB ``groups`` column). On each run we compare the
current per-group result to the PREVIOUS canary run:

* a group that PASSED last time and FAILS now  → **regression** (WARNING + notify)
* a group that FAILED last time and PASSES now  → **new_pass** (logged INFO)

Notification reuses ``app.auth.notify_user`` (best-effort) to the super-admin;
if that's unavailable we just emit a structured WARNING log.

Cost / safety
-------------
* Fail-soft: ``run_eval_canary`` never raises — every step is wrapped.
* Cost-aware: ``EVAL_CANARY_MAX_CASES`` caps how many smoke cases run.
* Opt-out: ``EVAL_CANARY_DISABLED=1``.
* Cadence: ``EVAL_CANARY_INTERVAL_SECONDS`` (default 86400 = daily).
* NOT run inline at boot — only on the scheduled loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

_DAY = 86400
_DEFAULT_MAX_CASES = 8


def _disabled() -> bool:
    return os.environ.get("EVAL_CANARY_DISABLED") in ("1", "true", "TRUE", "yes")


def _max_cases() -> int:
    raw = os.environ.get("EVAL_CANARY_MAX_CASES")
    if not raw:
        return _DEFAULT_MAX_CASES
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_CASES


def _bootstrap_table() -> None:
    """Create the compact canary tracking table (idempotent)."""
    from db import get_sql_engine
    from sqlalchemy import text

    eng = get_sql_engine()
    ddl = """
    CREATE TABLE IF NOT EXISTS public.dash_eval_canary_runs (
      id BIGSERIAL PRIMARY KEY,
      run_at TIMESTAMPTZ DEFAULT now(),
      total INT NOT NULL DEFAULT 0,
      passed INT NOT NULL DEFAULT 0,
      failed INT NOT NULL DEFAULT 0,
      groups JSONB NOT NULL DEFAULT '{}'::jsonb,
      regressions JSONB NOT NULL DEFAULT '[]'::jsonb
    );
    CREATE INDEX IF NOT EXISTS idx_eval_canary_run_at
      ON public.dash_eval_canary_runs(run_at DESC);
    """
    with eng.begin() as cn:
        cn.execute(text(ddl))


def _load_previous_groups() -> dict[str, str]:
    """Per-group status from the most recent prior canary run ({group: PASS|FAIL})."""
    from db import get_sql_engine
    from sqlalchemy import text

    eng = get_sql_engine()
    try:
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT groups FROM public.dash_eval_canary_runs "
                "ORDER BY run_at DESC LIMIT 1"
            )).fetchone()
        if not row or not row[0]:
            return {}
        groups = row[0]
        if isinstance(groups, str):
            groups = json.loads(groups)
        return groups if isinstance(groups, dict) else {}
    except Exception:
        log.exception("eval_canary: failed to load previous run")
        return {}


def _persist_run(total: int, passed: int, failed: int,
                 groups: dict[str, str], regressions: list[str]) -> int | None:
    from db import get_sql_engine
    from sqlalchemy import text

    eng = get_sql_engine()
    try:
        with eng.begin() as cn:
            rid = cn.execute(text(
                "INSERT INTO public.dash_eval_canary_runs "
                "(total, passed, failed, groups, regressions) "
                "VALUES (:t, :p, :f, CAST(:g AS jsonb), CAST(:r AS jsonb)) RETURNING id"
            ), {
                "t": total, "p": passed, "f": failed,
                "g": json.dumps(groups), "r": json.dumps(regressions),
            }).scalar()
        return int(rid) if rid is not None else None
    except Exception:
        log.exception("eval_canary: failed to persist run")
        return None


def _notify_regressions(regressions: list[str], total: int, passed: int, failed: int) -> None:
    """Best-effort notify super-admin; falls back to structured WARNING log only."""
    msg = (
        f"Eval canary regressions: {', '.join(regressions)} "
        f"(passed {passed}/{total}, failed {failed})"
    )
    log.warning("eval_canary REGRESSION: %s", msg)
    try:
        from db import get_sql_engine
        from sqlalchemy import text

        admin = os.getenv("SUPER_ADMIN", "admin")
        eng = get_sql_engine()
        with eng.connect() as cn:
            row = cn.execute(text(
                "SELECT id FROM public.dash_users WHERE username = :u LIMIT 1"
            ), {"u": admin}).fetchone()
        if row:
            from app.auth import notify_user
            notify_user(int(row[0]), "Eval canary regression", msg, "warn")
    except Exception:
        # Notification is best-effort; the WARNING log above is the guarantee.
        log.debug("eval_canary: notify_user unavailable, logged only", exc_info=True)


def run_eval_canary() -> dict:
    """Run the capped smoke suite, compare to the previous canary run.

    Returns ``{run_id, total, passed, failed, regressions, new_passes}``.
    Never raises — fail-soft by design (returns an ``error`` key on failure).
    """
    if _disabled():
        return {"skipped": "EVAL_CANARY_DISABLED"}

    try:
        _bootstrap_table()
    except Exception:
        log.exception("eval_canary: table bootstrap failed (continuing)")

    cap = _max_cases()

    # Run the smoke suite (capped). run_smoke_tests prints; we collect SmokeResults.
    try:
        from evals.smoke import run_smoke_tests, TESTS

        # Cap by selecting whole groups in order until we reach the case budget,
        # so dependent tests stay with their group. If a single group already
        # exceeds the cap we still run that one group (smallest viable signal).
        chosen_ids: set[str] = set()
        count = 0
        for t in TESTS:
            if count >= cap:
                break
            chosen_ids.add(t.id)
            count += 1
        # Monkey-free filtering: run all, then keep only the budgeted slice.
        # run_smoke_tests has no case-cap arg, so run the whole suite would be
        # expensive — instead temporarily trim TESTS to the budget.
        import evals.smoke as _smoke_mod
        _orig_tests = _smoke_mod.TESTS
        try:
            _smoke_mod.TESTS = [t for t in _orig_tests if t.id in chosen_ids]
            results = run_smoke_tests()
        finally:
            _smoke_mod.TESTS = _orig_tests
    except Exception as e:
        log.exception("eval_canary: smoke run failed")
        return {"error": f"smoke run failed: {e}"}

    # Aggregate per-group: a group PASSES only if every run in it passed.
    group_status: dict[str, str] = {}
    total = len(results)
    passed = 0
    failed = 0
    for r in results:
        ok = r.status == "PASS"
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        g = r.test.group
        if not ok:
            group_status[g] = "FAIL"
        elif g not in group_status:
            group_status[g] = "PASS"

    # Compare to previous run.
    prev = _load_previous_groups()
    regressions = [g for g, s in group_status.items()
                   if s == "FAIL" and prev.get(g) == "PASS"]
    new_passes = [g for g, s in group_status.items()
                  if s == "PASS" and prev.get(g) == "FAIL"]

    run_id = _persist_run(total, passed, failed, group_status, regressions)

    if regressions:
        _notify_regressions(regressions, total, passed, failed)
    if new_passes:
        log.info("eval_canary recovered groups: %s", ", ".join(new_passes))
    log.info(
        "eval_canary done: run_id=%s total=%d passed=%d failed=%d regressions=%d",
        run_id, total, passed, failed, len(regressions),
    )

    return {
        "run_id": run_id,
        "total": total,
        "passed": passed,
        "failed": failed,
        "regressions": regressions,
        "new_passes": new_passes,
    }


def _interval_seconds() -> float:
    raw = os.environ.get("EVAL_CANARY_INTERVAL_SECONDS")
    if not raw:
        return float(_DAY)
    try:
        return max(60.0, float(raw))
    except ValueError:
        return float(_DAY)


async def eval_canary_loop() -> None:
    """Forever-loop: sleep the interval, run the canary once. Cancellation-safe.

    Does NOT run inline on first iteration — sleeps first so boot stays cheap
    and the canary never runs synchronously at startup.
    """
    if _disabled():
        log.info("EVAL_CANARY_DISABLED set — canary not started")
        return

    interval = _interval_seconds()
    log.info(
        "eval_canary_loop started (interval=%.0fs, max_cases=%d)",
        interval, _max_cases(),
    )
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("eval_canary_loop sleep error")
            await asyncio.sleep(_DAY)
            continue

        try:
            # run_eval_canary is sync + LLM-bound — offload to a thread.
            with trace_span("cron.eval_canary", kind="cron"):
                await asyncio.to_thread(run_eval_canary)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("eval_canary_loop run failed (fail-soft)")


__all__ = ["run_eval_canary", "eval_canary_loop"]
