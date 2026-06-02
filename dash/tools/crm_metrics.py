"""
CRM Metric Registry (deterministic, definition-locked)
======================================================

Why this exists
---------------
Benchmark (2026-05) showed the LLM re-derives metric SQL per chat and silently
drops filters under some tiers:

  * Q8 drop-off absolute  → 2,632 instead of 2,630  (dropped call_category='Retention Call')
  * Q10 drop-off reasons  → inflated tables          (same dropped filter)
  * Q3 contribution %      → wrong denominator         (per-month vs total completed-outcome)

These are not data errors — they are definition drift. The fix is to NEVER let
the model invent the filter set for a known business metric. Each metric below
has ONE canonical SQL definition. The agent picks a metric by name; this tool
runs the locked SQL against the project read-only engine and returns exact rows.

Truth values verified against proj_demo_pg_crm (Jan–Jun 2025):
  total_leads          = 1,544
  new_users (all)      = 658     (Jan 310)   ← feedback's 644/299 was stale
  drop_off_users       = 2,630
  contribution         = Successful 7,526 (64.3%) / Unsuccessful 4,179 (35.7%)
  Ensure top drop-off reason "No purchase - normal meals is enough" = 636

Engine rule: reads go through get_project_readonly_engine (same resolution the
Analyst SQL tool uses); the cached engine is never disposed.
"""

from __future__ import annotations

import calendar
import json
import logging
import re

from agno.tools import tool
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

# ── Column names in the CRM monthly tables (exact) ────────────────────────────
C_STATUS   = "status"
C_OUTCOME  = "call_outcome"
C_CAT      = "call_category__affiliate_value_name"
C_TYPE     = "related_brand_relationship__type"
C_RSTATUS  = "related_brand_relationship__status"
C_CHANNEL  = "related_channel_response__channel_type"
C_BRAND    = "related_channel_response__brand"
C_REASON   = "unsuccessful_reason__affiliate_value_name"
C_CITY     = "city"

# ── Dimension → SQL expression (always TRIM categoricals) ─────────────────────
_DIMS = {
    "month":   "month",                 # synthesized literal per source table
    "channel": f"TRIM({C_CHANNEL})",
    "brand":   f"TRIM({C_BRAND})",
    "city":    f"TRIM({C_CITY})",
    "reason":  f"TRIM({C_REASON})",
}

# ── Metric registry ───────────────────────────────────────────────────────────
# Each entry:
#   kind   : "count" (single filtered count) | "rate" (num/denom)
#   where  : SQL predicate for a count metric (or the numerator for a rate)
#   denom  : SQL predicate for the denominator (rate only)
#   default_group : default dimensions if caller passes none
#   blurb  : human description used in the tool docstring
_REG: dict[str, dict] = {
    "total_leads": {
        "kind": "count",
        "where": (f"TRIM({C_STATUS})='Completed' AND TRIM({C_OUTCOME})='Unsuccessful' "
                  f"AND TRIM({C_TYPE})='Lead' AND TRIM({C_RSTATUS})='Non_User'"),
        "default_group": [],
        "blurb": "Total leads. Completed + Unsuccessful + type=Lead + status=Non_User. (truth=1,544)",
    },
    "channel_breakdown": {
        "kind": "count",
        "where": (f"TRIM({C_STATUS})='Completed' AND TRIM({C_OUTCOME})='Unsuccessful' "
                  f"AND TRIM({C_TYPE})='Lead' AND TRIM({C_RSTATUS})='Non_User'"),
        "default_group": ["channel", "brand"],
        "blurb": "Lead breakdown by channel/brand (same lead definition as total_leads).",
    },
    "new_users": {
        "kind": "count",
        "where": (f"TRIM({C_STATUS})='Completed' AND TRIM({C_OUTCOME})='Successful' "
                  f"AND TRIM({C_TYPE})='User' AND TRIM({C_RSTATUS})='New'"),
        "default_group": ["month", "channel", "brand"],
        "blurb": "New users. Completed + Successful + type=User + status=New. (truth total=658)",
    },
    "drop_off_users": {
        "kind": "count",
        "where": (f"TRIM({C_CAT})='Retention Call' AND TRIM({C_STATUS})='Completed' "
                  f"AND TRIM({C_OUTCOME})='Unsuccessful' AND TRIM({C_TYPE})='User' "
                  f"AND TRIM({C_RSTATUS})='Lapsed'"),
        "default_group": ["month", "channel", "brand"],
        "blurb": "Drop-off / lapsed users. Retention Call + Completed + Unsuccessful + type=User + status=Lapsed. (truth=2,630)",
    },
    "drop_off_reasons": {
        "kind": "count",
        "where": (f"TRIM({C_CAT})='Retention Call' AND TRIM({C_STATUS})='Completed' "
                  f"AND TRIM({C_OUTCOME})='Unsuccessful' AND TRIM({C_RSTATUS})='Lapsed'"),
        "default_group": ["brand", "reason"],
        "blurb": "Drop-off reasons. Retention Call + Completed + Unsuccessful + status=Lapsed, grouped by reason. (Ensure 'normal meals' truth=636)",
    },
    "recruitment_rate": {
        "kind": "rate",
        "where": (f"TRIM({C_CAT})='Recruitment Call' AND TRIM({C_OUTCOME})='Successful' "
                  f"AND TRIM({C_RSTATUS})='New'"),
        "denom": f"TRIM({C_CAT})='Recruitment Call' AND TRIM({C_STATUS})='Completed'",
        "default_group": ["month", "channel"],
        "blurb": "Recruitment rate = successful-new recruitment calls / completed recruitment calls.",
    },
    "retention_rate": {
        "kind": "rate",
        "where": (f"TRIM({C_CAT})='Retention Call' AND TRIM({C_OUTCOME})='Successful' "
                  f"AND TRIM({C_RSTATUS}) IN ('Retained','Existing')"),
        "denom": f"TRIM({C_CAT})='Retention Call' AND TRIM({C_STATUS})='Completed'",
        "default_group": ["month", "channel"],
        "blurb": "Retention rate = successful retention calls (Retained/Existing) / completed retention calls.",
    },
    "drop_off_rate": {
        "kind": "rate",
        "where": (f"TRIM({C_CAT})='Retention Call' AND TRIM({C_STATUS})='Completed' "
                  f"AND TRIM({C_OUTCOME})='Unsuccessful' AND TRIM({C_RSTATUS})='Lapsed'"),
        "denom": f"TRIM({C_CAT})='Retention Call' AND TRIM({C_STATUS})='Completed'",
        "default_group": ["month", "channel"],
        "blurb": "Drop-off rate = lapsed unsuccessful retention calls / completed retention calls.",
    },
    "contribution": {
        # Special-cased below: split of completed calls by outcome with % of
        # total completed-with-outcome. Denominator pinned = Successful+Unsuccessful.
        "kind": "contribution",
        "where": f"TRIM({C_STATUS})='Completed' AND TRIM({C_OUTCOME}) IN ('Successful','Unsuccessful')",
        "default_group": [],
        "blurb": "Successful vs unsuccessful contribution = each outcome's share of completed calls with an outcome. (truth 64.3% / 35.7%)",
    },
}

_MONTH_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[_-]?(\d{4})", re.I)
_MONTHNUM = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def _resolve(project_slug: str | None):
    """(engine, schema) resolved exactly like the Analyst SQL tool. Never disposes."""
    if not project_slug:
        return None, None
    from db import get_project_readonly_engine
    engine = get_project_readonly_engine(project_slug)
    schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]
    return engine, schema


def _month_tables(engine, schema: str) -> list[tuple[str, str]]:
    """Return [(table_name, 'YYYY-MM'), …] for crm monthly tables, sorted by month.
    Falls back to (table, '') for any matching table with no parseable month."""
    try:
        insp = inspect(engine)
        names = insp.get_table_names(schema=schema)
    except Exception:
        return []
    out = []
    for t in names:
        m = _MONTH_RE.search(t)
        if m:
            mon = _MONTHNUM.get(m.group(1).lower())
            label = f"{m.group(2)}-{mon:02d}" if mon else ""
            out.append((t, label))
    out.sort(key=lambda x: x[1])
    return out


def _union(schema: str, tables: list[tuple[str, str]], cols: list[str]) -> str:
    """UNION ALL across monthly tables, synthesizing a `month` literal column."""
    sel_cols = ", ".join(cols)
    parts = []
    for t, label in tables:
        parts.append(f"SELECT '{label}' AS month, {sel_cols} FROM \"{schema}\".\"{t}\"")
    return "  UNION ALL\n".join(parts)


def _coerce(o):
    """JSON default — Decimal (from ROUND/AVG) → float."""
    import decimal
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _dumps(obj) -> str:
    return json.dumps(obj, default=_coerce)


def _run(engine, sql: str):
    with engine.connect() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '20s'"))
        rows = conn.execute(text(sql)).fetchall()
    return rows


def _md_table(headers: list[str], rows: list[tuple]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return "\n".join([head, sep, body])


def create_crm_metric_tool(project_slug: str | None = None):
    """Build the deterministic `crm_metric` tool bound to a project."""

    _names = "\n".join(f"      • {k}: {v['blurb']}" for k, v in _REG.items())
    _desc = (
        "AUTHORITATIVE CRM metric calculator. ALWAYS use this for any of the "
        "named business metrics below — do NOT write your own SQL for them, the "
        "filter definitions are locked here and verified against ground truth.\n"
        "Args:\n"
        "  metric (str): one of the registered names.\n"
        "  group_by (str): optional comma list of dimensions from "
        "{month, channel, brand, city, reason}. Empty = metric default.\n"
        "  brand (str): optional single-brand filter (e.g. 'Ensure').\n"
        "Registered metrics:\n" + _names
    )

    @tool(name="crm_metric", description=_desc)
    def crm_metric(metric: str, group_by: str = "", brand: str = "") -> str:
        metric = (metric or "").strip().lower()
        if metric not in _REG:
            return _dumps({
                "ok": False, "error": "UNKNOWN_METRIC", "metric": metric,
                "available": list(_REG.keys()),
            })
        engine, schema = _resolve(project_slug)
        if engine is None:
            return _dumps({"ok": False, "error": "NO_PROJECT"})
        tables = _month_tables(engine, schema)
        if not tables:
            return _dumps({"ok": False, "error": "NO_CRM_TABLES", "schema": schema})

        spec = _REG[metric]
        # resolve grouping dims
        dims = [d.strip().lower() for d in group_by.split(",") if d.strip()] or list(spec["default_group"])
        dims = [d for d in dims if d in _DIMS]
        brand_f = ""
        if brand.strip():
            brand_f = f" AND TRIM({C_BRAND}) = :brand"

        # base column set the UNION must carry (everything any filter/dim touches)
        base_cols = sorted({C_STATUS, C_OUTCOME, C_CAT, C_TYPE, C_RSTATUS,
                            C_CHANNEL, C_BRAND, C_REASON, C_CITY})
        union = _union(schema, tables, base_cols)
        params = {"brand": brand.strip()} if brand_f else {}

        try:
            if spec["kind"] in ("count",):
                gsel = [f"{_DIMS[d]} AS {d}" for d in dims]
                gby = [_DIMS[d] for d in dims]
                sel = (", ".join(gsel) + ", " if gsel else "") + "COUNT(*) AS value"
                grp = ("GROUP BY " + ", ".join(gby) + " ORDER BY " + ", ".join(gby)) if gby else ""
                sql = (f"WITH base AS (\n{union}\n)\n"
                       f"SELECT {sel} FROM base WHERE {spec['where']}{brand_f}\n{grp}")
                rows = _run_p(engine, sql, params)
                total = sum(int(r[-1]) for r in rows)
                headers = dims + ["count"]
                return _dumps({
                    "ok": True, "metric": metric, "definition": spec["where"],
                    "group_by": dims, "brand": brand or None, "total": total,
                    "table_md": _md_table(headers, rows),
                    "rows": [list(r) for r in rows],
                })

            if spec["kind"] == "rate":
                gsel = [f"{_DIMS[d]} AS {d}" for d in dims]
                gby = [_DIMS[d] for d in dims]
                sel = (", ".join(gsel) + ", " if gsel else "")
                grp = ("GROUP BY " + ", ".join(gby) + " ORDER BY " + ", ".join(gby)) if gby else ""
                sql = (
                    f"WITH base AS (\n{union}\n)\n"
                    f"SELECT {sel}"
                    f"SUM(CASE WHEN {spec['where']} THEN 1 ELSE 0 END) AS numerator, "
                    f"SUM(CASE WHEN {spec['denom']} THEN 1 ELSE 0 END) AS denominator, "
                    f"ROUND(100.0 * SUM(CASE WHEN {spec['where']} THEN 1 ELSE 0 END) "
                    f"/ NULLIF(SUM(CASE WHEN {spec['denom']} THEN 1 ELSE 0 END),0), 1) AS rate_pct "
                    f"FROM base WHERE TRUE{brand_f}\n{grp}"
                )
                rows = _run_p(engine, sql, params)
                headers = dims + ["numerator", "denominator", "rate_%"]
                return _dumps({
                    "ok": True, "metric": metric, "numerator_def": spec["where"],
                    "denominator_def": spec["denom"], "group_by": dims, "brand": brand or None,
                    "table_md": _md_table(headers, rows),
                    "rows": [list(r) for r in rows],
                })

            if spec["kind"] == "contribution":
                sql = (
                    f"WITH base AS (\n{union}\n)\n"
                    f"SELECT TRIM({C_OUTCOME}) AS outcome, COUNT(*) AS value, "
                    f"ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),1) AS pct "
                    f"FROM base WHERE {spec['where']}{brand_f} "
                    f"GROUP BY TRIM({C_OUTCOME}) ORDER BY value DESC"
                )
                rows = _run_p(engine, sql, params)
                headers = ["outcome", "count", "contribution_%"]
                total = sum(int(r[1]) for r in rows)
                return _dumps({
                    "ok": True, "metric": metric, "definition": spec["where"],
                    "base_total": total, "brand": brand or None,
                    "table_md": _md_table(headers, rows),
                    "rows": [list(r) for r in rows],
                })
        except Exception as e:
            logger.warning("crm_metric %s failed: %s", metric, e)
            return _dumps({"ok": False, "error": "QUERY_FAILED", "detail": str(e)[:300]})

        return _dumps({"ok": False, "error": "UNHANDLED_KIND"})

    return crm_metric


def _run_p(engine, sql: str, params: dict):
    with engine.connect() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '20s'"))
        rows = conn.execute(text(sql), params).fetchall()
    return rows
