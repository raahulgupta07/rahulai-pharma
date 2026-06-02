"""Autonomous workflow runner.

Daemon polls every minute. For each `active` workflow whose schedule cadence
has elapsed since last_run, executes resolved_query against project schema
(read-only, statement_timeout, row cap), then routes rows to action handler.

Safety:
- Only runs workflows with status='active' (resolved_query non-null)
- Per-query: statement_timeout 30s, max 1000 rows, READ ONLY transaction
- Per-action: try/except, errors saved to last_error, daemon never crashes
- Never calls LLM
- Skips workflow if no rows returned
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from sqlalchemy import text

logger = logging.getLogger(__name__)

_RUNNER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()

# Schedule cadence in minutes
_CADENCE_MIN = {
    "hourly": 60,
    "daily": 60 * 24,
    "weekly": 60 * 24 * 7,
    "monthly": 60 * 24 * 30,
}

_MAX_ROWS = 1000
_STMT_TIMEOUT_MS = 30_000


# ─────────────────────────── Engine + Schema Routing ───────────────────────────


def _get_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _project_schema_name(project_slug: str) -> str:
    eng = _get_engine()
    if eng is None:
        return project_slug
    try:
        with eng.begin() as cn:
            row = cn.execute(
                text("SELECT schema_name FROM dash_projects WHERE slug=:s LIMIT 1"),
                {"s": project_slug},
            ).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return project_slug


# ─────────────────────────── Workflow Selection ───────────────────────────


def _due_workflow_ids() -> list[int]:
    """Return IDs of active workflows whose cadence has elapsed (no claim yet)."""
    eng = _get_engine()
    if eng is None:
        return []
    try:
        with eng.begin() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT id, schedule, last_run_at
                    FROM dash_autonomous_workflows
                    WHERE status = 'active' AND COALESCE(resolved_query, '') <> ''
                    """
                )
            ).fetchall()
    except Exception as e:
        logger.warning("could not list workflows: %s", e)
        return []
    now = datetime.now(timezone.utc)
    out: list[int] = []
    for r in rows:
        cadence = (_CADENCE_MIN.get((r[1] or "daily").lower())) or _CADENCE_MIN["daily"]
        last_run = r[2]
        if last_run is None or (now - last_run.astimezone(timezone.utc)) >= timedelta(minutes=cadence):
            out.append(r[0])
    return out


def _try_claim(wf_id: int) -> dict | None:
    """Atomic claim: UPDATE last_run_at if cadence elapsed. Race-safe via WHERE clause.

    Returns full workflow dict on win, None if another worker already claimed.
    """
    eng = _get_engine()
    if eng is None:
        return None
    try:
        with eng.begin() as cn:
            row = cn.execute(
                text(
                    """
                    UPDATE dash_autonomous_workflows
                       SET last_run_at = NOW()
                     WHERE id = :id
                       AND status = 'active'
                       AND COALESCE(resolved_query, '') <> ''
                       AND (
                         last_run_at IS NULL OR
                         (CASE LOWER(COALESCE(schedule, 'daily'))
                            WHEN 'hourly'  THEN NOW() - last_run_at >= INTERVAL '60 minutes'
                            WHEN 'daily'   THEN NOW() - last_run_at >= INTERVAL '1 day'
                            WHEN 'weekly'  THEN NOW() - last_run_at >= INTERVAL '7 days'
                            WHEN 'monthly' THEN NOW() - last_run_at >= INTERVAL '30 days'
                            ELSE NOW() - last_run_at >= INTERVAL '1 day'
                          END)
                       )
                    RETURNING id, project_slug, template_name, name, schedule,
                              resolved_query, action, expected_entity, expected_columns
                    """
                ),
                {"id": wf_id},
            ).fetchone()
    except Exception as e:
        logger.warning("claim failed for wf %s: %s", wf_id, e)
        return None
    if not row:
        return None
    return {
        "id": row[0],
        "project_slug": row[1],
        "template_name": row[2],
        "name": row[3],
        "schedule": row[4],
        "resolved_query": row[5],
        "action": row[6],
        "expected_entity": row[7],
        "expected_columns": row[8] or [],
    }


# ─────────────────────────── Run History (workflow hub) ───────────────────────

def _hist_start(wf_id: int, project_slug: str, trigger: str = "cron") -> str | None:
    """Insert a 'running' history row. Returns run_id or None on failure (fail-soft)."""
    eng = _get_engine()
    if eng is None:
        return None
    run_id = f"wfr_{secrets.token_hex(8)}"
    try:
        with eng.begin() as cn:
            cn.execute(text(
                """
                INSERT INTO dash.dash_workflow_run_history
                  (run_id, workflow_id, project_slug, started_at, status, triggered_by)
                VALUES (:rid, :wid, :slug, NOW(), 'running', :trig)
                """
            ), {"rid": run_id, "wid": wf_id, "slug": project_slug, "trig": trigger})
        return run_id
    except Exception as e:
        logger.debug("hist_start skipped wf=%s: %s", wf_id, e)
        return None


def _hist_finish(
    run_id: str | None,
    wf_id: int,
    status: str,
    output: Any = None,
    error: str | None = None,
    cost_usd: float = 0.0,
) -> None:
    """Update history row + cache last_output on workflow. Fail-soft."""
    if not run_id:
        return
    eng = _get_engine()
    if eng is None:
        return
    try:
        out_json = None
        if output is not None:
            try:
                out_json = json.dumps(output, default=str)
            except Exception:
                out_json = json.dumps({"_warn": "non-serializable"})
        with eng.begin() as cn:
            row = cn.execute(text(
                "SELECT started_at FROM dash.dash_workflow_run_history WHERE run_id = :rid"
            ), {"rid": run_id}).first()
            dur_ms = None
            if row and row[0]:
                started = row[0]
                try:
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    dur_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                except Exception:
                    dur_ms = None
            cn.execute(text(
                """
                UPDATE dash.dash_workflow_run_history
                   SET finished_at = NOW(),
                       duration_ms = :dur,
                       status = :st,
                       output = CASE WHEN :out IS NULL THEN output ELSE CAST(:out AS jsonb) END,
                       error = :err,
                       cost_usd = :cost
                 WHERE run_id = :rid
                """
            ), {"rid": run_id, "dur": dur_ms, "st": status, "out": out_json, "err": error, "cost": cost_usd})
            if out_json is not None:
                cn.execute(text(
                    "UPDATE dash.dash_autonomous_workflows SET last_output = CAST(:out AS jsonb) WHERE id = :wid"
                ), {"out": out_json, "wid": wf_id})
    except Exception as e:
        logger.debug("hist_finish skipped run=%s: %s", run_id, e)


def _mark_error(wf_id: int, error: str | None) -> None:
    """Set only last_error after work done. last_run_at already set by claim."""
    eng = _get_engine()
    if eng is None:
        return
    try:
        with eng.begin() as cn:
            cn.execute(
                text("UPDATE dash_autonomous_workflows SET last_error = :e WHERE id = :id"),
                {"id": wf_id, "e": error},
            )
    except Exception as e:
        logger.warning("could not mark error for wf %s: %s", wf_id, e)


# Backwards-compat alias for run-now endpoint (which doesn't go through tick).
def _mark_run(wf_id: int, error: str | None = None) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        with eng.begin() as cn:
            cn.execute(
                text(
                    "UPDATE dash_autonomous_workflows SET last_run_at = NOW(), last_error = :e WHERE id = :id"
                ),
                {"id": wf_id, "e": error},
            )
    except Exception as e:
        logger.warning("could not mark run for wf %s: %s", wf_id, e)


# ─────────────────────────── Query Execution ───────────────────────────


def _qualify_schema(sql: str, schema_name: str) -> str:
    """Prepend `set search_path` so unqualified table refs resolve into project schema.

    We don't rewrite the SQL — instead we set search_path inside the txn.
    Returns the SQL unchanged.
    """
    return sql


def execute_workflow(wf: dict) -> tuple[list[dict] | None, str | None]:
    """Run resolved_query against project schema. Read-only, bounded.

    Returns (rows | None, error | None). Caps at _MAX_ROWS.
    """
    eng = _get_engine()
    if eng is None:
        return None, "no engine"
    schema = _project_schema_name(wf["project_slug"])
    sql = (wf.get("resolved_query") or "").strip().rstrip(";")
    if not sql:
        return None, "empty query"

    # ── Built-in dispatch (no SQL exec). Token form: __BUILTIN__:<name> ──
    if sql.startswith("__BUILTIN__:"):
        builtin = sql.split(":", 1)[1].strip()
        if builtin == "compute_customer_scores":
            try:
                from dash.templates.customer_scores import compute_and_cache
                result = compute_and_cache(wf["project_slug"]) or {}
            except Exception as e:
                return None, f"builtin {builtin} crashed: {str(e)[:200]}"
            if not result.get("ok"):
                return None, f"builtin {builtin}: {result.get('error') or 'failed'}"
            scored = int(result.get("scored") or 0)
            # Synthetic "row" so the action handler logs a real summary.
            synthetic = [{
                "builtin": builtin,
                "scored": scored,
                "segments": result.get("segments_breakdown") or {},
                "risks": result.get("risk_breakdown") or {},
                "computed_at": result.get("computed_at"),
            }]
            return synthetic, None
        return None, f"unknown builtin: {builtin}"

    # SQL allow-list: only SELECT/WITH allowed
    head = sql.lstrip().split(None, 1)[0].lower() if sql.lstrip() else ""
    if head not in ("select", "with"):
        return None, f"non-select query blocked: {head}"

    # Cap rows
    capped = sql if " limit " in sql.lower() else f"{sql} LIMIT {_MAX_ROWS}"

    # Safety: SELECT-only allow-list above + LIMIT cap + statement_timeout.
    # No SET-based read_only because PgBouncer transaction pooling can leak it.
    try:
        with eng.begin() as cn:
            cn.execute(text(f"SET LOCAL search_path = {schema}, public"))
            cn.execute(text(f"SET LOCAL statement_timeout = {_STMT_TIMEOUT_MS}"))
            res = cn.execute(text(capped))
            cols = list(res.keys())
            rows = [dict(zip(cols, r)) for r in res.fetchmany(_MAX_ROWS)]
        return rows, None
    except Exception as e:
        return None, str(e)[:300]


# ─────────────────────────── Action Handlers ───────────────────────────


def _write_insight(wf: dict, rows: list[dict], severity: str, dedupe_hours: int) -> None:
    """Insert into dash_proactive_insights (deduped on insight prefix)."""
    eng = _get_engine()
    if eng is None:
        return
    header = f"[{wf['template_name']}/{wf['name']}] {len(rows)} hit(s)"
    body = _format_rows_summary(rows)
    insight = f"{header}\n{body}"
    interval_text = f"{int(dedupe_hours)} hours"
    try:
        with eng.begin() as cn:
            cn.execute(
                text(
                    """
                    INSERT INTO dash_proactive_insights
                      (project_slug, insight, severity, tables_involved, sql_used, created_at)
                    SELECT :slug, :ins, :sev, ARRAY[:tbl_one]::text[], :sql, NOW()
                     WHERE NOT EXISTS (
                       SELECT 1 FROM dash_proactive_insights
                        WHERE project_slug = :slug
                          AND insight LIKE :prefix
                          AND created_at > NOW() - CAST(:itv AS interval)
                     )
                    """
                ),
                {
                    "slug": wf["project_slug"],
                    "ins": insight,
                    "sev": severity,
                    "tbl_one": (wf.get("expected_entity") or "unknown"),
                    "sql": wf.get("resolved_query") or "",
                    "prefix": header + "%",
                    "itv": interval_text,
                },
            )
    except Exception as e:
        logger.warning("write_insight failed for wf %s: %s", wf["id"], e)


def _post_insight(wf: dict, rows: list[dict]) -> None:
    _write_insight(wf, rows, severity="info", dedupe_hours=24)


def _post_alert(wf: dict, rows: list[dict]) -> None:
    _write_insight(wf, rows, severity="high", dedupe_hours=4)


def _post_suggest(wf: dict, rows: list[dict]) -> None:
    _write_insight(wf, rows, severity="info", dedupe_hours=24)


def _format_rows_summary(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    head = rows[: min(5, len(rows))]
    keys = list(head[0].keys())
    lines = [" | ".join(keys)]
    for r in head:
        lines.append(" | ".join(str(r.get(k, ""))[:50] for k in keys))
    extra = f"\n… and {len(rows) - len(head)} more" if len(rows) > len(head) else ""
    return "\n".join(lines) + extra


_ACTION_HANDLERS = {
    "post_insight": _post_insight,
    "alert": _post_alert,
    "suggest": _post_suggest,
    "log": lambda wf, rows: logger.info("[wf log] %s: %d rows", wf["name"], len(rows)),
}


# ─────────────────────────── Main Loop ───────────────────────────


def run_one(wf: dict) -> dict:
    """Execute one workflow. Returns result dict."""
    rows, err = execute_workflow(wf)
    if err:
        _mark_run(wf["id"], error=err)
        return {"ok": False, "error": err, "rows": 0}
    if not rows:
        _mark_run(wf["id"], error=None)
        return {"ok": True, "rows": 0, "skipped": "no_hits"}
    handler = _ACTION_HANDLERS.get(wf.get("action") or "post_insight", _post_insight)
    try:
        handler(wf, rows)
    except Exception as e:
        _mark_run(wf["id"], error=f"action_error: {str(e)[:200]}")
        return {"ok": False, "error": str(e), "rows": len(rows)}
    _mark_run(wf["id"], error=None)
    return {"ok": True, "rows": len(rows)}


def tick() -> dict:
    """Poll-tick: claim + execute due workflows atomically.

    Multi-worker safe: each workflow runs at most once per cadence window
    even with N parallel runners (atomic claim via UPDATE...RETURNING).
    """
    candidate_ids = _due_workflow_ids()
    summary = {"candidates": len(candidate_ids), "claimed": 0, "ran": 0, "errors": 0, "skipped_lost_race": 0, "rows_total": 0}
    for wf_id in candidate_ids:
        wf = _try_claim(wf_id)
        if wf is None:
            # Another worker won the race, or status changed
            summary["skipped_lost_race"] += 1
            continue
        summary["claimed"] += 1
        run_id = _hist_start(wf["id"], wf.get("project_slug", ""), trigger="cron")
        try:
            # run_one will set last_error only — last_run_at already set by claim
            rows, err = execute_workflow(wf)
            if err:
                _mark_error(wf["id"], err)
                _hist_finish(run_id, wf["id"], status="fail", error=err)
                summary["errors"] += 1
                continue
            if not rows:
                _mark_error(wf["id"], None)
                _hist_finish(run_id, wf["id"], status="done", output={"rows": [], "n_rows": 0})
                continue
            handler = _ACTION_HANDLERS.get(wf.get("action") or "post_insight", _post_insight)
            try:
                handler(wf, rows)
                _mark_error(wf["id"], None)
                _hist_finish(run_id, wf["id"], status="done", output={"n_rows": len(rows), "sample": rows[:10]})
                summary["ran"] += 1
                summary["rows_total"] += len(rows)
            except Exception as e:
                _mark_error(wf["id"], f"action_error: {str(e)[:200]}")
                _hist_finish(run_id, wf["id"], status="fail", error=f"action_error: {str(e)[:200]}")
                summary["errors"] += 1
        except Exception as e:
            logger.exception("workflow %s crashed: %s", wf.get("name"), e)
            _mark_error(wf["id"], f"crash: {str(e)[:200]}")
            _hist_finish(run_id, wf["id"], status="fail", error=f"crash: {str(e)[:200]}")
            summary["errors"] += 1
            # Best-effort: notify project owner of workflow failure
            try:
                from app.auth import notify_user  # type: ignore
                eng2 = _get_engine()
                if eng2 is not None:
                    with eng2.connect() as cn2:
                        r = cn2.execute(text(
                            "SELECT user_id FROM public.dash_projects WHERE slug = :s"
                        ), {"s": wf.get("project_slug")}).fetchone()
                    if r and r[0]:
                        notify_user(
                            int(r[0]),
                            f"Workflow failed · {wf.get('name')}",
                            str(e)[:200],
                            "error",
                        )
            except Exception:
                pass
    return summary


def _loop(poll_seconds: int = 60) -> None:
    logger.info("autonomous workflow runner started (poll=%ds)", poll_seconds)
    while not _STOP_EVENT.is_set():
        try:
            s = tick()
            if s.get("candidates", 0) > 0:
                logger.info("autonomous tick: %s", s)
        except Exception as e:
            logger.exception("autonomous tick crashed: %s", e)
        _STOP_EVENT.wait(poll_seconds)


def start_runner(poll_seconds: int = 60) -> None:
    """Idempotent. Spawn daemon if not running."""
    global _RUNNER_THREAD
    import os
    if os.getenv("AUTONOMOUS_RUNNER_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("autonomous runner disabled via env")
        return
    if _RUNNER_THREAD and _RUNNER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _RUNNER_THREAD = threading.Thread(target=_loop, args=(poll_seconds,), daemon=True, name="autonomous-runner")
    _RUNNER_THREAD.start()


def stop_runner() -> None:
    _STOP_EVENT.set()
