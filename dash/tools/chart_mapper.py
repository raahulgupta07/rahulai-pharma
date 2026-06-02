"""
chart_mapper.py — Shape-driven mapper from SQL result rows to pptxgenjs chart specs.

Feeds the native python-pptx renderer (layout="chart"). No LLM —
pure heuristics over column types and row counts.

Public API:
    detect_column_types(rows) -> {"numeric": [...], "categorical": [...], "temporal": [...]}
    map_sql_result_to_chart(rows, query_meta) -> dict | None
    build_chart_slide(rows, query_meta, eyebrow, title) -> dict | None
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable


# ---------- type detection ---------------------------------------------------

_DATE_HINTS = ("-", "/", ":")


def _try_float(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if v is None:
        return False
    try:
        float(str(v).replace(",", "").strip().rstrip("%"))
        return True
    except (ValueError, TypeError):
        return False


def _try_temporal(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    if not isinstance(v, str):
        return False
    s = v.strip()
    if len(s) < 4:
        return False
    # iso-ish: 2026-01, 2026-01-15, 2026/01/15, 2026-01-15T...
    if any(h in s for h in _DATE_HINTS) and any(c.isdigit() for c in s[:4]):
        # require at least 3 leading digits to avoid false positives like "A-1"
        if sum(c.isdigit() for c in s[:4]) >= 3:
            return True
    return False


def detect_column_types(rows: list[dict]) -> dict[str, list[str]]:
    """Classify columns as numeric / temporal / categorical using first ≤5 values."""
    out = {"numeric": [], "categorical": [], "temporal": []}
    if not rows:
        return out
    cols = list(rows[0].keys())
    sample = rows[:5]
    for c in cols:
        vals = [r.get(c) for r in sample if r.get(c) is not None]
        if not vals:
            out["categorical"].append(c)
            continue
        # temporal beats numeric (e.g. "2026-01" parses as neither pure float nor pure date trivially)
        if all(_try_temporal(v) for v in vals):
            out["temporal"].append(c)
        elif all(_try_float(v) for v in vals):
            out["numeric"].append(c)
        else:
            out["categorical"].append(c)
    return out


# ---------- helpers ----------------------------------------------------------

def _fmt_label(v: Any) -> str:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v) if v is not None else ""


def _to_float(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip().rstrip("%"))
    except (ValueError, TypeError):
        return 0.0


def _is_share_like(col_name: str, values: list[float]) -> bool:
    name = (col_name or "").lower()
    if any(tok in name for tok in ("pct", "share", "%", "percent", "ratio")):
        return True
    total = sum(values)
    # share that sums to ~1.0 or ~100
    if 0.95 <= total <= 1.05:
        return True
    if 95.0 <= total <= 105.0 and all(0 <= v <= 100 for v in values):
        return True
    return False


# ---------- core mapper ------------------------------------------------------

def map_sql_result_to_chart(rows: list[dict], query_meta: dict) -> dict | None:
    """Return a chart_data spec dict or None if not chartable."""
    if not rows:
        return None

    types = detect_column_types(rows)
    n_rows = len(rows)
    n_num = len(types["numeric"])
    n_cat = len(types["categorical"])
    n_tmp = len(types["temporal"])
    total_cols = n_num + n_cat + n_tmp

    # Too wide + too long → caller should fall back to table layout
    if total_cols >= 4 and n_rows > 7:
        return None

    # --- branch A: 1 numeric + 1 temporal → line ---
    if n_num == 1 and n_tmp == 1 and 3 <= n_rows <= 24:
        tcol = types["temporal"][0]
        ncol = types["numeric"][0]
        labels = [_fmt_label(r.get(tcol)) for r in rows]
        values = [_to_float(r.get(ncol)) for r in rows]
        return {
            "chart_type": "line",
            "chart_data": {
                "labels": labels,
                "series": [{"name": ncol, "values": values}],
            },
        }

    # --- branch B/C: 1 numeric + 1 categorical → bar or pie ---
    if n_num == 1 and n_cat == 1 and 2 <= n_rows <= 12:
        ccol = types["categorical"][0]
        ncol = types["numeric"][0]
        labels = [_fmt_label(r.get(ccol)) for r in rows]
        values = [_to_float(r.get(ncol)) for r in rows]
        chart_type = "bar"
        if 2 <= n_rows <= 7 and _is_share_like(ncol, values):
            chart_type = "pie"
        return {
            "chart_type": chart_type,
            "chart_data": {
                "labels": labels,
                "series": [{"name": ncol, "values": values}],
            },
        }

    # --- branch D: 2+ numeric + 1 categorical → multi-series bar ---
    if n_num >= 2 and n_cat == 1 and n_rows <= 8:
        ccol = types["categorical"][0]
        labels = [_fmt_label(r.get(ccol)) for r in rows]
        series = [
            {"name": nc, "values": [_to_float(r.get(nc)) for r in rows]}
            for nc in types["numeric"]
        ]
        return {
            "chart_type": "bar",
            "chart_data": {"labels": labels, "series": series},
        }

    return None


# ---------- slide-builder convenience ---------------------------------------

def build_chart_slide(
    rows: list[dict],
    query_meta: dict,
    eyebrow: str,
    title: str,
) -> dict | None:
    """Wrap mapper output into a full slide spec for the python-pptx renderer (layout='chart')."""
    spec = map_sql_result_to_chart(rows, query_meta)
    if spec is None:
        return None
    src_table = query_meta.get("source_table") or "query"
    rowcount = query_meta.get("rowcount", len(rows))
    return {
        "layout": "chart",
        "eyebrow": eyebrow,
        "title": title,
        "chart_type": spec["chart_type"],
        "chart_data": spec["chart_data"],
        "source": f"Source: {src_table} · n={rowcount}",
    }
