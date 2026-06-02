"""File query executor.

For sources with dialect='files' (e.g. PPTX/PDF/DOCX/XLSX-extracted tables),
load the actual data from disk + run SELECT/WHERE/GROUP BY via pandas.

Limited SQL subset:
  SELECT [DISTINCT] cols FROM <table>
  [WHERE <conditions>]
  [GROUP BY <cols>]
  [ORDER BY <cols> [ASC|DESC]]
  [LIMIT N]

NOT supported:
  JOINs (federation engine handles cross-source)
  Subqueries
  Window functions

Storage layout:
  knowledge/{slug}/source_<id>/profile/{table}.json   col stats
  knowledge/{slug}/source_<id>/sample/{table}.parquet  full data
  knowledge/{slug}/extracted_tables/{doc}/{table}.parquet  doc-extracted

Tries Parquet first, falls back to CSV → JSON.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")


def execute_file_sql(provider, sql: str, *, max_rows: int = 10000) -> object:
    """Execute SQL against a file-source provider. Returns pandas DataFrame.

    provider: BaseProvider instance with dialect='files'
    """
    try:
        import pandas as pd
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(f"pandas required: {e}")

    parsed = _parse_simple_sql(sql)
    if parsed.get("error"):
        raise RuntimeError(f"file SQL parse: {parsed['error']}")

    table_name = parsed["from"]
    if not table_name:
        raise RuntimeError("FROM clause missing")

    project_slug = getattr(provider, "project_slug", "") or ""
    provider_id = getattr(provider, "id", "") or ""

    # Load table data
    df = _load_table(project_slug, table_name, provider_id)
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # Apply WHERE
    if parsed["where"]:
        try:
            pd_query = _sql_where_to_pandas_query(parsed["where"])
            if pd_query:
                df = df.query(pd_query)
        except Exception as e:
            logger.warning(f"WHERE failed: {e}, returning unfiltered")

    # Apply SELECT projection
    if parsed["select"] and parsed["select"] != ["*"]:
        # Strip table aliases from cols
        clean_cols = [c.split(".")[-1] if "." in c else c for c in parsed["select"]]
        # Keep only those that exist
        clean_cols = [c for c in clean_cols if c in df.columns]
        if clean_cols:
            df = df[clean_cols]

    # GROUP BY
    if parsed["group_by"]:
        gb_cols = [c for c in parsed["group_by"] if c in df.columns]
        if gb_cols:
            df = df.groupby(gb_cols, dropna=False).size().reset_index(name="count")

    # ORDER BY
    if parsed["order_by"]:
        cols = []
        ascendings = []
        for ob in parsed["order_by"]:
            parts = ob.split()
            cols.append(parts[0])
            ascendings.append(len(parts) < 2 or parts[1].upper() != "DESC")
        valid = [(c, a) for c, a in zip(cols, ascendings) if c in df.columns]
        if valid:
            df = df.sort_values(
                by=[c for c, _ in valid],
                ascending=[a for _, a in valid],
            )

    # LIMIT
    n = parsed["limit"] or max_rows
    n = min(n, max_rows)
    return df.head(n)


def _load_table(project_slug: str, table_name: str,
                 provider_id: str = "") -> object:
    """Find table data file on disk + load to DataFrame.

    Tries: Parquet → CSV → JSON dict-of-lists.
    """
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        return None

    base = KNOWLEDGE_DIR / project_slug

    # Search candidate paths
    candidates: list[Path] = []
    if provider_id:
        # Source-tied: knowledge/{slug}/source_<id>/sample/{table}.{ext}
        # provider_id might be like "src42" or "fabric_42"
        m = re.search(r"(\d+)$", provider_id)
        if m:
            sid = m.group(1)
            for ext in ("parquet", "csv", "json"):
                candidates.append(base / f"source_{sid}" / "sample" / f"{table_name}.{ext}")
                candidates.append(base / f"source_{sid}" / "data" / f"{table_name}.{ext}")
        # Doc-extracted
        for ext in ("parquet", "csv", "json"):
            candidates.append(base / "extracted_tables" / provider_id / f"{table_name}.{ext}")

    # Generic fallback
    for ext in ("parquet", "csv", "json"):
        candidates.append(base / "tables" / f"{table_name}.{ext}")
        candidates.append(base / f"{table_name}.{ext}")

    for p in candidates:
        if not p.exists():
            continue
        try:
            if p.suffix == ".parquet":
                return pd.read_parquet(p)
            if p.suffix == ".csv":
                return pd.read_csv(p)
            if p.suffix == ".json":
                data = json.loads(p.read_text())
                # JSON could be list-of-dicts or dict-of-lists
                if isinstance(data, list):
                    return pd.DataFrame(data)
                if isinstance(data, dict):
                    # Try as dict-of-lists (col → values)
                    return pd.DataFrame(data)
            return None
        except Exception as e:
            logger.debug(f"_load_table {p}: {e}")
            continue

    logger.debug(f"_load_table: no file found for {project_slug}/{table_name}")
    return None


def _parse_simple_sql(sql: str) -> dict:
    """Parse simple SELECT/WHERE/GROUP BY/ORDER BY/LIMIT.

    Returns dict: {select, from, where, group_by, order_by, limit, error}

    Best-effort. Uses sqlglot if available, else regex.
    """
    try:
        import sqlglot
        ast = sqlglot.parse_one(sql, read="postgres")
        if ast is None:
            return {"error": "empty AST"}

        # SELECT
        selects = ast.args.get("expressions") or []
        select_cols = []
        for s in selects:
            try:
                select_cols.append(s.alias_or_name if hasattr(s, "alias_or_name") and s.alias_or_name else s.sql())
            except Exception:
                select_cols.append(s.sql())

        # FROM table
        from_clause = ast.args.get("from")
        from_table = ""
        if from_clause:
            exprs = getattr(from_clause, "expressions", None)
            if exprs:
                t = exprs[0]
            else:
                t = getattr(from_clause, "this", None)
            if t is not None:
                from_table = getattr(t, "name", None) or t.sql()

        # WHERE
        where_clause = ast.args.get("where")
        where_str = ""
        if where_clause is not None:
            try:
                where_str = where_clause.this.sql()
            except Exception:
                where_str = where_clause.sql().replace("WHERE", "", 1).strip()

        # GROUP BY
        group_clause = ast.args.get("group")
        group_cols = []
        if group_clause:
            for g in group_clause.expressions:
                group_cols.append(getattr(g, "name", None) or g.sql())

        # ORDER BY
        order_clause = ast.args.get("order")
        order_cols = []
        if order_clause:
            for o in order_clause.expressions:
                order_cols.append(o.sql())

        # LIMIT
        limit_clause = ast.args.get("limit")
        limit_n = None
        if limit_clause is not None:
            try:
                expr = getattr(limit_clause, "expression", None) or getattr(limit_clause, "this", None)
                if expr is not None:
                    val = getattr(expr, "this", expr)
                    limit_n = int(str(val))
            except Exception:
                pass

        return {
            "select": select_cols if select_cols else ["*"],
            "from": from_table,
            "where": where_str,
            "group_by": group_cols,
            "order_by": order_cols,
            "limit": limit_n,
            "error": None,
        }
    except ImportError:
        # Regex fallback
        return _parse_simple_sql_regex(sql)
    except Exception as e:
        # If sqlglot fails, fall back to regex
        out = _parse_simple_sql_regex(sql)
        if out.get("error"):
            return {"error": str(e)[:200]}
        return out


def _parse_simple_sql_regex(sql: str) -> dict:
    """Pure regex fallback when sqlglot unavailable."""
    s = sql.strip().rstrip(";")

    out = {"select": ["*"], "from": "", "where": "", "group_by": [],
           "order_by": [], "limit": None, "error": None}

    m = re.search(r"SELECT\s+(.+?)\s+FROM\s+(\w+)", s, re.IGNORECASE | re.DOTALL)
    if not m:
        out["error"] = "no SELECT/FROM"
        return out

    out["select"] = [c.strip() for c in m.group(1).split(",")]
    out["from"] = m.group(2)

    m2 = re.search(r"WHERE\s+(.+?)(?=\s+(?:GROUP\s+BY|ORDER\s+BY|LIMIT)\b|$)",
                    s, re.IGNORECASE | re.DOTALL)
    if m2:
        out["where"] = m2.group(1).strip()

    m3 = re.search(r"GROUP\s+BY\s+(.+?)(?=\s+(?:ORDER\s+BY|LIMIT)\b|$)",
                    s, re.IGNORECASE | re.DOTALL)
    if m3:
        out["group_by"] = [c.strip() for c in m3.group(1).split(",")]

    m4 = re.search(r"ORDER\s+BY\s+(.+?)(?=\s+LIMIT\b|$)",
                    s, re.IGNORECASE | re.DOTALL)
    if m4:
        out["order_by"] = [c.strip() for c in m4.group(1).split(",")]

    m5 = re.search(r"LIMIT\s+(\d+)", s, re.IGNORECASE)
    if m5:
        out["limit"] = int(m5.group(1))

    return out


def _sql_where_to_pandas_query(where_sql: str) -> str:
    """Convert SQL WHERE → pandas .query() syntax (basic)."""
    s = where_sql
    s = re.sub(r"\bAND\b", "&", s, flags=re.IGNORECASE)
    s = re.sub(r"\bOR\b", "|", s, flags=re.IGNORECASE)
    s = re.sub(r"\bIS\s+NOT\s+NULL\b", ".notna()", s, flags=re.IGNORECASE)
    s = re.sub(r"\bIS\s+NULL\b", ".isna()", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<![=<>!])=(?!=)", "==", s)
    return s


def list_loadable_tables(project_slug: str) -> list[dict]:
    """For diagnostics: list all loadable file tables in this project."""
    out = []
    base = KNOWLEDGE_DIR / project_slug
    if not base.exists():
        return out

    for ext in ("parquet", "csv", "json"):
        for p in base.rglob(f"*.{ext}"):
            try:
                size = p.stat().st_size
                rel = p.relative_to(base)
                out.append({
                    "path": str(rel),
                    "table_name": p.stem,
                    "format": ext,
                    "size_bytes": size,
                })
            except Exception:
                continue
    return out
