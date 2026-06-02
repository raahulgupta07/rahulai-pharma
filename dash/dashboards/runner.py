"""Execute SQLs in spec, return data per cell."""
from __future__ import annotations

import logging
import re

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)
ALLOWED_PREFIXES = ("SELECT", "WITH")


def _safe(sql: str) -> bool:
    s = sql.strip().upper()
    if not any(s.startswith(p) for p in ALLOWED_PREFIXES):
        return False
    # Allow trailing semicolon but no embedded ones
    return ";" not in sql.rstrip().rstrip(";")


def _try_repair_sql(sql: str, error: str, project_slug: str) -> str | None:
    """Ask cheap LLM to rewrite SQL using real tables when relation-not-found error fires."""
    m = re.search(r'relation "([^"]+)" does not exist', error)
    if not m:
        m = re.search(r'UndefinedTable.*?"([^"]+)"', error)
    if not m:
        return None
    bad = m.group(1)
    try:
        from dash.dashboards.planner import _real_tables
        real = _real_tables(project_slug)
    except Exception as e:
        logger.debug(f"_real_tables failed during repair: {e}")
        return None
    if not real:
        return None
    table_list = "\n".join(
        f'- {t["qualified"]} cols={[c[0] for c in t["cols"][:8]]}'
        for t in real
    )
    fix_prompt = f"""SQL failed: table {bad} not found.
SQL: {sql}
Available tables:
{table_list}
Output ONLY corrected SQL using existing tables. No fences, no preamble."""
    try:
        from dash.settings import training_llm_call
        try:
            fixed = training_llm_call(fix_prompt, task="extraction")
        except Exception:
            fixed = training_llm_call(fix_prompt, task="lite")
        if fixed and ("SELECT" in fixed.upper() or "WITH" in fixed.upper()):
            cleaned = fixed.strip().strip("`").strip()
            # strip ```sql fences
            cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            return cleaned.strip()
    except Exception as e:
        logger.debug(f"sql repair llm call failed: {e}")
    return None


def run_cell(cell: dict, engine, project_slug: str | None = None) -> dict:
    """Run cell's SQL, return {data, error}. Retries once via LLM repair on table-not-found.

    Priority order for resolving cell data:
      1. cell.rows (inline data baked at build time — used by versioned dashboards)
      2. cell.config.echarts_options (pre-rendered ECharts spec from agent stage 7)
      3. cell.sql OR cell.config.sql (re-executes SQL against live DB)
      4. Otherwise: no error, just empty data — Cell.svelte renders narrative cleanly.
    """
    ctype = cell.get("type")

    # 1. Inline rows (preferred — cached at build time, no re-execute needed)
    inline_rows = cell.get("rows") or (cell.get("config") or {}).get("rows")
    if isinstance(inline_rows, list) and inline_rows:
        cols = cell.get("columns") or (cell.get("config") or {}).get("columns") or list(inline_rows[0].keys() if isinstance(inline_rows[0], dict) else [])
        if ctype == "kpi":
            try:
                first = inline_rows[0]
                if isinstance(first, dict):
                    val = list(first.values())[0] if first else None
                else:
                    val = first
                return {"value": val}
            except Exception:
                return {"value": None}
        elif ctype == "table":
            return {"rows": inline_rows[:50], "cols": cols}
        else:
            return {"rows": inline_rows[:500], "cols": cols}

    # 2. Pre-rendered ECharts options (chart cells from agent stage 7)
    eo = (cell.get("config") or {}).get("echarts_options") or cell.get("echarts_options")
    if isinstance(eo, dict) and eo.get("series"):
        # let Cell.svelte render via echarts_options directly; return empty rows so no "no data"
        return {"rows": [], "cols": [], "has_echarts": True}

    # 3. Resolve SQL from either location (top-level OR config.sql)
    sql = ((cell.get("config") or {}).get("sql") or cell.get("sql") or "").strip()
    if not sql:
        # No SQL + no inline data: clean empty state (no scary error).
        # Cell.svelte will render narrative cleanly.
        return {"data": None}
    if not _safe(sql):
        return {"data": None, "error": "unsafe sql"}

    df = None
    err = None
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
    except Exception as e:
        err = str(e)
        logger.warning(f"cell sql failed: {err[:200]}")
        repaired = _try_repair_sql(sql, err, project_slug) if project_slug else None
        if repaired and _safe(repaired):
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(text(repaired), conn)
                logger.info(f"sql repair succeeded for cell {cell.get('id')}")
                err = None
            except Exception as e2:
                return {"data": None, "error": f"{err[:100]} | retry: {str(e2)[:100]}"}
        else:
            return {"data": None, "error": err[:200]}

    if df is None:
        return {"data": None, "error": err or "unknown"}

    ctype = cell.get("type")
    if ctype == "kpi":
        val = None
        if not df.empty:
            val = df.iloc[0, 0]
        if val is None or (hasattr(pd, "isna") and pd.isna(val)):
            return {"value": None}
        try:
            return {"value": float(val) if hasattr(val, "item") or isinstance(val, (int, float)) else val}
        except Exception:
            return {"value": val}
    elif ctype == "chart":
        return {"rows": df.head(500).to_dict(orient="records")}
    elif ctype == "table":
        return {"rows": df.head(50).to_dict(orient="records"), "cols": list(df.columns)}
    return {"rows": df.head(50).to_dict(orient="records")}


def run_spec(spec: dict, project_slug: str) -> dict:
    """Run all cell SQLs. Return {cell_id: data_dict}."""
    from db.session import get_project_readonly_engine
    eng = get_project_readonly_engine(project_slug)
    out: dict = {}
    for cell in spec.get("cells", []):
        out[cell.get("id", "")] = run_cell(cell, eng, project_slug=project_slug)
    return out
