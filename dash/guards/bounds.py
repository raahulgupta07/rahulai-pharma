"""Vcol bounds validator (H12).

Per-vcol optional `bounds: {min, max, nullable}` block on MDL virtual
columns. Post-exec row scan checks every value against declared bounds,
logs anomalies (e.g., unit_cost < 0 means data-quality issue OR vcol
expression bug).

Bounds shape (added to vcol dict in MDL_PACK):

    {
      "name": "extended_value",
      "expression": "qty * unit_cost",
      "bounds": {"min": 0, "max": null, "nullable": false}
    }

Usage:

    from dash.guards import check_bounds
    report = check_bounds(slug, table="sales", columns_returned=["extended_value"],
                          rows=[{"extended_value": -50.0}, ...])
    # → {ok, anomalies: [{col, value, rule, row_idx}], total_rows, total_anomalies}

Caller (chat path, post-exec) logs to trace panel + optionally to
dash_data_quality_alerts. Never blocks the chat response. Fail-soft.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ANOMALIES = 50  # cap report size


def _load_vcol_bounds(slug: str, table: str) -> dict[str, dict]:
    """Return {col_name: bounds_dict} for the MDL model whose raw_table=table.

    Falls back to empty dict if no MDL bounds declared. Cached via
    load_models() 5-min TTL.
    """
    try:
        from dash.semantic import load_models
        models = load_models(slug) or {}
    except Exception as e:
        logger.debug(f"load_models failed in bounds: {e}")
        return {}
    out: dict[str, dict] = {}
    for _mname, m in models.items():
        if (m.get("raw_table") or "").lower() != (table or "").lower():
            continue
        for vc in m.get("virtual_columns") or []:
            b = vc.get("bounds")
            if b and isinstance(b, dict):
                out[vc["name"]] = b
    return out


def _violates(value: Any, bounds: dict) -> str | None:
    """Return rule-name violated or None. e.g., 'below_min', 'above_max', 'null_disallowed'."""
    if value is None:
        if bounds.get("nullable") is False:
            return "null_disallowed"
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None  # non-numeric, can't bound-check
    lo = bounds.get("min")
    hi = bounds.get("max")
    if lo is not None and v < lo:
        return f"below_min({lo})"
    if hi is not None and v > hi:
        return f"above_max({hi})"
    return None


def check_bounds(slug: str, table: str, columns_returned: list[str],
                 rows: list[dict]) -> dict:
    """Scan rows for vcol-bounds violations.

    Args:
      slug: project slug
      table: raw table name the SQL ran against
      columns_returned: SELECT projection list (matched against vcol names)
      rows: list of dicts (key=column, value=cell)

    Returns:
      {ok: True, total_rows: int, total_anomalies: int,
       anomalies: [{col, value, rule, row_idx}], by_column: {col: count}}
    """
    try:
        bounds_map = _load_vcol_bounds(slug, table)
        if not bounds_map:
            return {"ok": True, "total_rows": len(rows or []),
                    "total_anomalies": 0, "anomalies": [], "by_column": {}}
        # Only check columns we have bounds for AND that appear in projection
        relevant = {c: bounds_map[c] for c in columns_returned
                    if c in bounds_map}
        if not relevant:
            return {"ok": True, "total_rows": len(rows or []),
                    "total_anomalies": 0, "anomalies": [], "by_column": {}}
        anomalies: list[dict] = []
        by_col: dict[str, int] = {}
        for i, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            for col, bnd in relevant.items():
                rule = _violates(row.get(col), bnd)
                if rule is not None:
                    by_col[col] = by_col.get(col, 0) + 1
                    if len(anomalies) < _MAX_ANOMALIES:
                        anomalies.append({
                            "col": col, "value": row.get(col),
                            "rule": rule, "row_idx": i,
                        })
        total_anom = sum(by_col.values())
        if total_anom:
            logger.info(
                f"bounds violations slug={slug} table={table}: "
                f"{total_anom} across {len(by_col)} col(s)"
            )
        return {
            "ok": True,
            "total_rows": len(rows or []),
            "total_anomalies": total_anom,
            "anomalies": anomalies,
            "by_column": by_col,
        }
    except Exception as e:
        logger.debug(f"check_bounds failed: {e}")
        return {"ok": True, "total_rows": 0, "total_anomalies": 0,
                "anomalies": [], "by_column": {}}
