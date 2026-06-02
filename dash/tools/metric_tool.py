"""
Metric Tool
===========
Generic agent tool for DB-backed, per-project configurable metrics.
Mirrors the crm_metrics.create_crm_metric_tool pattern:
  - fetches VERIFIED definitions from the DB at build time for the description
  - at call time: load_definition → run_metric → return JSON

The tool instructs the agent: "AUTHORITATIVE metric calculator.
For any of the listed business metrics ALWAYS call this; do NOT write your
own SQL — definitions are locked + user-verified."
"""

from __future__ import annotations

import json
import logging

from agno.tools import tool

from dash.tools.metric_compiler import (
    list_definitions,
    load_definition,
    run_metric,
)

logger = logging.getLogger(__name__)


def _coerce(o):
    """JSON default — Decimal → float."""
    import decimal
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _dumps(obj) -> str:
    return json.dumps(obj, default=_coerce)


def create_metric_tool(project_slug: str):
    """Build the generic `metric` tool bound to *project_slug*.

    Fetches VERIFIED definitions at build time for the tool description.
    Fail-soft: if no verified definitions exist, falls back to a generic
    description so tool assembly never breaks.
    """
    # Build time: introspect VERIFIED definitions for the description
    try:
        verified = list_definitions(project_slug, status="verified")
    except Exception:
        verified = []

    if verified:
        _metric_lines = "\n".join(
            f"      • {d['name']}: {d.get('description') or d.get('kind','?')}"
            for d in verified
        )
        _name_list = [d["name"] for d in verified]
    else:
        _metric_lines = "      (no verified metrics yet — add definitions via the Metrics UI)"
        _name_list = []

    _desc = (
        "AUTHORITATIVE metric calculator. For any of the listed business metrics "
        "ALWAYS call this tool; do NOT write your own SQL — definitions are locked "
        "here and user-verified against ground truth. "
        "Args:\n"
        "  name (str): metric name (case-insensitive) or a known synonym.\n"
        "  group_by (str): optional comma-separated list of dimension names. "
        "Empty string = use metric default.\n"
        "  filters (str): optional JSON array of extra filter objects "
        "[{\"col\":\"x\",\"op\":\"=\",\"value\":\"y\"}]. Empty string = no extra filters.\n"
        "Registered verified metrics:\n"
        + _metric_lines
    )

    @tool(name="metric", description=_desc)
    def metric(name: str, group_by: str = "", filters: str = "") -> str:
        """Run a DB-locked business metric and return exact results."""
        clean_name = (name or "").strip()
        if not clean_name:
            return _dumps({
                "ok": False,
                "error": "MISSING_NAME",
                "hint": "Provide a metric name.",
                "available": _name_list,
            })

        # Load definition (exact name or synonym, case-insensitive)
        defn = load_definition(project_slug, clean_name)
        if defn is None:
            all_defs = list_definitions(project_slug)
            available = [d["name"] for d in all_defs]
            return _dumps({
                "ok": False,
                "error": "UNKNOWN_METRIC",
                "metric": clean_name,
                "available": available,
            })

        # Parse group_by
        dims = [d.strip().lower() for d in group_by.split(",") if d.strip()] if group_by else None

        # Parse extra filters
        extra: list | None = None
        if filters.strip():
            try:
                parsed = json.loads(filters)
                if isinstance(parsed, list):
                    extra = parsed
            except json.JSONDecodeError as exc:
                return _dumps({
                    "ok": False,
                    "error": "BAD_FILTERS_JSON",
                    "detail": str(exc)[:200],
                    "hint": "filters must be a JSON array of {col,op,value} objects.",
                })

        result = run_metric(
            project_slug,
            defn,
            group_by=dims,
            extra_filters=extra,
        )
        return _dumps(result)

    return metric
