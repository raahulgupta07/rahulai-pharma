"""
SQL Cost Pre-Flight Guard
=========================

EXPLAIN-based cost estimation run BEFORE executing read queries, so the agent
can be told to refine an expensive query (add filters / LIMIT) instead of
hammering the warehouse. Mirrors OpenAI's data-agent guard against expensive
queries — we already have hard row caps + statement_timeout, this adds a
plan-cost estimate.

Design rules:
- ONLY EXPLAIN read statements (SELECT / WITH). Non-read statements skip the
  guard entirely (return ok=True).
- FAIL-OPEN: any error during EXPLAIN / parsing → ok=True. We never block
  legitimate work because the guard itself broke.
- Thresholds configurable via env: SQL_COST_MAX, SQL_ROWS_MAX.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Plan-cost units (Postgres "Total Cost") and estimated plan rows.
_DEFAULT_COST_MAX = 5_000_000.0
_DEFAULT_ROWS_MAX = 5_000_000


def _cost_max() -> float:
    raw = os.getenv("SQL_COST_MAX")
    if not raw:
        return _DEFAULT_COST_MAX
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_COST_MAX


def _rows_max() -> int:
    raw = os.getenv("SQL_ROWS_MAX")
    if not raw:
        return _DEFAULT_ROWS_MAX
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_ROWS_MAX


def _is_read_statement(sql: str) -> bool:
    """True only for SELECT / WITH read statements."""
    if not sql:
        return False
    s = sql.strip()
    # Strip leading line comments and blank lines.
    lines = []
    for ln in s.splitlines():
        stripped = ln.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(ln)
    s = "\n".join(lines).strip()
    if not s:
        return False
    head = s[:6].upper()
    return head.startswith("SELECT") or head.startswith("WITH")


def _walk_plan(node, agg: dict) -> None:
    """Recursively collect Total Cost (max), Plan Rows (max), and seq-scan flag.

    Handles both the dict-shaped plan node and arbitrary nesting via the
    ``Plans`` child list.
    """
    if not isinstance(node, dict):
        return
    tc = node.get("Total Cost")
    if isinstance(tc, (int, float)):
        agg["total_cost"] = max(agg["total_cost"], float(tc))
    pr = node.get("Plan Rows")
    if isinstance(pr, (int, float)):
        agg["est_rows"] = max(agg["est_rows"], int(pr))
    node_type = node.get("Node Type") or ""
    if isinstance(node_type, str) and "Seq Scan" in node_type:
        # Treat a sizeable sequential scan as a full scan signal.
        rel_rows = node.get("Plan Rows")
        if not isinstance(rel_rows, (int, float)) or rel_rows >= 100_000:
            agg["full_scan"] = True
    children = node.get("Plans")
    if isinstance(children, list):
        for child in children:
            _walk_plan(child, agg)


def estimate_query_cost(engine, sql: str) -> dict:
    """Run EXPLAIN (FORMAT JSON) and return a cost estimate.

    Returns dict: {ok: bool, total_cost: float, est_rows: int,
                   full_scan: bool, reason: str | None}.

    FAIL-OPEN: if EXPLAIN errors (parse, permission, non-PG dialect, etc.) the
    result is ok=True with a reason describing the skip — never block.
    """
    result = {
        "ok": True,
        "total_cost": 0.0,
        "est_rows": 0,
        "full_scan": False,
        "reason": None,
    }

    if not _is_read_statement(sql):
        result["reason"] = "not a read statement; guard skipped"
        return result

    try:
        from sqlalchemy import text as _text

        explain_sql = "EXPLAIN (FORMAT JSON) " + sql.strip().rstrip(";")
        with engine.connect() as conn:
            row = conn.execute(_text(explain_sql)).fetchone()

        if not row:
            result["reason"] = "EXPLAIN returned no rows; guard skipped"
            return result

        plan_payload = row[0]
        # psycopg returns JSON either pre-parsed (list/dict) or as a string.
        if isinstance(plan_payload, str):
            import json as _json
            plan_payload = _json.loads(plan_payload)

        # The JSON plan may be a list of {"Plan": {...}} dicts OR a bare dict.
        plan_nodes = []
        if isinstance(plan_payload, list):
            for item in plan_payload:
                if isinstance(item, dict) and "Plan" in item:
                    plan_nodes.append(item["Plan"])
                elif isinstance(item, dict):
                    plan_nodes.append(item)
        elif isinstance(plan_payload, dict):
            plan_nodes.append(plan_payload.get("Plan", plan_payload))

        agg = {"total_cost": 0.0, "est_rows": 0, "full_scan": False}
        for pn in plan_nodes:
            _walk_plan(pn, agg)

        result["total_cost"] = agg["total_cost"]
        result["est_rows"] = agg["est_rows"]
        result["full_scan"] = agg["full_scan"]

        cost_max = _cost_max()
        rows_max = _rows_max()

        if agg["total_cost"] > cost_max:
            result["ok"] = False
            result["reason"] = (
                f"estimated plan cost {agg['total_cost']:,.0f} exceeds budget "
                f"{cost_max:,.0f}"
            )
        elif agg["est_rows"] > rows_max:
            result["ok"] = False
            result["reason"] = (
                f"estimated rows {agg['est_rows']:,} exceeds budget {rows_max:,}"
            )

        if not result["ok"]:
            logger.info(
                "SQL cost guard flagged query: cost=%.0f rows=%d full_scan=%s",
                agg["total_cost"], agg["est_rows"], agg["full_scan"],
            )
        return result

    except Exception as e:  # noqa: BLE001 — fail-open on ANY guard failure
        logger.debug("SQL cost guard EXPLAIN failed (fail-open): %s", e)
        result["ok"] = True
        result["reason"] = f"guard skipped (EXPLAIN error: {str(e)[:120]})"
        return result


def guard_or_note(engine, sql: str) -> str | None:
    """Return None if the query is fine to run, else a short human warning.

    Fail-soft: any internal error → None (allow query).
    """
    try:
        est = estimate_query_cost(engine, sql)
        if est.get("ok", True):
            return None
        rows = est.get("est_rows", 0)
        cost = est.get("total_cost", 0.0)
        return (
            f"Query estimated to scan ~{rows:,} rows / cost {cost:,.0f} — "
            "refine with filters/LIMIT before running."
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("guard_or_note failed (fail-soft): %s", e)
        return None
