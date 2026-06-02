"""DuckDB merge engine -- register subquery DataFrames + run final JOIN.

DuckDB allows querying pandas DataFrames as virtual tables. Register each
per-source DataFrame, then run the merge SQL against DuckDB.

Pros: real SQL JOIN semantics, fast.
Cons: requires DuckDB. Falls back to merge_pandas.py if absent.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    df: Any = None              # final pandas DataFrame
    row_count: int = 0
    duration_ms: int = 0
    engine_used: str = "duckdb"
    error: Optional[str] = None
    warnings: list = field(default_factory=list)


def merge(
    plan: Any,
    execution_result: Any,
    *,
    max_final_rows: int = 50_000,
) -> MergeResult:
    """Build + run merge SQL inside DuckDB.

    plan: SplitPlan
    execution_result: ExecutionResult (per_source: pid -> DataFrame)
    """
    result = MergeResult()

    try:
        import duckdb  # noqa: F401
        import pandas as pd  # noqa: F401
    except ImportError as e:
        result.error = f"duckdb/pandas not installed: {e}"
        return result

    if not execution_result.per_source:
        result.error = "no per-source results to merge"
        return result

    # Single source -- return its DataFrame directly (no need to pay
    # registration + parse cost on a no-op join).
    if len(execution_result.per_source) == 1:
        pid, df = next(iter(execution_result.per_source.items()))
        truncated = df.head(max_final_rows)
        result.df = truncated
        result.row_count = len(truncated)
        result.engine_used = "single_source_passthrough"
        return result

    import duckdb

    t0 = time.time()
    con = None
    try:
        con = duckdb.connect(":memory:")

        # Register each per-source DataFrame as a DuckDB virtual table,
        # keyed by a sanitized form of the provider id.
        for pid, df in execution_result.per_source.items():
            safe_pid = _sanitize_identifier(pid)
            con.register(safe_pid, df)

        merge_sql = _build_merge_sql(plan, execution_result, max_final_rows)
        logger.debug("duckdb merge sql: %s", merge_sql)

        result_df = con.execute(merge_sql).fetchdf()

        result.df = result_df
        result.row_count = len(result_df)
        result.duration_ms = int((time.time() - t0) * 1000)
        return result
    except Exception as e:
        result.error = f"duckdb merge: {str(e)[:300]}"
        result.duration_ms = int((time.time() - t0) * 1000)
        return result
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _build_merge_sql(plan: Any, execution_result: Any, max_rows: int) -> str:
    """Build SELECT ... FROM A JOIN B ON ... using plan.join_keys."""
    pids = list(execution_result.per_source.keys())
    if not pids:
        return "SELECT 1 LIMIT 0"

    pid_aliases = {pid: _sanitize_identifier(pid) for pid in pids}

    # Start from the first provider; chain JOINs based on join_keys we can
    # connect to providers we've already pulled in.
    first_alias = pid_aliases[pids[0]]
    from_parts: list[str] = [f"{first_alias} AS {first_alias}"]
    used: set[str] = {pids[0]}

    pending_keys = list(getattr(plan, "join_keys", []) or [])
    while pending_keys:
        progressed = False
        for jk in list(pending_keys):
            lp, rp = jk.left_provider, jk.right_provider
            lc, rc = jk.left_column, jk.right_column
            op = getattr(jk, "op", "=") or "="

            if lp in used and rp not in used:
                la = pid_aliases.get(lp, _sanitize_identifier(lp))
                ra = pid_aliases.setdefault(rp, _sanitize_identifier(rp))
                from_parts.append(
                    f"JOIN {ra} AS {ra} ON {la}.{lc} {op} {ra}.{rc}"
                )
                used.add(rp)
                pending_keys.remove(jk)
                progressed = True
            elif rp in used and lp not in used:
                la = pid_aliases.setdefault(lp, _sanitize_identifier(lp))
                ra = pid_aliases.get(rp, _sanitize_identifier(rp))
                from_parts.append(
                    f"JOIN {la} AS {la} ON {ra}.{rc} {op} {la}.{lc}"
                )
                used.add(lp)
                pending_keys.remove(jk)
                progressed = True
            elif lp in used and rp in used:
                # Both already joined; treat as a residual filter -- skip.
                pending_keys.remove(jk)
                progressed = True
        if not progressed:
            break

    # Any remaining unused providers get cross-joined (cartesian fallback).
    for pid in pids:
        if pid not in used:
            alias = pid_aliases[pid]
            from_parts.append(f"CROSS JOIN {alias} AS {alias}")
            used.add(pid)

    select_clause = getattr(plan, "final_select", "") or "*"
    final_where = getattr(plan, "final_where", "") or ""
    final_order = getattr(plan, "final_order_by", "") or ""
    final_limit = getattr(plan, "final_limit", None)

    where_clause = f" WHERE {final_where}" if final_where else ""
    order_clause = f" {final_order}" if final_order else ""
    limit_n = min(final_limit or max_rows, max_rows)
    limit_clause = f" LIMIT {limit_n}"

    return (
        f"SELECT {select_clause} FROM "
        + " ".join(from_parts)
        + where_clause
        + order_clause
        + limit_clause
    )


def _sanitize_identifier(name: str) -> str:
    """DuckDB table name -- keep alphanum + underscore."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name or "")
    if not safe or safe[0].isdigit():
        safe = "t_" + safe
    return safe
