"""
Visualization Agent
===================

Auto-detects the best chart type from data shape and generates
ECharts config JSON. Called by Analyst after getting query results.
Returns config tagged with [CHART_CONFIG:...] for frontend rendering.

All functions are plain functions (no classes). Rules engine picks
chart type instantly (no LLM); LLM fallback only when uncertain.
"""

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brutalist dark theme colors
# ---------------------------------------------------------------------------

COLORS = [
    "#00fc40",  # green (primary / positive)
    "#0078d4",  # blue
    "#ff9d00",  # orange
    "#e74c3c",  # red (negative)
    "#9b59b6",  # purple
    "#1abc9c",  # teal
    "#f39c12",  # amber
    "#e91e63",  # pink
]

COLOR_POSITIVE = "#00fc40"
COLOR_NEGATIVE = "#e74c3c"

# ---------------------------------------------------------------------------
# 1. Main tool function
# ---------------------------------------------------------------------------

def auto_visualize(question: str, project_slug: str, data: list[dict] | None = None) -> str:
    """Generate an ECharts config from the last SQL result or supplied data.

    Parameters
    ----------
    question : str
        The user's original question (used for chart-type hinting).
    project_slug : str
        Project identifier. Used to fetch last query result if *data* is None.
    data : list[dict] | None
        Row dicts straight from the query. When None the function looks up
        the most recent result stored in ``dash_query_plans``.

    Returns
    -------
    str
        ``[CHART_CONFIG:<json>]`` tag the frontend parses and renders.
    """
    try:
        # -- Resolve data --------------------------------------------------
        if data is None:
            data = _fetch_last_result(project_slug)
        if not data:
            return "[CHART_CONFIG:{}]"

        # -- Detect columns ------------------------------------------------
        columns = _infer_columns(data)
        row_count = len(data)

        # -- Pick chart type (smart: rules-first, LLM-on-ambiguity) --------
        chart_type, chart_via, chart_reason = _select_chart_type(question, columns, row_count)

        # -- Build ECharts config ------------------------------------------
        title = _derive_title(question)
        config = _generate_echarts_config(chart_type, data, columns, title)
        if isinstance(config, dict):
            config["chart_via"] = chart_via       # rules | llm | rules-fallback
            config["chart_reason"] = chart_reason  # why this chart was chosen

        return f"[CHART_CONFIG:{json.dumps(config, default=str)}]"

    except Exception as e:
        logger.exception("auto_visualize failed")
        return f"[CHART_CONFIG:{{}}]"


# ---------------------------------------------------------------------------
# 2. Chart type detection (rules engine, no LLM)
# ---------------------------------------------------------------------------

def _detect_chart_type(columns: list[dict], row_count: int, question: str) -> str:
    """Pick the best chart type from column metadata and question keywords.

    Parameters
    ----------
    columns : list[dict]
        ``[{"name": "store", "dtype": "object"}, ...]``
    row_count : int
        Number of data rows.
    question : str
        Natural-language question for keyword hints.

    Returns
    -------
    str
        One of bar, line, pie, grouped_bar, scatter, kpi, histogram, heatmap.
    """
    q = question.lower()

    date_cols = [
        c for c in columns
        if any(d in c["name"].lower() for d in ("date", "month", "year", "quarter", "week", "period"))
    ]
    numeric_cols = [
        c for c in columns
        if c["dtype"] in ("int64", "float64", "int32", "float32")
    ]
    category_cols = [
        c for c in columns
        if c["dtype"] == "object" and c not in date_cols
    ]

    # --- Forced by question keywords ---
    if any(w in q for w in ("trend", "over time", "monthly", "timeline")):
        return "line"
    if any(w in q for w in ("share", "composition", "breakdown", "split")):
        return "pie"
    if any(w in q for w in ("distribution", "histogram")):
        return "histogram"
    if any(w in q for w in ("correlation", "scatter", "relationship")):
        return "scatter"
    if any(w in q for w in ("heatmap", "matrix")):
        return "heatmap"

    # --- By data shape ---
    if row_count == 1 and len(numeric_cols) >= 2:
        return "kpi"
    if row_count <= 3 and len(numeric_cols) >= 3:
        return "kpi"
    if date_cols and numeric_cols:
        return "line"
    if len(category_cols) == 1 and len(numeric_cols) == 1:
        return "pie" if row_count <= 8 else "bar"
    if len(category_cols) == 1 and len(numeric_cols) >= 2:
        return "grouped_bar"
    if len(numeric_cols) == 2 and not category_cols:
        return "scatter"
    if row_count > 20:
        return "bar"

    return "bar"


# ---------------------------------------------------------------------------
# 2b. Smart selector — rules-first, LLM-on-ambiguity (mirrors complexity router)
# ---------------------------------------------------------------------------

_VIZ_TYPES = {"bar", "line", "pie", "grouped_bar", "scatter", "kpi", "histogram", "heatmap"}
_VIZ_FORCE_WORDS = (
    "trend", "over time", "monthly", "timeline", "share", "composition",
    "breakdown", "split", "distribution", "histogram", "correlation",
    "scatter", "relationship", "heatmap", "matrix",
)
_VIZ_CACHE: dict[str, tuple[str, str, str]] = {}
_VIZ_CACHE_MAX = 1000


def _col_sig(columns: list[dict]) -> str:
    return "|".join(f"{c.get('name')}:{c.get('dtype')}" for c in columns)


def _rules_confident(question: str, row_count: int, chart: str) -> bool:
    """True when the rules pick is unambiguous (skip the LLM)."""
    ql = (question or "").lower()
    if any(w in ql for w in _VIZ_FORCE_WORDS):
        return True
    if chart == "kpi" and row_count <= 3:
        return True
    if chart in ("line", "scatter", "grouped_bar", "histogram", "heatmap"):
        return True
    if chart == "pie" and row_count <= 8:
        return True
    return False  # bare "bar" fallback → ambiguous, ask the LLM


def _llm_pick_chart(question: str, columns: list[dict], row_count: int):
    """One cheap LITE call that understands intent + data shape. None on failure."""
    try:
        from dash.settings import training_llm_call
        import json as _json
        import re as _re
        cols = ", ".join(f"{c.get('name')}({c.get('dtype')})" for c in columns)
        prompt = (
            "Pick the single best chart type for this question + data.\n"
            f"Question: {question}\nColumns: {cols}\nRows: {row_count}\n"
            "Types: bar, line, pie, grouped_bar, scatter, kpi, histogram, heatmap.\n"
            "Guide: time series -> line; part-of-whole/share -> pie (<=8 slices) else bar; "
            "rank/compare categories -> bar; one category x 2+ metrics -> grouped_bar; "
            "two numerics relationship -> scatter; single value(s) -> kpi; "
            "value frequency -> histogram; two-dimension matrix -> heatmap.\n"
            'Return STRICT JSON only: {"chart_type":"...","reason":"short"}'
        )
        raw = training_llm_call(prompt, "viz_router") or ""
        txt = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=_re.M).strip()
        m = _re.search(r"\{.*\}", txt, _re.S)
        if not m:
            return None
        o = _json.loads(m.group(0))
        ct = str(o.get("chart_type", "")).strip().lower()
        if ct in _VIZ_TYPES:
            return ct, str(o.get("reason", ""))[:160]
    except Exception:
        logger.debug("viz LLM pick failed", exc_info=True)
    return None


def _select_chart_type(question: str, columns: list[dict], row_count: int) -> tuple[str, str, str]:
    """Return (chart_type, via, reason). via = rules | llm | rules-fallback."""
    key = f"{(question or '').strip().lower()[:120]}::{_col_sig(columns)}::{row_count}"
    hit = _VIZ_CACHE.get(key)
    if hit:
        return hit
    chart = _detect_chart_type(columns, row_count, question)
    if _rules_confident(question, row_count, chart):
        out = (chart, "rules", f"shape/keyword -> {chart}")
    else:
        llm = _llm_pick_chart(question, columns, row_count)
        out = (llm[0], "llm", llm[1]) if llm else (chart, "rules-fallback", f"default -> {chart}")
    if len(_VIZ_CACHE) >= _VIZ_CACHE_MAX:
        for k in list(_VIZ_CACHE)[:100]:
            _VIZ_CACHE.pop(k, None)
    _VIZ_CACHE[key] = out
    return out


# ---------------------------------------------------------------------------
# 3. ECharts config generation
# ---------------------------------------------------------------------------

def _generate_echarts_config(
    chart_type: str,
    data: list[dict],
    columns: list[dict],
    title: str,
) -> dict:
    """Build a complete ECharts option dict for the given chart type."""

    numeric_cols = [c["name"] for c in columns if c["dtype"] in ("int64", "float64", "int32", "float32")]
    category_cols = [c["name"] for c in columns if c["dtype"] == "object"]
    date_cols = [
        c["name"] for c in columns
        if any(d in c["name"].lower() for d in ("date", "month", "year", "quarter", "week", "period"))
    ]

    base_title = {
        "text": title,
        "textStyle": {"color": "#e0e0e0", "fontSize": 14},
    }

    # ---- KPI (big number cards) ----
    if chart_type == "kpi":
        metrics = []
        for col in numeric_cols:
            val = data[0].get(col)
            metrics.append({"label": col, "value": _format_value(val, col)})
        return {"chart_type": "kpi", "metrics": metrics}

    # ---- BAR ----
    if chart_type == "bar":
        cat_col = category_cols[0] if category_cols else (date_cols[0] if date_cols else columns[0]["name"])
        num_col = numeric_cols[0] if numeric_cols else columns[-1]["name"]
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "axis"},
            "xAxis": {
                "type": "category",
                "data": [str(row.get(cat_col, "")) for row in data],
                "axisLabel": {"color": "#999", "rotate": 30},
            },
            "yAxis": {"type": "value", "axisLabel": {"color": "#999"}},
            "series": [{
                "type": "bar",
                "data": [_safe_num(row.get(num_col)) for row in data],
                "itemStyle": {"color": COLORS[0]},
            }],
        }

    # ---- LINE ----
    if chart_type == "line":
        x_col = date_cols[0] if date_cols else (category_cols[0] if category_cols else columns[0]["name"])
        series = []
        for idx, nc in enumerate(numeric_cols[:5]):
            series.append({
                "type": "line",
                "name": nc,
                "data": [_safe_num(row.get(nc)) for row in data],
                "smooth": True,
                "areaStyle": {"opacity": 0.15},
                "itemStyle": {"color": COLORS[idx % len(COLORS)]},
            })
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "axis"},
            "legend": {"textStyle": {"color": "#999"}, "top": 30},
            "xAxis": {
                "type": "category",
                "data": [str(row.get(x_col, "")) for row in data],
                "axisLabel": {"color": "#999", "rotate": 30},
            },
            "yAxis": {"type": "value", "axisLabel": {"color": "#999"}},
            "series": series,
        }

    # ---- PIE ----
    if chart_type == "pie":
        cat_col = category_cols[0] if category_cols else columns[0]["name"]
        num_col = numeric_cols[0] if numeric_cols else columns[-1]["name"]
        pie_data = [
            {"name": str(row.get(cat_col, "")), "value": _safe_num(row.get(num_col))}
            for row in data
        ]
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "series": [{
                "type": "pie",
                "radius": ["40%", "70%"],
                "data": pie_data,
                "label": {"color": "#ccc"},
                "itemStyle": {"borderColor": "#1a1a2e", "borderWidth": 2},
            }],
        }

    # ---- GROUPED BAR ----
    if chart_type == "grouped_bar":
        cat_col = category_cols[0] if category_cols else columns[0]["name"]
        series = []
        for idx, nc in enumerate(numeric_cols[:6]):
            series.append({
                "type": "bar",
                "name": nc,
                "data": [_safe_num(row.get(nc)) for row in data],
                "itemStyle": {"color": COLORS[idx % len(COLORS)]},
            })
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "axis"},
            "legend": {"textStyle": {"color": "#999"}, "top": 30},
            "xAxis": {
                "type": "category",
                "data": [str(row.get(cat_col, "")) for row in data],
                "axisLabel": {"color": "#999", "rotate": 30},
            },
            "yAxis": {"type": "value", "axisLabel": {"color": "#999"}},
            "series": series,
        }

    # ---- SCATTER ----
    if chart_type == "scatter":
        x_col = numeric_cols[0] if len(numeric_cols) >= 2 else columns[0]["name"]
        y_col = numeric_cols[1] if len(numeric_cols) >= 2 else columns[-1]["name"]
        scatter_data = [
            [_safe_num(row.get(x_col)), _safe_num(row.get(y_col))]
            for row in data
        ]
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "item"},
            "xAxis": {"type": "value", "name": x_col, "axisLabel": {"color": "#999"}},
            "yAxis": {"type": "value", "name": y_col, "axisLabel": {"color": "#999"}},
            "series": [{
                "type": "scatter",
                "data": scatter_data,
                "itemStyle": {"color": COLORS[0]},
                "symbolSize": 8,
            }],
        }

    # ---- HISTOGRAM ----
    if chart_type == "histogram":
        num_col = numeric_cols[0] if numeric_cols else columns[-1]["name"]
        values = [_safe_num(row.get(num_col)) for row in data]
        values = [v for v in values if v is not None]
        bins = _bin_values(values)
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "axis"},
            "xAxis": {
                "type": "category",
                "data": [b["label"] for b in bins],
                "axisLabel": {"color": "#999", "rotate": 30},
            },
            "yAxis": {"type": "value", "name": "Count", "axisLabel": {"color": "#999"}},
            "series": [{
                "type": "bar",
                "data": [b["count"] for b in bins],
                "itemStyle": {"color": COLORS[1]},
            }],
        }

    # ---- HEATMAP ----
    if chart_type == "heatmap":
        cat1 = category_cols[0] if len(category_cols) >= 1 else columns[0]["name"]
        cat2 = category_cols[1] if len(category_cols) >= 2 else (columns[1]["name"] if len(columns) > 1 else cat1)
        num_col = numeric_cols[0] if numeric_cols else columns[-1]["name"]
        x_vals = sorted(set(str(row.get(cat1, "")) for row in data))
        y_vals = sorted(set(str(row.get(cat2, "")) for row in data))
        heat_data = []
        for row in data:
            xi = x_vals.index(str(row.get(cat1, ""))) if str(row.get(cat1, "")) in x_vals else 0
            yi = y_vals.index(str(row.get(cat2, ""))) if str(row.get(cat2, "")) in y_vals else 0
            heat_data.append([xi, yi, _safe_num(row.get(num_col))])
        all_vals = [h[2] for h in heat_data if h[2] is not None]
        return {
            "title": base_title,
            "backgroundColor": "transparent",
            "tooltip": {"trigger": "item"},
            "xAxis": {"type": "category", "data": x_vals, "axisLabel": {"color": "#999"}},
            "yAxis": {"type": "category", "data": y_vals, "axisLabel": {"color": "#999"}},
            "visualMap": {
                "min": min(all_vals) if all_vals else 0,
                "max": max(all_vals) if all_vals else 1,
                "inRange": {"color": ["#1a1a2e", "#00fc40"]},
                "textStyle": {"color": "#999"},
            },
            "series": [{
                "type": "heatmap",
                "data": heat_data,
                "label": {"show": True, "color": "#ccc"},
            }],
        }

    # ---- Fallback: bar ----
    return _generate_echarts_config("bar", data, columns, title)


# ---------------------------------------------------------------------------
# 4. Value formatting
# ---------------------------------------------------------------------------

def _format_value(val: Any, col_name: str = "") -> str:
    """Format a number for display (KPI cards, tooltips)."""
    if val is None:
        return "N/A"

    try:
        num = float(val)
    except (ValueError, TypeError):
        return str(val)

    cn = col_name.lower()
    is_pct = any(w in cn for w in ("pct", "rate", "margin", "percent", "ratio"))

    if is_pct:
        return f"{num:.1f}%"
    if abs(num) >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    if abs(num) >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if abs(num) >= 1_000:
        return f"{num / 1_000:.1f}K"
    if num == int(num):
        return f"{int(num):,}"
    return f"{num:,.2f}"


# ---------------------------------------------------------------------------
# 5. LLM fallback
# ---------------------------------------------------------------------------

def _llm_chart_suggestion(columns: list[dict], row_count: int, question: str) -> str:
    """Ask the LLM for the best chart type when the rules engine is uncertain."""
    try:
        from dash.settings import training_llm_call
        prompt = (
            f"Columns: {columns}\nRows: {row_count}\nQuestion: {question}\n"
            "Best chart type? Return ONE word: bar/line/pie/scatter/grouped_bar/kpi/histogram"
        )
        result = training_llm_call(prompt, "routing")
        return result.strip().lower() if result else "bar"
    except Exception:
        logger.warning("LLM chart suggestion failed, defaulting to bar")
        return "bar"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_last_result(project_slug: str) -> list[dict]:
    """Pull the most recent query result from dash_query_plans."""
    try:
        from db import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT result_data FROM dash_query_plans "
                "WHERE project_slug = :slug AND result_data IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ), {"slug": project_slug}).fetchone()
            if row and row[0]:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except Exception:
        logger.debug("Could not fetch last result for %s", project_slug)
    return []


def _infer_columns(data: list[dict]) -> list[dict]:
    """Infer column metadata from the first row of data."""
    if not data:
        return []
    sample = data[0]
    cols = []
    for key, val in sample.items():
        if isinstance(val, int):
            dtype = "int64"
        elif isinstance(val, float):
            dtype = "float64"
        else:
            dtype = "object"
        cols.append({"name": key, "dtype": dtype})
    return cols


def _safe_num(val: Any) -> float | None:
    """Coerce a value to float or None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _derive_title(question: str) -> str:
    """Create a short chart title from the question."""
    title = question.strip()
    if len(title) > 60:
        title = title[:57] + "..."
    return title


def _bin_values(values: list[float], num_bins: int = 10) -> list[dict]:
    """Bin numeric values for histogram display."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [{"label": str(lo), "count": len(values)}]
    step = (hi - lo) / num_bins
    bins = []
    for i in range(num_bins):
        edge_lo = lo + i * step
        edge_hi = lo + (i + 1) * step
        count = sum(1 for v in values if edge_lo <= v < edge_hi)
        if i == num_bins - 1:
            count = sum(1 for v in values if edge_lo <= v <= edge_hi)
        label = f"{_format_value(edge_lo)}-{_format_value(edge_hi)}"
        bins.append({"label": label, "count": count})
    return bins
