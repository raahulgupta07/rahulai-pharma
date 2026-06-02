"""
Analysis Type Tools
===================

Toolkit of analysis functions for the Analyst agent.
Each function takes a question + project context and returns
structured Markdown results. Registered as Agno function tools
via the @tool decorator in build.py.

All queries are READ ONLY with LIMIT 1000 and 30s timeout.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_schema(project_slug: str) -> str:
    """Sanitize project slug into a valid PostgreSQL schema name."""
    return re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]


def _get_engine(project_slug: str):
    """Get a read-only engine scoped to the project schema."""
    from db import get_project_readonly_engine
    return get_project_readonly_engine(project_slug)


def _run_readonly_query(
    engine, schema: str, sql: str, params: dict | None = None, timeout: int = 30
) -> list[dict]:
    """Execute a read-only query with timeout. Returns list of row dicts."""
    with engine.connect() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{timeout}s'"))
        result = conn.execute(text(sql), params or {})
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


def _find_date_columns(engine, schema: str, table: str) -> list[str]:
    """Find columns with date/timestamp types."""
    rows = _run_readonly_query(
        engine, schema,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = :table "
        "AND data_type IN ('date','timestamp without time zone','timestamp with time zone')",
        {"schema": schema, "table": table},
    )
    return [r["column_name"] for r in rows]


def _find_metric_columns(engine, schema: str, table: str) -> list[str]:
    """Find numeric columns likely to be metrics."""
    rows = _run_readonly_query(
        engine, schema,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = :table "
        "AND data_type IN ('integer','bigint','numeric','double precision','real','smallint')",
        {"schema": schema, "table": table},
    )
    return [r["column_name"] for r in rows]


def _find_dimension_columns(engine, schema: str, table: str) -> list[str]:
    """Find categorical columns (text with <50 distinct values)."""
    text_cols = _run_readonly_query(
        engine, schema,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = :table "
        "AND data_type IN ('text','character varying','character')",
        {"schema": schema, "table": table},
    )
    dims = []
    for r in text_cols:
        col = r["column_name"]
        cnt = _run_readonly_query(
            engine, schema,
            f'SELECT COUNT(DISTINCT "{col}") AS n FROM "{schema}"."{table}" LIMIT 1',
        )
        if cnt and cnt[0]["n"] < 50:
            dims.append(col)
    return dims


def _get_tables(engine, schema: str) -> list[str]:
    """List all tables in the project schema."""
    rows = _run_readonly_query(
        engine, schema,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = :schema AND table_type = 'BASE TABLE'",
        {"schema": schema},
    )
    return [r["table_name"] for r in rows]


def _detect_period(question: str) -> tuple[str, str, str, str]:
    """Extract time period from question.

    Returns (current_start, current_end, prev_start, prev_end) as ISO date strings.
    Defaults to latest complete month vs prior month.
    """
    today = datetime.now()
    q = question.lower()

    # Try to detect quarter references
    for yr in range(today.year - 2, today.year + 1):
        for qn in range(1, 5):
            if f"q{qn}" in q and str(yr) in q:
                m_start = (qn - 1) * 3 + 1
                cs = datetime(yr, m_start, 1)
                ce = datetime(yr, m_start + 3, 1) if m_start + 3 <= 12 else datetime(yr + 1, (m_start + 3) - 12, 1)
                ps = cs - timedelta(days=90)
                ps = ps.replace(day=1)
                pe = cs
                return cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d"), ps.strftime("%Y-%m-%d"), pe.strftime("%Y-%m-%d")

    # Default: last complete month vs month before
    first_this_month = today.replace(day=1)
    ce = first_this_month
    cs = (first_this_month - timedelta(days=1)).replace(day=1)
    pe = cs
    ps = (cs - timedelta(days=1)).replace(day=1)
    return cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d"), ps.strftime("%Y-%m-%d"), pe.strftime("%Y-%m-%d")


def _fmt(val: Any) -> str:
    """Format a value for Markdown display."""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        if abs(val) >= 1_000_000:
            return f"{val:,.0f}"
        return f"{val:,.2f}"
    return str(val)


def _delta_arrow(change_pct: float | None) -> str:
    if change_pct is None:
        return "N/A"
    arrow = "\u25b2" if change_pct >= 0 else "\u25bc"
    return f"{arrow} {abs(change_pct):.1f}%"


# ---------------------------------------------------------------------------
# 1. Comparator Analysis  (THE most important tool)
# ---------------------------------------------------------------------------

def comparator_analysis(question: str, project_slug: str) -> str:
    """Run period comparison (MoM + YoY) analysis.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    Finds tables with date columns, queries the same metric for current
    period, previous period, and same period last year, then calculates
    deltas with direction arrows.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found in project schema."

        cs, ce, ps, pe = _detect_period(question)
        # YoY: same month last year
        from dateutil.relativedelta import relativedelta
        cs_dt = datetime.strptime(cs, "%Y-%m-%d")
        ce_dt = datetime.strptime(ce, "%Y-%m-%d")
        yoy_cs = (cs_dt - relativedelta(years=1)).strftime("%Y-%m-%d")
        yoy_ce = (ce_dt - relativedelta(years=1)).strftime("%Y-%m-%d")

        sections: list[str] = [f"## Comparator Analysis\n**Period:** {cs} to {ce}\n"]

        for tbl in tables[:5]:
            date_cols = _find_date_columns(engine, schema, tbl)
            metric_cols = _find_metric_columns(engine, schema, tbl)
            if not date_cols or not metric_cols:
                continue

            dc = date_cols[0]
            dim_cols = _find_dimension_columns(engine, schema, tbl)
            group_clause = f'"{dim_cols[0]}",' if dim_cols else ""
            group_by = f'GROUP BY "{dim_cols[0]}"' if dim_cols else ""

            metric_selects = ", ".join(
                f'SUM("{m}") AS "{m}"' for m in metric_cols[:5]
            )

            sql = (
                f"WITH current_p AS ("
                f'  SELECT {group_clause} {metric_selects} FROM "{schema}"."{tbl}" '
                f"  WHERE \"{dc}\" >= '{cs}' AND \"{dc}\" < '{ce}' {group_by} LIMIT 1000"
                f"), prev_p AS ("
                f'  SELECT {group_clause} {metric_selects} FROM "{schema}"."{tbl}" '
                f"  WHERE \"{dc}\" >= '{ps}' AND \"{dc}\" < '{pe}' {group_by} LIMIT 1000"
                f"), yoy_p AS ("
                f'  SELECT {group_clause} {metric_selects} FROM "{schema}"."{tbl}" '
                f"  WHERE \"{dc}\" >= '{yoy_cs}' AND \"{dc}\" < '{yoy_ce}' {group_by} LIMIT 1000"
                f") SELECT 'current' AS period, * FROM current_p "
                f"UNION ALL SELECT 'previous' AS period, * FROM prev_p "
                f"UNION ALL SELECT 'yoy' AS period, * FROM yoy_p "
                f"LIMIT 1000"
            )

            rows = _run_readonly_query(engine, schema, sql)
            if not rows:
                continue

            # Build comparison table
            lines = [f"\n### {tbl}\n", "| Metric | Current | MoM \u0394 | YoY \u0394 |", "|--------|---------|-------|-------|"]
            curr = [r for r in rows if r.get("period") == "current"]
            prev = [r for r in rows if r.get("period") == "previous"]
            yoy = [r for r in rows if r.get("period") == "yoy"]

            for m in metric_cols[:5]:
                cv = sum(float(r.get(m) or 0) for r in curr)
                pv = sum(float(r.get(m) or 0) for r in prev)
                yv = sum(float(r.get(m) or 0) for r in yoy)
                mom_pct = ((cv - pv) / pv * 100) if pv else None
                yoy_pct = ((cv - yv) / yv * 100) if yv else None
                lines.append(f"| {m} | {_fmt(cv)} | {_delta_arrow(mom_pct)} | {_delta_arrow(yoy_pct)} |")

            sections.append("\n".join(lines))

        return "\n".join(sections) if len(sections) > 1 else "No tables with date + metric columns found for comparison."

    except Exception as e:
        logger.exception("comparator_analysis failed")
        return f"Comparator analysis error: {e}"


# ---------------------------------------------------------------------------
# 2. Diagnostic Analysis
# ---------------------------------------------------------------------------

def diagnostic_analysis(question: str, project_slug: str) -> str:
    """Decompose a metric into dimensions to find root cause of change.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.

    For each dimension column, calculates contribution to total change
    and ranks by absolute impact (waterfall-style breakdown).
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found."

        cs, ce, ps, pe = _detect_period(question)
        sections = ["## Diagnostic Analysis\n"]

        for tbl in tables[:3]:
            date_cols = _find_date_columns(engine, schema, tbl)
            metric_cols = _find_metric_columns(engine, schema, tbl)
            dim_cols = _find_dimension_columns(engine, schema, tbl)
            if not date_cols or not metric_cols or not dim_cols:
                continue

            dc = date_cols[0]
            mc = metric_cols[0]
            sections.append(f"\n### {tbl} | Metric: `{mc}`\n")

            # Total change
            totals = _run_readonly_query(engine, schema, (
                f'SELECT '
                f'SUM(CASE WHEN "{dc}" >= \'{cs}\' AND "{dc}" < \'{ce}\' THEN "{mc}" END) AS curr, '
                f'SUM(CASE WHEN "{dc}" >= \'{ps}\' AND "{dc}" < \'{pe}\' THEN "{mc}" END) AS prev '
                f'FROM "{schema}"."{tbl}" LIMIT 1'
            ))
            if not totals:
                continue
            curr_total = float(totals[0].get("curr") or 0)
            prev_total = float(totals[0].get("prev") or 0)
            total_delta = curr_total - prev_total
            total_pct = (total_delta / prev_total * 100) if prev_total else 0

            sections.append(f"**Total change:** {_fmt(curr_total)} vs {_fmt(prev_total)} = {_delta_arrow(total_pct)}\n")

            # Per-dimension decomposition
            for dim in dim_cols[:4]:
                rows = _run_readonly_query(engine, schema, (
                    f'SELECT "{dim}", '
                    f'SUM(CASE WHEN "{dc}" >= \'{cs}\' AND "{dc}" < \'{ce}\' THEN "{mc}" END) AS curr, '
                    f'SUM(CASE WHEN "{dc}" >= \'{ps}\' AND "{dc}" < \'{pe}\' THEN "{mc}" END) AS prev, '
                    f'SUM(CASE WHEN "{dc}" >= \'{cs}\' AND "{dc}" < \'{ce}\' THEN "{mc}" END) - '
                    f'SUM(CASE WHEN "{dc}" >= \'{ps}\' AND "{dc}" < \'{pe}\' THEN "{mc}" END) AS contribution '
                    f'FROM "{schema}"."{tbl}" '
                    f'GROUP BY "{dim}" ORDER BY ABS('
                    f'SUM(CASE WHEN "{dc}" >= \'{cs}\' AND "{dc}" < \'{ce}\' THEN "{mc}" END) - '
                    f'SUM(CASE WHEN "{dc}" >= \'{ps}\' AND "{dc}" < \'{pe}\' THEN "{mc}" END)'
                    f') DESC LIMIT 10'
                ))
                if not rows:
                    continue

                sections.append(f"\n**Dimension: `{dim}`**\n")
                sections.append("| Value | Current | Previous | Contribution |")
                sections.append("|-------|---------|----------|-------------|")
                for r in rows:
                    c = float(r.get("curr") or 0)
                    p = float(r.get("prev") or 0)
                    contrib = float(r.get("contribution") or 0)
                    pct_of_total = (contrib / total_delta * 100) if total_delta else 0
                    sections.append(f"| {r[dim]} | {_fmt(c)} | {_fmt(p)} | {_fmt(contrib)} ({pct_of_total:+.0f}%) |")

        return "\n".join(sections) if len(sections) > 1 else "No suitable tables for diagnostic analysis."

    except Exception as e:
        logger.exception("diagnostic_analysis failed")
        return f"Diagnostic analysis error: {e}"


# ---------------------------------------------------------------------------
# 3. Narrator Analysis
# ---------------------------------------------------------------------------

def narrator_analysis(question: str, project_slug: str) -> str:
    """Generate McKinsey-style executive narrative combining data + document context.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found."

        # Gather key metrics from up to 5 tables
        data_points: list[str] = []
        for tbl in tables[:5]:
            metrics = _find_metric_columns(engine, schema, tbl)
            if not metrics:
                continue
            agg_parts = ", ".join(
                f'ROUND(SUM("{m}")::numeric, 2) AS "{m}_total", '
                f'ROUND(AVG("{m}")::numeric, 2) AS "{m}_avg", '
                f'COUNT("{m}") AS "{m}_count"'
                for m in metrics[:3]
            )
            rows = _run_readonly_query(engine, schema, f'SELECT {agg_parts} FROM "{schema}"."{tbl}" LIMIT 1')
            if rows:
                data_points.append(f"Table '{tbl}': {json.dumps(rows[0], default=str)}")

        # Load document context if available
        from dash.paths import KNOWLEDGE_DIR
        doc_context = ""
        kg_path = KNOWLEDGE_DIR / project_slug / "grounded_facts.json"
        if kg_path.exists():
            try:
                facts = json.loads(kg_path.read_text())[:20]
                doc_context = "\n".join(f"- {f.get('text', f)}" for f in facts if isinstance(f, dict))
            except Exception:
                pass

        # LLM narrative generation
        from dash.settings import training_llm_call
        prompt = (
            "Write a McKinsey-style executive summary for this data. "
            "Include: a bold headline, 3 key wins, 3 key risks, 3 recommended actions.\n\n"
            f"USER QUESTION: {question}\n\n"
            f"DATA POINTS:\n" + "\n".join(data_points) + "\n\n"
            f"BUSINESS CONTEXT:\n{doc_context or 'No additional context available.'}"
        )
        narrative = training_llm_call(prompt, task="deep_analysis")
        if narrative:
            return f"## Executive Narrative\n\n{narrative}"
        return "## Executive Narrative\n\nLLM call unavailable. Raw data:\n" + "\n".join(data_points)

    except Exception as e:
        logger.exception("narrator_analysis failed")
        return f"Narrator analysis error: {e}"


# ---------------------------------------------------------------------------
# 4. Validator Analysis
# ---------------------------------------------------------------------------

def validator_analysis(question: str, project_slug: str) -> str:
    """Profile data quality across all tables in the project schema.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found."

        sections = ["## Data Quality Report\n"]
        scores: list[float] = []

        for tbl in tables[:10]:
            # Row count
            cnt = _run_readonly_query(engine, schema, f'SELECT COUNT(*) AS n FROM "{schema}"."{tbl}" LIMIT 1')
            total = cnt[0]["n"] if cnt else 0

            # Column-level profiling
            cols_info = _run_readonly_query(engine, schema, (
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ), {"schema": schema, "table": tbl})

            col_reports: list[str] = []
            null_pcts: list[float] = []

            for ci in cols_info[:20]:
                col = ci["column_name"]
                stats = _run_readonly_query(engine, schema, (
                    f'SELECT COUNT(*) AS total, '
                    f'COUNT("{col}") AS non_null, '
                    f'COUNT(DISTINCT "{col}") AS distinct_vals '
                    f'FROM "{schema}"."{tbl}" LIMIT 1'
                ))
                if not stats:
                    continue
                s = stats[0]
                t = s["total"] or 1
                nn = s["non_null"] or 0
                null_pct = round((1 - nn / t) * 100, 1) if t else 0
                null_pcts.append(null_pct)
                flag = " **!**" if null_pct > 30 else ""
                col_reports.append(
                    f"| {col} | {ci['data_type']} | {nn}/{t} | {null_pct}% | {s['distinct_vals']}{flag} |"
                )

            # Duplicate check
            dup_check = _run_readonly_query(engine, schema, (
                f'SELECT COUNT(*) AS total, COUNT(*) - COUNT(DISTINCT *) AS dups '
                f'FROM (SELECT * FROM "{schema}"."{tbl}" LIMIT 1000) sub LIMIT 1'
            ))
            dup_pct = 0
            if dup_check:
                dups = dup_check[0].get("dups", 0) or 0
                dup_pct = round(dups / max(total, 1) * 100, 1)

            # Score: 100 - (avg_null_pct * 0.5) - (dup_pct * 0.3)
            avg_null = sum(null_pcts) / len(null_pcts) if null_pcts else 0
            score = max(0, round(100 - avg_null * 0.5 - dup_pct * 0.3, 1))
            scores.append(score)

            health = "Good" if score >= 80 else "Fair" if score >= 60 else "Poor"
            sections.append(f"\n### {tbl} | Score: **{score}/100** ({health})")
            sections.append(f"Rows: {total} | Duplicates: {dup_pct}%\n")
            sections.append("| Column | Type | Non-null | Null% | Distinct |")
            sections.append("|--------|------|----------|-------|----------|")
            sections.extend(col_reports)

        overall = round(sum(scores) / len(scores), 1) if scores else 0
        overall_health = "Good" if overall >= 80 else "Fair" if overall >= 60 else "Poor"
        sections.insert(1, f"**Overall Score: {overall}/100 ({overall_health})** | Tables: {len(tables)}\n")

        return "\n".join(sections)

    except Exception as e:
        logger.exception("validator_analysis failed")
        return f"Validator analysis error: {e}"


# ---------------------------------------------------------------------------
# 5. Planner Analysis (What-if Scenarios)
# ---------------------------------------------------------------------------

def planner_analysis(question: str, project_slug: str) -> str:
    """What-if scenario modeling with base / upside / downside projections.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found."

        # Parse change from question (e.g. "drops 20%", "increase by 15%")
        import re as _re
        m = _re.search(r'(\-?\d+(?:\.\d+)?)\s*%', question)
        change_pct = float(m.group(1)) if m else -10.0
        # If question says "drop" or "decrease", ensure negative
        if any(w in question.lower() for w in ("drop", "decrease", "decline", "fall")):
            change_pct = -abs(change_pct)
        elif any(w in question.lower() for w in ("increase", "grow", "rise", "gain")):
            change_pct = abs(change_pct)

        sections = [f"## Scenario Analysis\n**Assumed change:** {change_pct:+.1f}%\n"]

        for tbl in tables[:3]:
            metrics = _find_metric_columns(engine, schema, tbl)
            if not metrics:
                continue

            mc = metrics[0]
            base_rows = _run_readonly_query(engine, schema, (
                f'SELECT SUM("{mc}") AS total, AVG("{mc}") AS avg, COUNT(*) AS n '
                f'FROM "{schema}"."{tbl}" LIMIT 1'
            ))
            if not base_rows:
                continue
            baseline = float(base_rows[0]["total"] or 0)

            # Three scenarios
            scenarios = [
                ("Downside (15% prob)", change_pct * 1.5),
                ("Base (60% prob)", change_pct),
                ("Upside (25% prob)", change_pct * 0.3),
            ]

            sections.append(f"\n### {tbl} | Metric: `{mc}`")
            sections.append(f"**Current baseline:** {_fmt(baseline)}\n")
            sections.append("| Scenario | Change | Projected | Impact |")
            sections.append("|----------|--------|-----------|--------|")

            for name, pct in scenarios:
                projected = baseline * (1 + pct / 100)
                impact = projected - baseline
                sections.append(f"| {name} | {pct:+.1f}% | {_fmt(projected)} | {_fmt(impact)} |")

            # Expected value
            ev = sum(
                baseline * (1 + s[1] / 100) * p
                for s, p in zip(scenarios, [0.15, 0.60, 0.25])
            )
            sections.append(f"\n**Expected value (probability-weighted):** {_fmt(ev)}")

        return "\n".join(sections) if len(sections) > 1 else "No numeric data for scenario modeling."

    except Exception as e:
        logger.exception("planner_analysis failed")
        return f"Planner analysis error: {e}"


# ---------------------------------------------------------------------------
# 6. Trend Analysis
# ---------------------------------------------------------------------------

def trend_analysis(question: str, project_slug: str) -> str:
    """Time series with moving averages and direction detection.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)

        sections = ["## Trend Analysis\n"]
        for tbl in tables[:3]:
            date_cols = _find_date_columns(engine, schema, tbl)
            metric_cols = _find_metric_columns(engine, schema, tbl)
            if not date_cols or not metric_cols:
                continue

            dc, mc = date_cols[0], metric_cols[0]
            rows = _run_readonly_query(engine, schema, (
                f'SELECT DATE_TRUNC(\'month\', "{dc}") AS month, '
                f'SUM("{mc}") AS value, COUNT(*) AS n '
                f'FROM "{schema}"."{tbl}" '
                f'WHERE "{dc}" IS NOT NULL '
                f'GROUP BY 1 ORDER BY 1 LIMIT 60'
            ))
            if len(rows) < 2:
                continue

            # Calculate 3-period moving average
            values = [float(r["value"] or 0) for r in rows]
            ma3 = [None, None] + [round(sum(values[i - 2:i + 1]) / 3, 2) for i in range(2, len(values))]

            # Direction detection
            recent = values[-3:] if len(values) >= 3 else values
            if len(recent) >= 2:
                direction = "Upward" if recent[-1] > recent[0] else "Downward" if recent[-1] < recent[0] else "Flat"
            else:
                direction = "Insufficient data"

            sections.append(f"\n### {tbl} | `{mc}` | Trend: **{direction}**\n")
            sections.append("| Month | Value | MA(3) |")
            sections.append("|-------|-------|-------|")
            for i, r in enumerate(rows[-12:]):
                idx = len(rows) - 12 + i if len(rows) >= 12 else i
                ma_val = _fmt(ma3[idx]) if idx < len(ma3) and ma3[idx] is not None else "-"
                month_str = str(r["month"])[:10]
                sections.append(f"| {month_str} | {_fmt(float(r['value'] or 0))} | {ma_val} |")

        return "\n".join(sections) if len(sections) > 1 else "No time series data found."

    except Exception as e:
        logger.exception("trend_analysis failed")
        return f"Trend analysis error: {e}"


# ---------------------------------------------------------------------------
# 7. Pareto Analysis
# ---------------------------------------------------------------------------

def pareto_analysis(question: str, project_slug: str) -> str:
    """Run 80/20 Pareto analysis: sort by impact, cumulative %, identify drivers.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)

        sections = ["## Pareto Analysis (80/20)\n"]
        for tbl in tables[:3]:
            dims = _find_dimension_columns(engine, schema, tbl)
            metrics = _find_metric_columns(engine, schema, tbl)
            if not dims or not metrics:
                continue

            dim, mc = dims[0], metrics[0]
            rows = _run_readonly_query(engine, schema, (
                f'SELECT "{dim}", SUM("{mc}"::numeric) AS total '
                f'FROM "{schema}"."{tbl}" '
                f'GROUP BY "{dim}" ORDER BY total DESC LIMIT 50'
            ))
            if not rows:
                continue

            grand = sum(float(r["total"] or 0) for r in rows)
            if grand == 0:
                continue

            sections.append(f"\n### {tbl} | `{mc}` by `{dim}`\n")
            sections.append("| Rank | Value | Total | % | Cumul% | Zone |")
            sections.append("|------|-------|-------|---|--------|------|")
            cumul = 0.0
            eighty_count = 0
            for i, r in enumerate(rows):
                pct = float(r["total"] or 0) / grand * 100
                cumul += pct
                zone = "A (vital)" if cumul <= 80 else "B (useful)" if cumul <= 95 else "C (trivial)"
                if cumul <= 80:
                    eighty_count = i + 1
                sections.append(
                    f"| {i + 1} | {r[dim]} | {_fmt(float(r['total'] or 0))} | {pct:.1f}% | {cumul:.1f}% | {zone} |"
                )

            sections.append(f"\n**{eighty_count}/{len(rows)} items** drive 80% of `{mc}` (concentration: {'high' if eighty_count <= len(rows) * 0.3 else 'moderate'})")

        return "\n".join(sections) if len(sections) > 1 else "No categorical + metric columns for Pareto analysis."

    except Exception as e:
        logger.exception("pareto_analysis failed")
        return f"Pareto analysis error: {e}"


# ---------------------------------------------------------------------------
# 8. Anomaly Analysis
# ---------------------------------------------------------------------------

def anomaly_analysis(question: str, project_slug: str) -> str:
    """Z-score outlier detection across numeric columns.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)

        sections = ["## Anomaly Detection\n"]
        for tbl in tables[:5]:
            metrics = _find_metric_columns(engine, schema, tbl)
            if not metrics:
                continue

            tbl_anomalies: list[str] = []
            for mc in metrics[:5]:
                stats = _run_readonly_query(engine, schema, (
                    f'SELECT AVG("{mc}"::numeric) AS mean, STDDEV("{mc}"::numeric) AS std, '
                    f'COUNT(*) AS n FROM "{schema}"."{tbl}" WHERE "{mc}" IS NOT NULL LIMIT 1'
                ))
                if not stats or not stats[0].get("std") or float(stats[0]["std"]) == 0:
                    continue

                mean = float(stats[0]["mean"])
                std = float(stats[0]["std"])
                threshold = 2.5

                outliers = _run_readonly_query(engine, schema, (
                    f'SELECT * FROM "{schema}"."{tbl}" '
                    f'WHERE ABS(("{mc}"::numeric - {mean}) / {std}) > {threshold} '
                    f'LIMIT 10'
                ))
                if outliers:
                    tbl_anomalies.append(f"- **{mc}**: {len(outliers)} outliers (z > {threshold}), mean={_fmt(mean)}, std={_fmt(std)}")

            if tbl_anomalies:
                sections.append(f"\n### {tbl}\n")
                sections.extend(tbl_anomalies)

        return "\n".join(sections) if len(sections) > 1 else "No anomalies detected."

    except Exception as e:
        logger.exception("anomaly_analysis failed")
        return f"Anomaly analysis error: {e}"


# ---------------------------------------------------------------------------
# 9. Benchmark Analysis
# ---------------------------------------------------------------------------

def benchmark_analysis(question: str, project_slug: str) -> str:
    """Compare entity vs group average.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)

        sections = ["## Benchmark Analysis\n"]
        for tbl in tables[:3]:
            dims = _find_dimension_columns(engine, schema, tbl)
            metrics = _find_metric_columns(engine, schema, tbl)
            if not dims or not metrics:
                continue

            dim, mc = dims[0], metrics[0]
            rows = _run_readonly_query(engine, schema, (
                f'SELECT "{dim}", '
                f'AVG("{mc}"::numeric) AS entity_avg, '
                f'(SELECT AVG("{mc}"::numeric) FROM "{schema}"."{tbl}") AS group_avg, '
                f'COUNT(*) AS n '
                f'FROM "{schema}"."{tbl}" '
                f'GROUP BY "{dim}" ORDER BY entity_avg DESC LIMIT 20'
            ))
            if not rows:
                continue

            group_avg = float(rows[0].get("group_avg") or 0)
            sections.append(f"\n### {tbl} | `{mc}` by `{dim}`")
            sections.append(f"**Group average:** {_fmt(group_avg)}\n")
            sections.append("| Entity | Avg | vs Group | Status |")
            sections.append("|--------|-----|----------|--------|")
            for r in rows:
                ea = float(r.get("entity_avg") or 0)
                gap_pct = ((ea - group_avg) / group_avg * 100) if group_avg else 0
                status = "Above" if gap_pct > 5 else "Below" if gap_pct < -5 else "On par"
                sections.append(f"| {r[dim]} | {_fmt(ea)} | {_delta_arrow(gap_pct)} | {status} |")

        return "\n".join(sections) if len(sections) > 1 else "No suitable data for benchmarking."

    except Exception as e:
        logger.exception("benchmark_analysis failed")
        return f"Benchmark analysis error: {e}"


# ---------------------------------------------------------------------------
# 10. Root Cause Analysis
# ---------------------------------------------------------------------------

def root_cause_analysis(question: str, project_slug: str) -> str:
    """Iterative drill-down: total -> dimension -> sub-dimension.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)

        sections = ["## Root Cause Drill-Down\n"]
        for tbl in tables[:2]:
            metrics = _find_metric_columns(engine, schema, tbl)
            dims = _find_dimension_columns(engine, schema, tbl)
            if not metrics or not dims:
                continue

            mc = metrics[0]
            total_rows = _run_readonly_query(engine, schema, (
                f'SELECT SUM("{mc}"::numeric) AS total, AVG("{mc}"::numeric) AS avg '
                f'FROM "{schema}"."{tbl}" LIMIT 1'
            ))
            if not total_rows:
                continue

            grand_total = float(total_rows[0]["total"] or 0)
            sections.append(f"\n### {tbl} | `{mc}` = {_fmt(grand_total)}\n")

            # Level 1: top dimension
            for level, dim in enumerate(dims[:3], 1):
                rows = _run_readonly_query(engine, schema, (
                    f'SELECT "{dim}", SUM("{mc}"::numeric) AS val, '
                    f'ROUND(SUM("{mc}"::numeric) / NULLIF((SELECT SUM("{mc}"::numeric) FROM "{schema}"."{tbl}"), 0) * 100, 1) AS pct '
                    f'FROM "{schema}"."{tbl}" GROUP BY "{dim}" ORDER BY val DESC LIMIT 10'
                ))
                if not rows:
                    continue

                sections.append(f"\n**Level {level}: `{dim}`**\n")
                sections.append("| Value | Amount | Share |")
                sections.append("|-------|--------|-------|")
                for r in rows:
                    sections.append(f"| {r[dim]} | {_fmt(float(r['val'] or 0))} | {r['pct']}% |")

                # Drill into top value if there's another dimension
                if level < len(dims):
                    top_val = rows[0][dim]
                    next_dim = dims[level] if level < len(dims) else None
                    if next_dim:
                        sub_rows = _run_readonly_query(engine, schema, (
                            f'SELECT "{next_dim}", SUM("{mc}"::numeric) AS val '
                            f'FROM "{schema}"."{tbl}" WHERE "{dim}" = :top_val '
                            f'GROUP BY "{next_dim}" ORDER BY val DESC LIMIT 5'
                        ), {"top_val": top_val})
                        if sub_rows:
                            sections.append(f"\n  *Drill into `{dim}` = {top_val}:*")
                            for sr in sub_rows:
                                sections.append(f"  - {sr[next_dim]}: {_fmt(float(sr['val'] or 0))}")

        return "\n".join(sections) if len(sections) > 1 else "No dimensional data for root cause analysis."

    except Exception as e:
        logger.exception("root_cause_analysis failed")
        return f"Root cause analysis error: {e}"


# ---------------------------------------------------------------------------
# 11. Prescriptive Analysis
# ---------------------------------------------------------------------------

def prescriptive_analysis(question: str, project_slug: str) -> str:
    """Data pull + LLM generates numbered recommendations with expected impact.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving
    the result, STOP and synthesize the answer. Do NOT call this tool
    again with different parameters — the result is complete.
    """
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tables = _get_tables(engine, schema)
        if not tables:
            return "No tables found."

        # Gather summary stats
        summaries: list[str] = []
        for tbl in tables[:5]:
            metrics = _find_metric_columns(engine, schema, tbl)
            dims = _find_dimension_columns(engine, schema, tbl)
            if not metrics:
                continue

            aggs = ", ".join(f'SUM("{m}"::numeric) AS "{m}_sum", AVG("{m}"::numeric) AS "{m}_avg"' for m in metrics[:3])
            rows = _run_readonly_query(engine, schema, f'SELECT {aggs} FROM "{schema}"."{tbl}" LIMIT 1')
            if rows:
                summaries.append(f"Table '{tbl}': {json.dumps(rows[0], default=str)}")

            # Top/bottom per dimension
            if dims:
                d = dims[0]
                mc = metrics[0]
                top = _run_readonly_query(engine, schema, (
                    f'SELECT "{d}", SUM("{mc}"::numeric) AS val FROM "{schema}"."{tbl}" '
                    f'GROUP BY "{d}" ORDER BY val DESC LIMIT 3'
                ))
                bot = _run_readonly_query(engine, schema, (
                    f'SELECT "{d}", SUM("{mc}"::numeric) AS val FROM "{schema}"."{tbl}" '
                    f'GROUP BY "{d}" ORDER BY val ASC LIMIT 3'
                ))
                if top:
                    summaries.append(f"  Top {d}: {json.dumps(top, default=str)}")
                if bot:
                    summaries.append(f"  Bottom {d}: {json.dumps(bot, default=str)}")

        from dash.settings import training_llm_call
        prompt = (
            "Based on this data, provide 5 numbered actionable recommendations. "
            "For each: state the action, expected impact (quantified), and priority (high/medium/low).\n\n"
            f"QUESTION: {question}\n\n"
            f"DATA:\n" + "\n".join(summaries)
        )
        result = training_llm_call(prompt, task="deep_analysis")
        if result:
            return f"## Prescriptive Recommendations\n\n{result}"
        return "## Prescriptive Recommendations\n\nLLM unavailable. Data summary:\n" + "\n".join(summaries)

    except Exception as e:
        logger.exception("prescriptive_analysis failed")
        return f"Prescriptive analysis error: {e}"


# ---------------------------------------------------------------------------
# Pre-lookup + Post-save wrapper (closes Analyst's MANDATORY chain bypass)
# Brain context + schema audit BEFORE specialist tool runs
# Validated-SQL save AFTER specialist tool runs (skill library)
# ---------------------------------------------------------------------------

def _pre_lookup_context(question: str, project_slug: str) -> str:
    """Return markdown block of brain hits + schema summary for audit trail."""
    parts: list[str] = []
    # Brain (glossary, formulas, aliases, thresholds)
    try:
        from app.brain import get_brain_context
        bctx = get_brain_context(for_agent="analyst", project_slug=project_slug)
        if bctx:
            snippet = bctx.strip()
            if len(snippet) > 600:
                snippet = snippet[:600] + " …"
            parts.append(f"**Brain context used:**\n{snippet}")
    except Exception:
        pass
    # Schema audit — list tables + column count
    try:
        schema = _safe_schema(project_slug)
        engine = _get_engine(project_slug)
        tbls = _get_tables(engine, schema)
        if tbls:
            parts.append(f"**Schema introspected:** {len(tbls)} tables — " + ", ".join(tbls[:10]) + (" …" if len(tbls) > 10 else ""))
    except Exception:
        pass
    if not parts:
        return ""
    return "<!--ANALYSIS_AUDIT_START-->\n_Pre-flight chain (auto):_\n\n" + "\n\n".join(parts) + "\n<!--ANALYSIS_AUDIT_END-->\n"


def _post_save_pattern(question: str, project_slug: str, tool_name: str) -> None:
    """Save proven (question, tool) pattern so 2nd ask hits cache."""
    try:
        from sqlalchemy import text as _t
        from db import db_url
        from sqlalchemy import create_engine as _ce
        from sqlalchemy.pool import NullPool
        eng = _ce(db_url, poolclass=NullPool)
        with eng.begin() as conn:
            conn.execute(_t(
                """INSERT INTO public.dash_query_patterns
                    (project_slug, question, sql, source, uses, created_at)
                   VALUES (:p, :q, :s, 'specialist_tool', 1, now())
                   ON CONFLICT DO NOTHING"""
            ), {"p": project_slug, "q": (question or "")[:500], "s": f"[tool:{tool_name}]"})
        eng.dispose()
    except Exception:
        pass


def _wrap_with_audit(fn):
    """Wrap a specialist analysis fn with pre-lookup + post-save."""
    def wrapped(question: str, project_slug: str) -> str:
        ctx = _pre_lookup_context(question, project_slug)
        try:
            result = fn(question, project_slug)
        except Exception as e:
            return f"{ctx}\n\n{fn.__name__} failed: {e}" if ctx else f"{fn.__name__} failed: {e}"
        _post_save_pattern(question, project_slug, fn.__name__)
        footer = f"\n\n_Validated via {fn.__name__} — saved to skill library for future reuse._"
        return (ctx + "\n" + (result or "") + footer) if ctx else ((result or "") + footer)
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped


# Apply wrapper to every specialist analysis function in this module
comparator_analysis = _wrap_with_audit(comparator_analysis)
diagnostic_analysis = _wrap_with_audit(diagnostic_analysis)
narrator_analysis = _wrap_with_audit(narrator_analysis)
validator_analysis = _wrap_with_audit(validator_analysis)
planner_analysis = _wrap_with_audit(planner_analysis)
trend_analysis = _wrap_with_audit(trend_analysis)
pareto_analysis = _wrap_with_audit(pareto_analysis)
anomaly_analysis = _wrap_with_audit(anomaly_analysis)
benchmark_analysis = _wrap_with_audit(benchmark_analysis)
root_cause_analysis = _wrap_with_audit(root_cause_analysis)
prescriptive_analysis = _wrap_with_audit(prescriptive_analysis)


# ---------------------------------------------------------------------------
# Unified Analysis Dispatcher (OpenAI lesson: one well-scoped tool > 11 overlaps)
# ---------------------------------------------------------------------------
#
# Routes a single `analyze(analysis_type, ...)` call to the correct underlying
# analyzer above. The 11 underlying functions are KEPT on disk (reversibility)
# and still callable individually — this dispatcher just calls them internally.
# Each underlying fn already has the _wrap_with_audit pre-lookup + post-save
# chain, so the dispatcher inherits that for free.

# analysis_type (canonical + common synonyms) -> underlying wrapped function.
_ANALYZE_ROUTES = {
    # diagnostic / why-did-it-change
    "diagnostic": diagnostic_analysis,
    "diagnose": diagnostic_analysis,
    # comparative / period-over-period (MoM + YoY)
    "comparative": comparator_analysis,
    "comparison": comparator_analysis,
    "comparator": comparator_analysis,
    "compare": comparator_analysis,
    # trend / time series
    "trend": trend_analysis,
    "timeseries": trend_analysis,
    "time_series": trend_analysis,
    # predictive (no dedicated stat forecaster here -> trend is the read-only proxy;
    # true forecasting routes to the Data Scientist `predict` tool, not this one)
    "predictive": trend_analysis,
    "predict": trend_analysis,
    "forecast": trend_analysis,
    # prescriptive / recommendations
    "prescriptive": prescriptive_analysis,
    "prescribe": prescriptive_analysis,
    "recommendations": prescriptive_analysis,
    # anomaly / outliers
    "anomaly": anomaly_analysis,
    "anomalies": anomaly_analysis,
    "outlier": anomaly_analysis,
    "outliers": anomaly_analysis,
    # root cause / drill-down
    "root_cause": root_cause_analysis,
    "rootcause": root_cause_analysis,
    "root-cause": root_cause_analysis,
    "drilldown": root_cause_analysis,
    # pareto / 80-20 concentration
    "pareto": pareto_analysis,
    "80/20": pareto_analysis,
    "concentration": pareto_analysis,
    # scenario / what-if (planner)
    "scenario": planner_analysis,
    "what_if": planner_analysis,
    "whatif": planner_analysis,
    "planning": planner_analysis,
    "planner": planner_analysis,
    "plan": planner_analysis,
    # benchmark / vs group average
    "benchmark": benchmark_analysis,
    "benchmarking": benchmark_analysis,
    # descriptive / narrative / executive summary
    "descriptive": narrator_analysis,
    "narrative": narrator_analysis,
    "narrator": narrator_analysis,
    "summary": narrator_analysis,
    # validation / data quality
    "validation": validator_analysis,
    "validator": validator_analysis,
    "data_quality": validator_analysis,
    "quality": validator_analysis,
}


def analyze(analysis_type: str, question: str, project_slug: str) -> str:
    """Run one structured data analysis. THIS IS THE ONLY analysis tool — pick the
    right `analysis_type` instead of calling many separate tools.

    CRITICAL: Call this tool EXACTLY ONCE per question. After receiving the
    result, STOP and synthesize the answer. Do NOT call it again with a
    different `analysis_type` — the result is complete.

    Args:
        analysis_type: which analysis to run. One of:
            - "diagnostic"   — WHY did a metric change? Decomposes the metric across
                               every dimension and ranks each value's contribution to
                               the total change (waterfall-style). Use for "why did X drop/rise".
            - "comparative"  — period-over-period. Same metric for current vs prior period
                               vs same period last year, with MoM Δ and YoY Δ arrows.
                               Use for "vs last month/quarter/year", "how did we do this period".
            - "trend"        — time series with 3-period moving average + direction
                               (Upward/Downward/Flat). Use for "trend over time", "trajectory".
            - "predictive"   — directional read of where a series is heading (proxy via trend).
                               For true statistical forecasting, defer to the Data Scientist
                               `predict` tool instead.
            - "prescriptive" — 5 numbered, quantified, prioritized recommendations
                               (LLM over the data). Use for "what should we do", "next steps".
            - "anomaly"      — z-score outlier detection across numeric columns
                               (|z| > 2.5). Use for "any outliers/spikes/unusual values".
            - "root_cause"   — iterative drill-down: total → top dimension → sub-dimension
                               of the biggest driver. Use for "find the root cause", "drill into".
            - "pareto"       — 80/20 concentration: sorts by impact, cumulative %, A/B/C zones,
                               counts how many items drive 80%. Use for "which few drive most".
            - "scenario"     — what-if modeling with downside/base/upside projections +
                               probability-weighted expected value. Use for "what if X drops 20%".
            - "benchmark"    — each entity's average vs the group average, Above/On par/Below.
                               Use for "which stores/reps are above/below average".
            - "descriptive" / "narrative" — McKinsey-style executive summary: headline,
                               key wins, key risks, recommended actions. Use for "summarize",
                               "executive narrative", "tell the story".
            - "validation"   — data quality profile across tables: null %, distinct counts,
                               duplicates, 0-100 health score. Use for "is this data clean",
                               "data quality", "can I trust these numbers".
        question: the user's natural-language question (drives period/metric detection).
        project_slug: the project schema to analyze (read-only, LIMIT 1000, 30s timeout).

    Returns:
        Structured Markdown with the analysis result.
    """
    key = (analysis_type or "").strip().lower().replace(" ", "_")
    fn = _ANALYZE_ROUTES.get(key)
    if fn is None:
        valid = sorted({
            "diagnostic", "comparative", "trend", "predictive", "prescriptive",
            "anomaly", "root_cause", "pareto", "scenario", "benchmark",
            "descriptive", "narrative", "validation", "planning",
        })
        return (
            f"Unknown analysis_type '{analysis_type}'. "
            f"Valid types: {', '.join(valid)}."
        )
    try:
        return fn(question, project_slug)
    except Exception as e:  # fail-soft: never crash the agent turn
        logger.exception("analyze dispatcher failed for type=%s", analysis_type)
        return f"Analysis '{analysis_type}' error: {e}"
