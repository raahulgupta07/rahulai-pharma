"""EDA drill-down tools for the Dash Analyst agent.

Three dimension drill-down tools — `inspect_dimension`, `inspect_cross_dim`,
`inspect_time` — that the agent calls instead of writing raw SQL when the user
asks for full-cardinality dimension lists, cross-dim breakdowns, or time
trends. The agent's prompt already carries a compact dimension summary
(top-10 values per column); these tools are the on-demand deep dive.

Design rules (frozen contract; integrator depends on these signatures):
- All reads via `db.session.get_sql_engine()` (cached, read-only).
- Tenant scoping: schema = `_safe_ident(project_slug)`. All queries qualified.
- Fail-soft: tools NEVER raise — they return `{... "error": str}` on failure.
- 30s per-query timeout via `SET LOCAL statement_timeout = 30000`.
- Cached fast path (when available) for `inspect_dimension(top_n<=10)` reads
  the table profile cached in `public.dash_table_metadata.metadata.profile_v2`.
- Kill switch: `EDA_TOOLS_DISABLED=1` → `create_eda_tools` returns [].
- Use sqlalchemy `text()` with bind params; identifiers are pre-sanitised via
  `_safe_ident` before being f-stringed into the SQL.

`create_eda_tools(project_slug)` returns a list of Agno `@tool`-decorated
callables with `project_slug` pre-bound via closure.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_MAX_TOP_N_DIM = 200
_MAX_TOP_N_XDIM = 100
_STMT_TIMEOUT_MS = 30000
_HIGH_CARD_THRESHOLD = 10000
_COMBO_PROBE_LIMIT = 100_000

_VALID_GRANULARITIES = {"day", "week", "month", "quarter", "year"}

_DOW_MAP = {
    "Mon": 0, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 0, "Sat": 0, "Sun": 0,
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _safe_ident(name: str) -> str:
    """Lowercase, alphanum + underscore only, ≤63 chars. Postgres-safe."""
    if not name:
        return ""
    s = re.sub(r"[^a-z0-9_]", "_", str(name).lower())
    return s[:63]


def _get_eng():
    """Return cached read-only SQL engine. Raises on import failure."""
    from db.session import get_sql_engine
    return get_sql_engine()


def _qualified(slug: str, table: str) -> str:
    """Return double-quoted `"schema"."table"`. Both pre-sanitised."""
    schema = _safe_ident(slug)
    tbl = _safe_ident(table)
    return f'"{schema}"."{tbl}"'


def _record_telemetry(
    tool_name: str,
    args: dict,
    *,
    success: bool,
    result: Any = None,
    session_id: str = "global",
) -> None:
    """Best-effort ToolGuardrail telemetry emit. Swallows all errors."""
    try:
        from dash.runtime.tool_guardrail import record
        record(tool_name, args, success=success, result=result, session_id=session_id)
    except Exception:
        pass


def _read_cached_top_values(
    slug: str, table: str, column: str
) -> list | None:
    """Return cached `top_values` list from profile_v2 for (slug, table, column),
    or None if not cached / not profiled."""
    try:
        eng = _get_eng()
        with eng.connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {_STMT_TIMEOUT_MS}"))
            row = conn.execute(
                text(
                    "SELECT metadata #> '{profile_v2, columns}' AS cols "
                    "FROM public.dash_table_metadata "
                    "WHERE project_slug = :s AND table_name = :t"
                ),
                {"s": slug, "t": table},
            ).fetchone()
        if not row or not row[0]:
            return None
        cols = row[0]
        if not isinstance(cols, list):
            return None
        for c in cols:
            if not isinstance(c, dict):
                continue
            if c.get("name") == column or c.get("column") == column:
                tv = c.get("top_values")
                if isinstance(tv, list):
                    return tv
        return None
    except Exception as e:
        logger.debug(f"[eda] cache read failed: {e}")
        return None


# --------------------------------------------------------------------------- #
# inspect_dimension
# --------------------------------------------------------------------------- #
def inspect_dimension(
    table: str,
    column: str,
    top_n: int = 50,
    project_slug: str | None = None,
) -> dict:
    """Return top-N values + frequencies for a dimension column.

    Returns:
        {
          "values": [{"v": str, "count": int, "freq_pct": float,
                      "cumulative_pct": float}],
          "total_distinct": int,
          "shown": int,
          "source": "cache" | "query",
          "warning": str | None,
        }
    """
    args = {"table": table, "column": column, "top_n": top_n, "project_slug": project_slug}
    try:
        if not project_slug:
            return {"values": [], "error": "project_slug required"}

        try:
            top_n = int(top_n)
        except Exception:
            top_n = 50
        top_n = max(1, min(_MAX_TOP_N_DIM, top_n))

        col = _safe_ident(column)
        if not col:
            return {"values": [], "error": "invalid column name"}

        # Fast path: profile cache for small top_n
        if top_n <= 10:
            cached = _read_cached_top_values(project_slug, table, column)
            if cached:
                # Trim, compute cumulative
                trimmed = cached[: top_n]
                total = sum(int(v.get("count", 0)) for v in trimmed) or 1
                # Use stored freq_pct when present; recompute cumulative
                vals = []
                cum = 0.0
                for v in trimmed:
                    count = int(v.get("count", 0))
                    freq_pct = float(v.get("freq_pct", (count * 100.0 / total)))
                    cum += freq_pct
                    vals.append(
                        {
                            "v": str(v.get("v") or v.get("value") or ""),
                            "count": count,
                            "freq_pct": round(freq_pct, 2),
                            "cumulative_pct": round(min(cum, 100.0), 2),
                        }
                    )
                result = {
                    "values": vals,
                    "total_distinct": len(cached),
                    "shown": len(vals),
                    "source": "cache",
                    "warning": None,
                }
                _record_telemetry("inspect_dimension", args, success=True, result=result)
                return result

        # Slow path
        qual = _qualified(project_slug, table)
        with _get_eng().connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {_STMT_TIMEOUT_MS}"))
            # total_distinct + total_count (cap distinct probe at threshold +1)
            stats = conn.execute(
                text(
                    f'SELECT COUNT(DISTINCT "{col}") AS distinct_ct, COUNT(*) AS total_ct '
                    f"FROM {qual} WHERE \"{col}\" IS NOT NULL"
                )
            ).fetchone()
            total_distinct = int(stats[0]) if stats and stats[0] is not None else 0
            total_count = int(stats[1]) if stats and stats[1] is not None else 0

            rows = conn.execute(
                text(
                    f'SELECT "{col}" AS v, COUNT(*) AS freq '
                    f"FROM {qual} WHERE \"{col}\" IS NOT NULL "
                    f"GROUP BY 1 ORDER BY 2 DESC LIMIT :n"
                ),
                {"n": top_n},
            ).fetchall()

        values = []
        cum = 0.0
        denom = total_count or 1
        for r in rows:
            count = int(r[1])
            freq_pct = count * 100.0 / denom
            cum += freq_pct
            values.append(
                {
                    "v": str(r[0]) if r[0] is not None else "",
                    "count": count,
                    "freq_pct": round(freq_pct, 2),
                    "cumulative_pct": round(min(cum, 100.0), 2),
                }
            )

        warning = None
        if total_distinct > _HIGH_CARD_THRESHOLD:
            warning = (
                f"very high cardinality ({total_distinct} distinct values) — "
                "consider filtering or aggregating before drill-down"
            )

        result = {
            "values": values,
            "total_distinct": total_distinct,
            "shown": len(values),
            "source": "query",
            "warning": warning,
        }
        _record_telemetry("inspect_dimension", args, success=True, result=result)
        return result
    except Exception as e:
        logger.warning(f"[eda] inspect_dimension failed: {e}")
        _record_telemetry("inspect_dimension", args, success=False)
        return {"values": [], "error": str(e)[:200]}


# --------------------------------------------------------------------------- #
# inspect_cross_dim
# --------------------------------------------------------------------------- #
def inspect_cross_dim(
    table: str,
    dim_a: str,
    dim_b: str,
    top_n: int = 20,
    project_slug: str | None = None,
) -> dict:
    """Return top-N (dim_a, dim_b) combinations with counts + pct of total.

    Returns:
        {
          "cells": [{"a": str, "b": str, "count": int, "pct_of_total": float}],
          "total_combos_approx": int,
          "total_rows": int,
          "sparsity_warning": str | None,
        }
    """
    args = {
        "table": table,
        "dim_a": dim_a,
        "dim_b": dim_b,
        "top_n": top_n,
        "project_slug": project_slug,
    }
    try:
        if not project_slug:
            return {"cells": [], "error": "project_slug required"}

        try:
            top_n = int(top_n)
        except Exception:
            top_n = 20
        top_n = max(1, min(_MAX_TOP_N_XDIM, top_n))

        a = _safe_ident(dim_a)
        b = _safe_ident(dim_b)
        if not a or not b:
            return {"cells": [], "error": "invalid column name"}

        qual = _qualified(project_slug, table)
        with _get_eng().connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {_STMT_TIMEOUT_MS}"))

            total_row = conn.execute(
                text(f"SELECT COUNT(*) FROM {qual}")
            ).fetchone()
            total_rows = int(total_row[0]) if total_row and total_row[0] is not None else 0

            rows = conn.execute(
                text(
                    f'SELECT "{a}" AS a, "{b}" AS b, COUNT(*) AS freq '
                    f"FROM {qual} "
                    f'WHERE "{a}" IS NOT NULL AND "{b}" IS NOT NULL '
                    f"GROUP BY 1, 2 ORDER BY 3 DESC LIMIT :n"
                ),
                {"n": top_n},
            ).fetchall()

            # Approx total distinct combos via capped subquery
            combo_row = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ("
                    f'SELECT 1 FROM {qual} '
                    f'WHERE "{a}" IS NOT NULL AND "{b}" IS NOT NULL '
                    f'GROUP BY "{a}", "{b}" LIMIT :p'
                    ") s"
                ),
                {"p": _COMBO_PROBE_LIMIT},
            ).fetchone()
            total_combos_approx = int(combo_row[0]) if combo_row and combo_row[0] is not None else 0

        denom = total_rows or 1
        cells = []
        for r in rows:
            count = int(r[2])
            cells.append(
                {
                    "a": str(r[0]) if r[0] is not None else "",
                    "b": str(r[1]) if r[1] is not None else "",
                    "count": count,
                    "pct_of_total": round(count * 100.0 / denom, 2),
                }
            )

        sparsity_warning = None
        if total_combos_approx == 1 and len(cells) == 1:
            sparsity_warning = (
                f"{dim_a} always maps to a single {dim_b} value — these dimensions "
                "are perfectly correlated; cross-breakdown adds no information"
            )
        elif total_combos_approx >= _COMBO_PROBE_LIMIT:
            sparsity_warning = (
                f"≥{_COMBO_PROBE_LIMIT} distinct combinations (probe capped) — "
                "cross-breakdown will be very sparse"
            )

        result = {
            "cells": cells,
            "total_combos_approx": total_combos_approx,
            "total_rows": total_rows,
            "sparsity_warning": sparsity_warning,
        }
        _record_telemetry("inspect_cross_dim", args, success=True, result=result)
        return result
    except Exception as e:
        logger.warning(f"[eda] inspect_cross_dim failed: {e}")
        _record_telemetry("inspect_cross_dim", args, success=False)
        return {"cells": [], "error": str(e)[:200]}


# --------------------------------------------------------------------------- #
# inspect_time
# --------------------------------------------------------------------------- #
def _estimate_total_periods(min_d: date, max_d: date, granularity: str) -> int:
    """Theoretical period count between min/max date at given granularity."""
    if not min_d or not max_d or max_d < min_d:
        return 0
    days = (max_d - min_d).days + 1
    if granularity == "day":
        return days
    if granularity == "week":
        return max(1, days // 7 + (1 if days % 7 else 0))
    if granularity == "month":
        return (max_d.year - min_d.year) * 12 + (max_d.month - min_d.month) + 1
    if granularity == "quarter":
        ymin = min_d.year * 4 + (min_d.month - 1) // 3
        ymax = max_d.year * 4 + (max_d.month - 1) // 3
        return ymax - ymin + 1
    if granularity == "year":
        return max_d.year - min_d.year + 1
    return 0


def _iso(v) -> str:
    """Render date/datetime as iso string."""
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def inspect_time(
    table: str,
    date_col: str,
    granularity: str = "month",
    project_slug: str | None = None,
) -> dict:
    """Return time-range, period counts (top 24 most recent), missing-period
    estimate, and day-of-week distribution (for day/week granularities).

    Returns:
        {
          "range": {"min": iso_str, "max": iso_str},
          "period_count": int,
          "period_counts": [{"period": iso_str, "count": int}],
          "missing_periods_estimate": int,
          "dow_distribution": {"Mon": int, ..., "Sun": int} | None,
          "warning": str | None,
        }
    """
    args = {
        "table": table,
        "date_col": date_col,
        "granularity": granularity,
        "project_slug": project_slug,
    }
    try:
        if not project_slug:
            return {"period_counts": [], "error": "project_slug required"}

        gran = (granularity or "month").lower().strip()
        if gran not in _VALID_GRANULARITIES:
            return {
                "period_counts": [],
                "error": f"invalid granularity '{granularity}'; must be one of {sorted(_VALID_GRANULARITIES)}",
            }

        col = _safe_ident(date_col)
        if not col:
            return {"period_counts": [], "error": "invalid date_col name"}

        qual = _qualified(project_slug, table)
        warning = None

        # Build cast expression — try ::date first, fall back to raw column.
        # We do this by trying the cast variant and on error retrying with raw.
        def _run(cast: bool) -> dict:
            expr = f'("{col}")::date' if cast else f'"{col}"'
            with _get_eng().connect() as conn:
                conn.execute(text(f"SET LOCAL statement_timeout = {_STMT_TIMEOUT_MS}"))

                rr = conn.execute(
                    text(
                        f"SELECT MIN({expr}), MAX({expr}) FROM {qual} "
                        f'WHERE "{col}" IS NOT NULL'
                    )
                ).fetchone()
                min_d, max_d = (rr[0], rr[1]) if rr else (None, None)

                rows = conn.execute(
                    text(
                        f"SELECT date_trunc(:g, {expr})::date AS period, COUNT(*) AS ct "
                        f"FROM {qual} "
                        f'WHERE "{col}" IS NOT NULL '
                        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 24"
                    ),
                    {"g": gran},
                ).fetchall()

                pc_row = conn.execute(
                    text(
                        f"SELECT COUNT(DISTINCT date_trunc(:g, {expr})) FROM {qual} "
                        f'WHERE "{col}" IS NOT NULL'
                    ),
                    {"g": gran},
                ).fetchone()
                period_count = int(pc_row[0]) if pc_row and pc_row[0] is not None else 0

                dow_dist = None
                if gran in ("day", "week"):
                    dow_rows = conn.execute(
                        text(
                            f"SELECT TRIM(TO_CHAR({expr}, 'Dy')) AS dow, COUNT(*) AS ct "
                            f"FROM {qual} "
                            f'WHERE "{col}" IS NOT NULL '
                            f"GROUP BY 1"
                        )
                    ).fetchall()
                    # Initialize 0s in canonical order
                    dow_dist = {k: 0 for k in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
                    for dr in dow_rows:
                        k = str(dr[0]).strip()[:3]
                        if k in dow_dist:
                            dow_dist[k] = int(dr[1])

            period_counts = [
                {"period": _iso(r[0]), "count": int(r[1])} for r in rows
            ]

            missing = 0
            if isinstance(min_d, (date, datetime)) and isinstance(max_d, (date, datetime)):
                md = min_d.date() if isinstance(min_d, datetime) else min_d
                xd = max_d.date() if isinstance(max_d, datetime) else max_d
                expected = _estimate_total_periods(md, xd, gran)
                missing = max(0, expected - period_count)

            return {
                "range": {"min": _iso(min_d), "max": _iso(max_d)},
                "period_count": period_count,
                "period_counts": period_counts,
                "missing_periods_estimate": missing,
                "dow_distribution": dow_dist,
                "warning": warning,
            }

        try:
            result = _run(cast=False)
        except Exception as e1:
            logger.debug(f"[eda] inspect_time raw failed, retrying with cast: {e1}")
            try:
                result = _run(cast=True)
                result["warning"] = (
                    "date_col is text-typed; auto-cast to ::date applied"
                )
            except Exception as e2:
                raise e2

        _record_telemetry("inspect_time", args, success=True, result=result)
        return result
    except Exception as e:
        logger.warning(f"[eda] inspect_time failed: {e}")
        _record_telemetry("inspect_time", args, success=False)
        return {
            "range": {"min": "", "max": ""},
            "period_count": 0,
            "period_counts": [],
            "missing_periods_estimate": 0,
            "dow_distribution": None,
            "warning": None,
            "error": str(e)[:200],
        }


# --------------------------------------------------------------------------- #
# Agno @tool factory
# --------------------------------------------------------------------------- #
def create_eda_tools(project_slug: str) -> list:
    """Return list of 3 Agno @tool-decorated callables bound to `project_slug`.

    Each wrapper omits `project_slug` from its signature — the LLM doesn't
    need (or have permission) to choose a tenant. Pre-bound via closure.

    Returns [] if `EDA_TOOLS_DISABLED=1` or Agno @tool import fails.
    """
    if os.getenv("EDA_TOOLS_DISABLED", "").strip() in ("1", "true", "yes", "on"):
        logger.info("[eda] EDA_TOOLS_DISABLED set, returning empty tool list")
        return []

    try:
        from agno.tools import tool  # type: ignore
    except Exception as e:
        logger.warning(f"[eda] agno @tool import failed, returning empty: {e}")
        return []

    @tool(
        name="inspect_dim",
        description=(
            "Return the top-N values + counts + frequency for one dimension "
            "column. Use this when the user asks for the full list of values "
            "for a column (e.g. 'show me all regions') or when the compact "
            "10-value summary in the prompt is not enough. `top_n` defaults "
            "to 50, capped at 200."
        ),
    )
    def inspect_dim(table: str, column: str, top_n: int = 50) -> dict:
        return inspect_dimension(table, column, top_n, project_slug=project_slug)

    @tool(
        name="inspect_xdim",
        description=(
            "Return the top-N (dim_a, dim_b) co-occurrence cells with counts "
            "and percent of total. Use for cross-dimension breakdowns "
            "(e.g. 'channel x region breakdown'). `top_n` defaults to 20."
        ),
    )
    def inspect_xdim(table: str, dim_a: str, dim_b: str, top_n: int = 20) -> dict:
        return inspect_cross_dim(table, dim_a, dim_b, top_n, project_slug=project_slug)

    @tool(
        name="inspect_time_tool",
        description=(
            "Return the date range, period counts (top 24 most-recent), "
            "missing-period estimate, and day-of-week distribution "
            "(when granularity is day or week). Use for time-trend questions "
            "(e.g. 'monthly trend last 12 months'). `granularity` is one of "
            "day, week, month, quarter, year (default month)."
        ),
    )
    def inspect_time_tool(table: str, date_col: str, granularity: str = "month") -> dict:
        return inspect_time(table, date_col, granularity, project_slug=project_slug)

    return [inspect_dim, inspect_xdim, inspect_time_tool]
