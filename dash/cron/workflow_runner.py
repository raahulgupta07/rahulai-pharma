"""Workflow runner daemon — claims queued workflow runs, executes steps,
builds an auto-dashboard from the results.

Pattern mirrors `dash/cron/table_usage_refresh.py`:
- async loop, fail-soft per cycle, configurable interval
- honors _should_run_daemons() + WORKER_RANK==0 (gated by caller in lifespan)
- kill switch via env WORKFLOW_RUNNER_DISABLED=1
- interval via env WORKFLOW_RUNNER_INTERVAL_SECONDS (default 5)

For each tick:
  SELECT up to 10 queued runs
  For each: atomic-claim via UPDATE … WHERE status='queued' RETURNING …
  Execute the workflow (one SQL step today: resolved_query/query_template)
  Auto-build a dashboard from the result via dash.dashboards.from_workflow
  Mark run done/failed with dashboard_id + duration_ms
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Observability tracing — fail-soft no-op if unavailable.
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None

# SQL-VALIDATED: central validator + schema-hint helpers. Fail-soft on import:
# if validator unavailable, we log a warning at call time and skip validation
# rather than break the cron daemon.
try:
    from dash.tools.sql_validator import validate_and_fix as _sql_validate_and_fix
except Exception as _e:  # noqa: BLE001
    _sql_validate_and_fix = None  # type: ignore[assignment]
    logger.warning("workflow_runner: sql_validator unavailable, validation will be skipped: %s", _e)

try:
    from dash.tools.llm_sql_helper import (
        _postgres_sql_rules as _pg_sql_rules,
        get_schema_hint as _get_schema_hint,
    )
except Exception as _e:  # noqa: BLE001
    _pg_sql_rules = None  # type: ignore[assignment]
    _get_schema_hint = None  # type: ignore[assignment]
    logger.warning("workflow_runner: llm_sql_helper unavailable, prompt enrichment skipped: %s", _e)


DEFAULT_INTERVAL_SECONDS = 5
CLAIM_BATCH = 10
SQL_ROW_CAP = 5000
SQL_STATEMENT_TIMEOUT_MS = 30000


def _is_disabled() -> bool:
    return os.getenv("WORKFLOW_RUNNER_DISABLED", "").lower() in ("1", "true", "yes")


def _interval_seconds() -> int:
    raw = os.getenv("WORKFLOW_RUNNER_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _engine():
    """NullPool engine for the metadata DB. Caller disposes."""
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.pool import NullPool
    from db import db_url
    return _ce(db_url, poolclass=NullPool)


# ── per-step execution ──────────────────────────────────────────────────


def _llm_generate_sql(project_slug: str, question: str, workflow_name: str) -> str:
    """Generate SQL via LLM when step has no explicit query. Reads project schema
    via information_schema, asks training_llm_call for one SELECT statement.
    Returns '' on any failure (caller treats as no-sql)."""
    try:
        from dash.tools.metric_compiler import resolve_engine
        from dash.settings import training_llm_call
        from sqlalchemy import text as _t
        eng, schema = resolve_engine(project_slug)
        with eng.connect() as cn:
            rows = cn.execute(_t(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema=:s "
                "ORDER BY table_name, ordinal_position LIMIT 200"
            ), {"s": schema or "public"}).fetchall()
        if not rows:
            return ""
        tables: dict[str, list[str]] = {}
        for tbl, col, dt in rows:
            tables.setdefault(tbl, []).append(f"{col} ({dt})")
        schema_blob = "\n".join(
            f"  {t}: {', '.join(cs[:25])}" for t, cs in list(tables.items())[:30]
        )
        # SQL-VALIDATED: inject central Postgres rules + project schema hint at
        # the top of the prompt so the LLM produces dialect-correct, schema-aware
        # SQL on the first try.
        prompt_parts: list[str] = []
        if _pg_sql_rules is not None:
            try:
                rules = _pg_sql_rules() or ""
                if rules:
                    prompt_parts.append(rules)
            except Exception as _e:  # noqa: BLE001
                logger.warning("workflow_runner: _postgres_sql_rules() failed slug=%s: %s", project_slug, _e)
        if _get_schema_hint is not None:
            try:
                hint = _get_schema_hint(project_slug) or ""
                if hint:
                    prompt_parts.append(hint)
            except Exception as _e:  # noqa: BLE001
                logger.warning("workflow_runner: get_schema_hint failed slug=%s: %s", project_slug, _e)
        prompt_parts.append(
            f"You are a Postgres SQL author. Workflow: {workflow_name}\n"
            f"Question: {question}\n\n"
            f"Schema (table: columns):\n{schema_blob}\n\n"
            "Write ONE read-only SELECT statement (no CTEs writing, no DDL). "
            "Use only listed tables + columns. No markdown fences. SQL only:"
        )
        prompt = "\n\n".join(prompt_parts)

        raw = training_llm_call(prompt, "extraction") or ""
        sql = raw.strip()
        # Strip markdown fences if present
        if sql.startswith("```"):
            sql = sql.split("```", 2)[1] if sql.count("```") >= 2 else sql[3:]
            if sql.lower().startswith("sql"):
                sql = sql[3:].lstrip()
            sql = sql.split("```")[0].strip()
        # Reject anything not starting with SELECT/WITH
        head = sql.lstrip().lower()[:6]
        if not (head.startswith("select") or head.startswith("with")):
            return ""
        sql = sql.rstrip(";")

        # SQL-VALIDATED: route through central validator. On ok=True use the
        # (possibly auto-fixed) SQL. On ok=False return a sentinel that the
        # caller can detect to mark the workflow run as failed before exec.
        if _sql_validate_and_fix is not None:
            try:
                v = _sql_validate_and_fix(sql, project_slug, strict=True)
            except Exception as _e:  # noqa: BLE001
                logger.warning(
                    "workflow_runner: sql_validator raised slug=%s: %s; using unvalidated SQL",
                    project_slug, _e,
                )
                return sql
            if v.get("ok"):
                fixes = v.get("fixes_applied") or []
                if fixes:
                    logger.info(
                        "workflow_runner: SQL auto-fixed slug=%s fixes=%s", project_slug, fixes,
                    )
                return (v.get("sql") or sql).rstrip(";")
            # Bad SQL: don't execute on a cron. Encode validator errors into a
            # sentinel string the caller checks for.
            errs = v.get("errors") or ["unknown validation error"]
            logger.warning(
                "workflow_runner: SQL validation failed slug=%s errors=%s", project_slug, errs,
            )
            return f"__SQL_VALIDATION_FAILED__:{json.dumps(errs)[:1000]}"
        else:
            # Fail-soft: validator unavailable, log + run unvalidated as before.
            logger.warning(
                "workflow_runner: sql_validator missing, executing unvalidated SQL slug=%s",
                project_slug,
            )
            return sql
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_generate_sql failed slug=%s: %s", project_slug, e)
        return ""


def _exec_sql_step(project_slug: str, sql: str) -> dict:
    """Run one SQL step against the project schema (read-only, capped).
    Returns {ok, rows, columns, row_count, ms, error}."""
    t0 = time.time()
    try:
        from dash.tools.metric_compiler import resolve_engine
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"engine import failed: {e}", "ms": 0}

    try:
        from sqlalchemy import text as _text
        # Heuristic LIMIT injection — only for select-shaped queries lacking LIMIT.
        s = (sql or "").strip().rstrip(";")
        if s and " limit " not in s.lower():
            s = f"{s} LIMIT {SQL_ROW_CAP}"
        eng, _schema = resolve_engine(project_slug)
        with eng.connect() as cn:
            try:
                cn.execute(_text(f"SET LOCAL statement_timeout = {SQL_STATEMENT_TIMEOUT_MS}"))
            except Exception:
                pass
            res = cn.execute(_text(s))
            cols = [{"name": c, "dtype": ""} for c in res.keys()]
            rows = [dict(r._mapping) for r in res.fetchall()]
        ms = int((time.time() - t0) * 1000)
        return {"ok": True, "rows": rows, "columns": cols, "row_count": len(rows), "ms": ms}
    except Exception as e:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        return {"ok": False, "error": str(e)[:500], "ms": ms}


def _run_one_step(wf: dict, step: dict, run_id: str, step_index: int) -> dict:
    """Execute a single workflow step. Returns a result dict including chart spec
    via auto_visualize and an optional analysis_types dispatch."""
    project_slug = wf.get("project_slug") or ""
    sql = (step.get("sql") or step.get("resolved_query") or
           step.get("query_template") or wf.get("resolved_query") or
           wf.get("query_template") or "")
    analysis_type = step.get("analysis_type") or wf.get("analysis_type")
    title = step.get("title") or step.get("name") or wf.get("name") or "Step"
    question = step.get("question") or title

    out: dict[str, Any] = {
        "step_index": step_index,
        "title": title,
        "question": question,
        "sql": sql,
        "ok": False,
    }

    # LLM SQL generation fallback when step has no explicit SQL
    if not sql:
        try:
            sql = _llm_generate_sql(project_slug, question, wf.get("name") or "")
            if sql:
                # SQL-VALIDATED: detect validator-failure sentinel and short-circuit
                # without executing the bad SQL on the cron.
                if sql.startswith("__SQL_VALIDATION_FAILED__:"):
                    err_payload = sql[len("__SQL_VALIDATION_FAILED__:"):]
                    out["sql"] = ""
                    out["sql_source"] = "llm"
                    out["ok"] = False
                    out["error"] = f"sql_validation_failed: {err_payload}"[:500]
                    out["ms"] = 0
                    out["row_count"] = 0
                    out["columns"] = []
                    out["rows"] = []
                    return out
                out["sql"] = sql
                out["sql_source"] = "llm"
        except Exception as _e:  # noqa: BLE001
            out["sql_gen_error"] = str(_e)[:200]

    sql_res = _exec_sql_step(project_slug, sql) if sql else {"ok": False, "error": "no sql"}
    out.update({
        "ok": sql_res.get("ok", False),
        "ms": sql_res.get("ms", 0),
        "row_count": sql_res.get("row_count", 0),
        "columns": sql_res.get("columns", []),
        "rows": sql_res.get("rows", []),
        "error": sql_res.get("error"),
    })

    # Optional analysis dispatch
    if analysis_type and out["ok"]:
        try:
            from dash.tools.analysis_types import _ANALYZE_ROUTES, analyze
            fn = _ANALYZE_ROUTES.get(str(analysis_type).lower())
            if fn is not None:
                try:
                    narrative = fn(question, project_slug)  # legacy fn signature
                except TypeError:
                    narrative = analyze(str(analysis_type), question, project_slug)
                out["narrative"] = str(narrative or "")[:4000]
        except Exception as _e:  # noqa: BLE001
            out["narrative_error"] = str(_e)[:200]

    # Visualizer (always attempt, fail-soft)
    chart_type = None
    if out["ok"] and out["rows"]:
        try:
            from dash.tools.visualizer import auto_visualize
            tag = auto_visualize(question, project_slug, data=out["rows"])
            if isinstance(tag, str) and tag.startswith("[CHART_CONFIG:"):
                inner = tag[len("[CHART_CONFIG:"): -1] if tag.endswith("]") else tag[len("[CHART_CONFIG:"):]
                try:
                    spec = json.loads(inner)
                    out["chart"] = spec
                    chart_type = (spec or {}).get("type") or (spec or {}).get("chart_type")
                except Exception:
                    out["chart_raw"] = inner[:4000]
        except Exception as _e:  # noqa: BLE001
            out["chart_error"] = str(_e)[:200]

    # Emit trace span (best effort)
    try:
        with trace_span("workflow.step", project_slug=project_slug, meta={
            "run_id": run_id,
            "step_id": step.get("id", str(step_index)),
            "step_index": step_index,
            "rows_count": out.get("row_count", 0),
            "ms": out.get("ms", 0),
            "chart_type": chart_type,
        }):
            pass
    except Exception:
        pass

    return out


def _load_workflow(wf_id: int) -> dict | None:
    """Pull the workflow row + its steps[] (if column exists)."""
    eng = _engine()
    try:
        from sqlalchemy import text as _t
        with eng.connect() as cn:
            row = cn.execute(_t(
                "SELECT id, name, project_slug, query_template, resolved_query, "
                "action, owner_user_id FROM dash.dash_autonomous_workflows WHERE id=:id"
            ), {"id": wf_id}).mappings().first()
            if not row:
                return None
            wf = dict(row)
            # Try optional steps JSONB col (if a future migration adds it)
            try:
                steps_row = cn.execute(_t(
                    "SELECT steps FROM dash.dash_autonomous_workflows WHERE id=:id"
                ), {"id": wf_id}).first()
                steps = steps_row[0] if steps_row else None
            except Exception:
                steps = None
            if not steps or not isinstance(steps, list) or not steps:
                # Fall back: synthesize one step from resolved_query / query_template
                sql = wf.get("resolved_query") or wf.get("query_template") or ""
                steps = [{"id": "s1", "title": wf.get("name") or "Step 1", "sql": sql,
                          "question": wf.get("name") or "Workflow result"}]
            wf["steps"] = steps
            return wf
    except Exception as e:  # noqa: BLE001
        logger.warning("workflow_runner: load_workflow %s failed: %s", wf_id, e)
        return None
    finally:
        eng.dispose()


# ── claim + finish ──────────────────────────────────────────────────────


def _claim_one(run_id: str) -> dict | None:
    """Atomic claim: queued → running. Returns dict if won, None if lost."""
    eng = _engine()
    try:
        from sqlalchemy import text as _t
        with eng.begin() as cn:
            row = cn.execute(_t(
                "UPDATE dash.dash_workflow_run_history "
                "   SET status='running', started_at=NOW() "
                " WHERE run_id=:rid AND status='queued' "
                " RETURNING workflow_id, project_slug, owner_user_id, enqueued_at"
            ), {"rid": run_id}).mappings().first()
            return dict(row) if row else None
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow_runner: claim %s failed: %s", run_id, e)
        return None
    finally:
        eng.dispose()


def _select_queued(limit: int = CLAIM_BATCH) -> list[str]:
    eng = _engine()
    try:
        from sqlalchemy import text as _t
        with eng.connect() as cn:
            rows = cn.execute(_t(
                "SELECT run_id FROM dash.dash_workflow_run_history "
                "WHERE status='queued' ORDER BY enqueued_at ASC LIMIT :lim"
            ), {"lim": limit}).fetchall()
            return [r[0] for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow_runner: select_queued failed: %s", e)
        return []
    finally:
        eng.dispose()


def _finish_run(run_id: str, status: str, dashboard_id: str | None,
                output: Any, error: str | None, duration_ms: int,
                steps_completed: int, steps_total: int) -> None:
    eng = _engine()
    try:
        from sqlalchemy import text as _t
        out_json = None
        if output is not None:
            try:
                out_json = json.dumps(output, default=str)
            except Exception:
                out_json = json.dumps({"_warn": "non-serializable"})
        with eng.begin() as cn:
            # Split into two paths to avoid psycopg ambiguous-parameter on CASE+NULL+jsonb cast.
            if out_json is None:
                cn.execute(_t(
                    "UPDATE dash.dash_workflow_run_history "
                    "   SET status=:st, finished_at=NOW(), duration_ms=:dur, "
                    "       dashboard_id=:did, error=:err, "
                    "       steps_completed=:sc, steps_total=:stt "
                    " WHERE run_id=:rid"
                ), {
                    "st": status, "dur": duration_ms, "did": dashboard_id,
                    "err": error, "sc": steps_completed, "stt": steps_total,
                    "rid": run_id,
                })
            else:
                cn.execute(_t(
                    "UPDATE dash.dash_workflow_run_history "
                    "   SET status=:st, finished_at=NOW(), duration_ms=:dur, "
                    "       dashboard_id=:did, output=CAST(:out AS jsonb), "
                    "       error=:err, steps_completed=:sc, steps_total=:stt "
                    " WHERE run_id=:rid"
                ), {
                    "st": status, "dur": duration_ms, "did": dashboard_id,
                    "out": out_json, "err": error,
                    "sc": steps_completed, "stt": steps_total,
                    "rid": run_id,
                })
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow_runner: finish %s failed: %s", run_id, e)
    finally:
        eng.dispose()


# ── per-run pipeline ────────────────────────────────────────────────────


def _process_run(run_id: str) -> dict:
    t0 = time.time()
    claim = _claim_one(run_id)
    if not claim:
        return {"run_id": run_id, "claimed": False}

    wf_id = claim.get("workflow_id")
    if wf_id is None:
        logger.warning("workflow_runner: claim missing workflow_id, skipping run=%s", run_id)
        return {"run_id": run_id, "claimed": False, "error": "missing workflow_id"}
    wf = _load_workflow(wf_id) or {}
    project_slug = claim.get("project_slug") or wf.get("project_slug") or ""
    wf["project_slug"] = project_slug

    # notify start (fail-soft)
    try:
        from dash.notifications.workflow_hooks import notify_workflow_started
        notify_workflow_started(
            owner_user_id=claim.get("owner_user_id") or 0,
            workflow_name=wf.get("name") or f"workflow #{wf_id}",
            run_id=run_id,  # type: ignore[arg-type]
            source=claim.get("source") or "manual",
        )
    except Exception:
        pass

    # Build dashboard skeleton immediately so UI can subscribe (Task #7)
    # NOTE: dashboards_v2 lives in `public` schema — must use get_write_engine()
    # not get_sql_engine() (the latter blocks public writes via _guard_public_schema).
    dashboard_id_early: str | None = None
    _eng = None
    try:
        from dash.dashboards.from_workflow import ensure_dashboard_skeleton
        try:
            from db.session import get_write_engine
            _eng = get_write_engine()
        except Exception:
            _eng = None
        dashboard_id_early = ensure_dashboard_skeleton(run_id, wf, _eng)
    except Exception:
        logger.exception("workflow_runner: skeleton failed run=%s", run_id)
        dashboard_id_early = None

    steps = wf.get("steps") or []
    results: list[dict] = []
    steps_completed = 0
    for i, step in enumerate(steps):
        try:
            r = _run_one_step(wf, step, run_id, i)
            results.append(r)
            if r.get("ok"):
                steps_completed += 1
                # Incremental panel write + panel_ready trace emit (Task #7)
                if dashboard_id_early:
                    try:
                        from dash.dashboards.from_workflow import upsert_panel
                        new_count = upsert_panel(run_id, r, _eng)
                        try:
                            with trace_span("workflow.panel", project_slug=project_slug, meta={
                                "run_id": run_id,
                                "step_index": i,
                                "step_id": step.get("id", str(i)),
                                "panel_idx": new_count - 1 if new_count > 0 else 0,
                                "rows_count": r.get("row_count", 0),
                            }):
                                pass
                        except Exception:
                            pass
                    except Exception:
                        logger.exception(
                            "workflow_runner: upsert_panel failed run=%s step=%d",
                            run_id, i,
                        )
        except Exception as e:  # noqa: BLE001
            results.append({"step_index": i, "ok": False, "error": str(e)[:500]})

    dashboard_id: str | None = None
    error: str | None = None
    status = "failed"
    try:
        from dash.dashboards.from_workflow import build_dashboard_from_run
        if any(r.get("ok") for r in results):
            # Reuse the write engine we already have (or let from_workflow resolve)
            dashboard_id = build_dashboard_from_run(run_id, wf, results, _eng)
            if dashboard_id:
                status = "done"
            else:
                error = "dashboard build returned no id"
        else:
            error = "all steps failed"
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow_runner: dashboard build failed run=%s: %s", run_id, e)
        error = f"dashboard build failed: {e}"[:500]

    duration_ms = int((time.time() - t0) * 1000)
    _finish_run(
        run_id=run_id, status=status, dashboard_id=dashboard_id,
        output={"results": results, "n_steps": len(steps)},
        error=error, duration_ms=duration_ms,
        steps_completed=steps_completed, steps_total=len(steps),
    )

    # notify done / failed (fail-soft)
    try:
        if status == "done":
            from dash.notifications.workflow_hooks import notify_workflow_done
            notify_workflow_done(
                owner_user_id=claim.get("owner_user_id") or 0,
                workflow_name=wf.get("name") or f"workflow #{wf_id}",
                run_id=run_id,  # type: ignore[arg-type]
                dashboard_id=dashboard_id,
                duration_s=duration_ms / 1000.0,
                project_slug=project_slug,
            )
        else:
            from dash.notifications.workflow_hooks import notify_workflow_failed
            notify_workflow_failed(
                owner_user_id=claim.get("owner_user_id") or 0,
                workflow_name=wf.get("name") or f"workflow #{wf_id}",
                run_id=run_id,  # type: ignore[arg-type]
                error_msg=error or "unknown",
            )
    except Exception:
        pass

    return {"run_id": run_id, "claimed": True, "status": status,
            "dashboard_id": dashboard_id, "steps": len(steps),
            "steps_ok": steps_completed, "duration_ms": duration_ms}


def run_cycle() -> dict:
    """One tick: select queued runs, process each."""
    queued = _select_queued()
    if not queued:
        return {"queued": 0, "processed": 0, "errors": 0}
    processed = 0
    errors = 0
    for rid in queued:
        try:
            res = _process_run(rid)
            if res.get("claimed"):
                processed += 1
                if res.get("status") == "failed":
                    errors += 1
        except Exception:
            logger.exception("workflow_runner: process %s crashed", rid)
            errors += 1
    return {"queued": len(queued), "processed": processed, "errors": errors}


def reap_orphans(stale_minutes: int = 10) -> dict:
    """Mark runs stuck in 'running' beyond stale_minutes as 'failed' with
    error='orphaned (daemon outage or worker crash)'. Idempotent."""
    try:
        from sqlalchemy import text as _t
        eng = _engine()
        with eng.begin() as cn:
            res = cn.execute(_t(
                "UPDATE dash.dash_workflow_run_history "
                "   SET status='failed', "
                "       finished_at=COALESCE(finished_at, NOW()), "
                "       error=COALESCE(error, 'orphaned (daemon outage or worker crash)') "
                " WHERE status='running' "
                "   AND started_at < NOW() - make_interval(mins => :m) "
                " RETURNING run_id"
            ), {"m": stale_minutes})
            reaped = [r[0] for r in res.fetchall()]
        if reaped:
            logger.info("workflow_runner: reaped %d orphan run(s): %s",
                        len(reaped), reaped[:5])
        return {"reaped": len(reaped)}
    except Exception:
        logger.exception("workflow_runner: reap_orphans failed")
        return {"reaped": 0, "error": True}


async def workflow_runner_loop() -> None:
    if _is_disabled():
        logger.info("workflow_runner: disabled via env")
        return
    interval = _interval_seconds()
    logger.info("workflow_runner: starting (interval=%ds)", interval)
    _reap_counter = 0
    while True:
        try:
            with trace_span("cron.workflow_runner", kind="cron"):
                stats = await asyncio.to_thread(run_cycle)
            if stats.get("queued"):
                logger.info("workflow_runner: tick queued=%d processed=%d errors=%d",
                            stats.get("queued", 0), stats.get("processed", 0),
                            stats.get("errors", 0))
            # Reap orphans every 30 ticks (~5 min at 10s interval)
            _reap_counter += 1
            if _reap_counter >= 30:
                _reap_counter = 0
                try:
                    await asyncio.to_thread(reap_orphans, 10)
                except Exception:
                    logger.exception("workflow_runner: orphan reap failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow_runner: outer loop crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_cycle", "reap_orphans", "workflow_runner_loop"]
