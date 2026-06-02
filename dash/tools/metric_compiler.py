"""
Metric Compiler
===============
Shared SQL compiler + DB CRUD for the generic, DB-backed metric engine.

Design contract
---------------
* All DB writes to public.dash_* go through get_write_engine() (read-write, no
  public guard).  Project-schema reads go through get_project_readonly_engine().
* NEVER use :x::jsonb — always CAST(:x AS jsonb)  (PgBouncer + SQLAlchemy
  named-param collision safety).
* NEVER dispose the cached shared engines.
* TTL cache (60 s) on load_definition; bust via cache_bust(project_slug).
"""

from __future__ import annotations

import decimal
import fnmatch
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MONTH_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[_-]?(\d{4})", re.I)
_MONTHNUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Column name allow-list: only [A-Za-z0-9_] permitted in identifiers.
_SAFE_COL_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Ops that need a text comparison (TRIM applicable)
_TEXT_OPS = {"=", "!=", "<>", "LIKE", "ILIKE", "IN", "NOT IN"}

# ── Cache ────────────────────────────────────────────────────────────────────

_def_cache: dict[str, tuple[float, Any]] = {}  # (slug, lower_name) -> (ts, row)
_DEF_TTL = 60.0  # seconds


def cache_bust(project_slug: str) -> None:
    """Invalidate all cached definitions for a project."""
    prefix = project_slug.lower()
    stale = [k for k in _def_cache if k[0] == prefix]
    for k in stale:
        _def_cache.pop(k, None)


# ── Schema helpers ────────────────────────────────────────────────────────────

def _slug_to_schema(slug: str) -> str:
    """Convert project_slug to Postgres schema name (mirrors db/session.py)."""
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def resolve_engine(project_slug: str):
    """Return (engine, schema) for a project's read-only engine."""
    from db import get_project_readonly_engine  # type: ignore[import]
    engine = get_project_readonly_engine(project_slug)
    schema = _slug_to_schema(project_slug)
    return engine, schema


# ── Table discovery ───────────────────────────────────────────────────────────

def month_tables(
    engine,
    schema: str,
    source_glob: str | None = None,
    source_tables: list | None = None,
) -> list[tuple[str, str]]:
    """Return [(table_name, 'YYYY-MM'), …] sorted by label.

    Priority:
    1. source_tables given → use them directly (detect month from name).
    2. source_glob given   → filter schema tables by fnmatch pattern.
    3. Neither             → all tables in schema.

    If no month token in name, label is ''.
    """
    if source_tables:
        names = list(source_tables)
    else:
        try:
            insp = inspect(engine)
            names = insp.get_table_names(schema=schema)
        except Exception as exc:
            logger.warning("month_tables inspect failed: %s", exc)
            return []
        if source_glob:
            names = [t for t in names if fnmatch.fnmatch(t.lower(), source_glob.lower())]

    out: list[tuple[str, str]] = []
    for t in names:
        m = MONTH_RE.search(t)
        if m:
            mon = _MONTHNUM.get(m.group(1).lower(), 0)
            label = f"{m.group(2)}-{mon:02d}" if mon else ""
        else:
            label = ""
        out.append((t, label))
    out.sort(key=lambda x: x[1])
    return out


# ── WHERE clause compiler ─────────────────────────────────────────────────────

def _quote_col(col: str) -> str:
    """Quote column name safely; reject unsafe identifiers."""
    if not _SAFE_COL_RE.match(col):
        raise ValueError(f"Unsafe column name rejected: {col!r}")
    return col  # plain identifier (no quotes needed for safe names)


def build_where(
    filters: list[dict],
    trim: bool,
    param_prefix: str = "f",
) -> tuple[str, dict]:
    """Compile a list of filter dicts to a SQL predicate string + bound params.

    Filter dict schema:
        col   : str   — column name (must match [A-Za-z0-9_])
        op    : str   — operator (see below)
        value : any   — scalar or list (for IN / BETWEEN)
        trim  : bool  — per-filter trim override (optional; falls back to global `trim`)

    Supported ops:
        =  !=  <>  >  >=  <  <=  LIKE  ILIKE
        IN  NOT IN  BETWEEN  IS NULL  IS NOT NULL

    Returns (predicate_sql, params_dict).
    Returns ('TRUE', {}) when filters is empty.
    """
    if not filters:
        return "TRUE", {}

    parts: list[str] = []
    params: dict = {}
    idx = 0

    for flt in filters:
        col = _quote_col(flt["col"])
        op = str(flt.get("op", "=")).strip().upper()
        value = flt.get("value")
        per_filter_trim = flt.get("trim", trim)

        # Column expression (TRIM when applicable)
        if per_filter_trim and op in _TEXT_OPS:
            col_expr = f"TRIM({col})"
        else:
            col_expr = col

        if op in ("IS NULL", "IS NOT NULL"):
            parts.append(f"{col_expr} {op}")

        elif op in ("IN", "NOT IN"):
            vals = list(value) if isinstance(value, (list, tuple)) else [value]
            if not vals:
                parts.append("FALSE")
                continue
            pnames = []
            for v in vals:
                pname = f"{param_prefix}{idx}"
                params[pname] = v
                pnames.append(f":{pname}")
                idx += 1
            parts.append(f"{col_expr} {op} ({', '.join(pnames)})")

        elif op == "BETWEEN":
            vals = list(value) if isinstance(value, (list, tuple)) else [value, value]
            lo_name = f"{param_prefix}{idx}"
            hi_name = f"{param_prefix}{idx + 1}"
            params[lo_name] = vals[0]
            params[hi_name] = vals[1] if len(vals) > 1 else vals[0]
            idx += 2
            parts.append(f"{col_expr} BETWEEN :{lo_name} AND :{hi_name}")

        else:
            # scalar ops: = != <> > >= < <= LIKE ILIKE
            pname = f"{param_prefix}{idx}"
            params[pname] = value
            idx += 1
            parts.append(f"{col_expr} {op} :{pname}")

    return " AND ".join(parts) if parts else "TRUE", params


# ── SQL compiler ──────────────────────────────────────────────────────────────

def _union_cte(schema: str, tables: list[tuple[str, str]], cols: list[str]) -> str:
    """Build UNION ALL body for a CTE, synthesizing a `month` literal column."""
    col_list = ", ".join(cols)
    parts = []
    for tname, label in tables:
        parts.append(
            f"  SELECT '{label}' AS month, {col_list} FROM \"{schema}\".\"{tname}\""
        )
    return "\nUNION ALL\n".join(parts)


def compile_metric_sql(
    spec: dict,
    schema: str,
    tables: list[tuple[str, str]],
    group_by: list[str] | None = None,
    extra_filters: list[dict] | None = None,
) -> tuple[str, dict]:
    """Compile a metric spec into deterministic SQL + bound params.

    Returns (sql, params).

    Spec keys used:
        kind           : count | sum | avg | rate | ratio | contribution
        filters        : list[{col,op,value,trim}]   numerator/main filter
        denom_filters  : list[{col,op,value,trim}]   denominator filter (rate/ratio)
        measure_col    : str   column for sum/avg
        group_dims     : list[str]  available group dimensions
        default_group  : list[str]  default if group_by not supplied
        trim_values    : bool

    group_by=None → use spec['default_group'].
    extra_filters are AND-merged into the numerator filters.
    Special dim 'month' resolves to the synthesized literal column.
    """
    if not tables:
        raise ValueError("No tables available for metric SQL compilation")

    kind: str = spec.get("kind", "count").lower()
    trim: bool = bool(spec.get("trim_values", True))
    filters: list[dict] = list(spec.get("filters", []))
    denom_filters: list[dict] = list(spec.get("denom_filters", []))
    measure_col: str = spec.get("measure_col") or ""
    group_dims: list[str] = list(spec.get("group_dims", []))
    default_group: list[str] = list(spec.get("default_group", []))

    # Resolve requested dims
    dims = group_by if group_by is not None else default_group
    # Keep only dims that exist in group_dims or is 'month'
    valid_dims = set(group_dims) | {"month"}
    dims = [d for d in dims if d in valid_dims]

    # Merge extra_filters into numerator
    if extra_filters:
        filters = filters + list(extra_filters)

    # Build WHERE for numerator
    where_sql, where_params = build_where(filters, trim, param_prefix="f")
    # Build WHERE for denominator
    denom_sql, denom_params = build_where(denom_filters, trim, param_prefix="d")

    # Determine which columns the UNION must carry. SELECT * across sibling
    # monthly tables breaks when their column TYPES differ by position
    # ("UNION types double precision and text cannot be matched"). So enumerate
    # ONLY the columns this metric references, by name — guarantees consistent
    # type/order across every table and matches the crm_metrics approach.
    _ref: list[str] = []
    for _f in (filters + denom_filters):
        c = (_f or {}).get("col")
        if c:
            _ref.append(c)
    for _d in group_dims:
        if _d and _d != "month":
            _ref.append(_d)
    if measure_col:
        _ref.append(measure_col)
    # dedupe preserving order, quote each identifier
    _seen: set[str] = set()
    _cols = []
    for c in _ref:
        if c not in _seen:
            _seen.add(c)
            _cols.append(_quote_col(c))
    if not _cols:
        _cols = ["1 AS _dummy"]  # contribution-by-nothing edge case
    cte_body = _union_cte(schema, tables, _cols)
    cte = f"WITH base AS (\n{cte_body}\n)"

    # Dimension SELECT expressions
    def dim_expr(d: str) -> str:
        if d == "month":
            return "month"
        # Other dims: TRIM(col) AS dim
        col = _quote_col(d)
        return f"TRIM({col})" if trim else col

    def dim_alias(d: str) -> str:
        return d  # alias = dim name

    if kind in ("count",):
        gsel_parts = [f"{dim_expr(d)} AS {dim_alias(d)}" for d in dims]
        gby_parts = [dim_expr(d) for d in dims]
        sel = (", ".join(gsel_parts) + ", " if gsel_parts else "") + "COUNT(*) AS value"
        grp = (
            "GROUP BY " + ", ".join(gby_parts) + " ORDER BY " + ", ".join(gby_parts)
            if gby_parts
            else ""
        )
        sql = f"{cte}\nSELECT {sel} FROM base WHERE {where_sql}\n{grp}".strip()
        return sql, where_params

    elif kind in ("sum", "avg"):
        if not measure_col:
            raise ValueError(f"kind={kind!r} requires measure_col")
        col = _quote_col(measure_col)
        agg = "SUM" if kind == "sum" else "AVG"
        gsel_parts = [f"{dim_expr(d)} AS {dim_alias(d)}" for d in dims]
        gby_parts = [dim_expr(d) for d in dims]
        sel = (", ".join(gsel_parts) + ", " if gsel_parts else "") + f"{agg}({col}) AS value"
        grp = (
            "GROUP BY " + ", ".join(gby_parts) + " ORDER BY " + ", ".join(gby_parts)
            if gby_parts
            else ""
        )
        sql = f"{cte}\nSELECT {sel} FROM base WHERE {where_sql}\n{grp}".strip()
        return sql, where_params

    elif kind in ("rate", "ratio"):
        gsel_parts = [f"{dim_expr(d)} AS {dim_alias(d)}" for d in dims]
        gby_parts = [dim_expr(d) for d in dims]
        sel_prefix = ", ".join(gsel_parts) + ", " if gsel_parts else ""
        grp = (
            "GROUP BY " + ", ".join(gby_parts) + " ORDER BY " + ", ".join(gby_parts)
            if gby_parts
            else ""
        )
        sql = (
            f"{cte}\n"
            f"SELECT {sel_prefix}"
            f"SUM(CASE WHEN {where_sql} THEN 1 ELSE 0 END) AS numerator, "
            f"SUM(CASE WHEN {denom_sql} THEN 1 ELSE 0 END) AS denominator, "
            f"ROUND(100.0 * SUM(CASE WHEN {where_sql} THEN 1 ELSE 0 END) "
            f"/ NULLIF(SUM(CASE WHEN {denom_sql} THEN 1 ELSE 0 END), 0), 1) AS rate_pct "
            f"FROM base WHERE TRUE\n{grp}"
        ).strip()
        merged_params = {**where_params, **denom_params}
        return sql, merged_params

    elif kind == "contribution":
        # Split by a dim if given; otherwise split by the filtered outcome
        # Pattern: count per outcome with % of window total
        gsel_parts = [f"{dim_expr(d)} AS {dim_alias(d)}" for d in dims]
        gby_parts = [dim_expr(d) for d in dims]
        if gby_parts:
            grp_clause = "GROUP BY " + ", ".join(gby_parts)
            ord_clause = "ORDER BY value DESC"
            sel_prefix = ", ".join(gsel_parts) + ", "
        else:
            grp_clause = ""
            ord_clause = "ORDER BY value DESC"
            sel_prefix = ""
        sql = (
            f"{cte}\n"
            f"SELECT {sel_prefix}COUNT(*) AS value, "
            f"ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct "
            f"FROM base WHERE {where_sql} "
            f"{grp_clause} {ord_clause}"
        ).strip()
        return sql, where_params

    else:
        raise ValueError(f"Unknown metric kind: {kind!r}")


# ── Query runner ──────────────────────────────────────────────────────────────

def _md_table(headers: list[str], rows: list) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return "\n".join([head, sep, body])


def _coerce(o: Any) -> Any:
    """JSON serialisation default — Decimal → float."""
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def run_metric(
    project_slug: str,
    spec: dict,
    group_by: list[str] | None = None,
    extra_filters: list[dict] | None = None,
) -> dict:
    """Resolve engine + tables, compile SQL, execute read-only, return result dict.

    Returns:
        {ok, metric, kind, definition, total, columns, rows, table_md, sql}
    On error:
        {ok: False, error, detail}
    """
    try:
        engine, schema = resolve_engine(project_slug)
        tables = month_tables(
            engine,
            schema,
            source_glob=spec.get("source_glob"),
            source_tables=spec.get("source_tables") or None,
        )
        if not tables:
            return {"ok": False, "error": "NO_TABLES", "schema": schema}

        sql, params = compile_metric_sql(spec, schema, tables, group_by, extra_filters)

        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '20s'"))
            result = conn.execute(text(sql), params)
            col_names = list(result.keys())
            raw_rows = result.fetchall()

        # Coerce decimals
        rows = []
        for r in raw_rows:
            rows.append([float(v) if isinstance(v, decimal.Decimal) else v for v in r])

        # Compute total (last column assumed to be the primary measure)
        total: float | int | None = None
        if rows and col_names:
            try:
                last_col_vals = [r[-1] for r in rows if r[-1] is not None]
                total = sum(float(v) for v in last_col_vals)
            except (TypeError, ValueError):
                total = None

        return {
            "ok": True,
            "metric": spec.get("name", ""),
            "kind": spec.get("kind", "count"),
            "definition": {
                "filters": spec.get("filters", []),
                "denom_filters": spec.get("denom_filters", []),
                "group_dims": spec.get("group_dims", []),
            },
            "total": total,
            "columns": col_names,
            "rows": rows,
            "table_md": _md_table(col_names, rows),
            "sql": sql,
        }
    except Exception as exc:
        logger.warning("run_metric failed: %s", exc)
        return {"ok": False, "error": "QUERY_FAILED", "detail": str(exc)[:400]}


# ── DB CRUD ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy Row to a plain dict."""
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def load_definition(project_slug: str, name: str) -> dict | None:
    """Load a metric definition by exact name (case-insensitive) or synonym.

    Result is TTL-cached for 60 s keyed (slug, lower(name)).
    """
    cache_key = (project_slug.lower(), name.lower())
    now = time.monotonic()
    cached = _def_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < _DEF_TTL:
        return cached[1]

    try:
        from db.session import get_write_engine  # type: ignore[import]
        engine = get_write_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM public.dash_metric_definitions "
                    "WHERE project_slug = :slug "
                    "  AND (LOWER(name) = LOWER(:name) "
                    "       OR synonyms @> CAST(:jname AS jsonb)) "
                    "LIMIT 1"
                ),
                {
                    "slug": project_slug,
                    "name": name,
                    "jname": json.dumps([name.lower()]),
                },
            ).fetchone()
        result = _row_to_dict(row) if row else None
        _def_cache[cache_key] = (now, result)
        return result
    except Exception as exc:
        logger.warning("load_definition failed: %s", exc)
        return None


def list_definitions(project_slug: str, status: str | None = None) -> list[dict]:
    """List all metric definitions for a project, optionally filtered by status."""
    try:
        from db.session import get_write_engine  # type: ignore[import]
        engine = get_write_engine()
        with engine.connect() as conn:
            if status:
                rows = conn.execute(
                    text(
                        "SELECT * FROM public.dash_metric_definitions "
                        "WHERE project_slug = :slug AND status = :status "
                        "ORDER BY name"
                    ),
                    {"slug": project_slug, "status": status},
                ).fetchall()
            else:
                rows = conn.execute(
                    text(
                        "SELECT * FROM public.dash_metric_definitions "
                        "WHERE project_slug = :slug ORDER BY name"
                    ),
                    {"slug": project_slug},
                ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_definitions failed: %s", exc)
        return []


def save_definition(project_slug: str, spec: dict, user: str | None = None) -> dict:
    """Upsert a metric definition. On conflict (project_slug, name) bumps version.

    Writes a dash_metric_versions snapshot row in the same transaction.
    Busts the definition cache for this project after save.

    Returns the saved row as a dict.
    """
    from db.session import get_write_engine  # type: ignore[import]
    engine = get_write_engine()

    name = spec["name"]
    now_ts = "now()"  # server-side

    upsert_sql = text(
        """
        INSERT INTO public.dash_metric_definitions
            (project_slug, name, synonyms, description, kind,
             source_glob, source_tables, measure_col,
             filters, denom_filters, group_dims, default_group,
             trim_values, verified_answer, status, version,
             created_by, updated_by, created_at, updated_at)
        VALUES
            (:slug, :name,
             CAST(:synonyms AS jsonb), :description, :kind,
             :source_glob,
             CAST(:source_tables AS jsonb),
             :measure_col,
             CAST(:filters AS jsonb),
             CAST(:denom_filters AS jsonb),
             CAST(:group_dims AS jsonb),
             CAST(:default_group AS jsonb),
             :trim_values,
             CAST(:verified_answer AS jsonb),
             :status, 1,
             :user, :user, now(), now())
        ON CONFLICT (project_slug, name) DO UPDATE
            SET synonyms        = CAST(:synonyms AS jsonb),
                description     = :description,
                kind            = :kind,
                source_glob     = :source_glob,
                source_tables   = CAST(:source_tables AS jsonb),
                measure_col     = :measure_col,
                filters         = CAST(:filters AS jsonb),
                denom_filters   = CAST(:denom_filters AS jsonb),
                group_dims      = CAST(:group_dims AS jsonb),
                default_group   = CAST(:default_group AS jsonb),
                trim_values     = :trim_values,
                verified_answer = CAST(:verified_answer AS jsonb),
                status          = :status,
                version         = public.dash_metric_definitions.version + 1,
                updated_by      = :user,
                updated_at      = now()
        RETURNING *
        """
    )

    params = {
        "slug": project_slug,
        "name": name,
        "synonyms": json.dumps(spec.get("synonyms") or []),
        "description": spec.get("description"),
        "kind": spec.get("kind", "count"),
        "source_glob": spec.get("source_glob"),
        "source_tables": json.dumps(spec.get("source_tables") or []),
        "measure_col": spec.get("measure_col"),
        "filters": json.dumps(spec.get("filters") or []),
        "denom_filters": json.dumps(spec.get("denom_filters") or []),
        "group_dims": json.dumps(spec.get("group_dims") or []),
        "default_group": json.dumps(spec.get("default_group") or []),
        "trim_values": bool(spec.get("trim_values", True)),
        "verified_answer": json.dumps(spec.get("verified_answer")) if spec.get("verified_answer") else None,
        "status": spec.get("status", "draft"),
        "user": user,
    }

    with engine.begin() as conn:
        row = conn.execute(upsert_sql, params).fetchone()
        saved = _row_to_dict(row)

        # Write version snapshot
        conn.execute(
            text(
                """
                INSERT INTO public.dash_metric_versions
                    (metric_id, project_slug, name, snapshot,
                     change_type, changed_by, change_reason, created_at)
                VALUES
                    (:metric_id, :slug, :name,
                     CAST(:snapshot AS jsonb),
                     :change_type, :changed_by, :change_reason, now())
                """
            ),
            {
                "metric_id": saved["id"],
                "slug": project_slug,
                "name": name,
                "snapshot": json.dumps(saved, default=str),
                "change_type": "upsert",
                "changed_by": user,
                "change_reason": None,
            },
        )

    cache_bust(project_slug)
    return saved


def set_status(
    project_slug: str, name: str, status: str, user: str | None = None
) -> dict:
    """Update the status of a metric definition (soft delete = 'deprecated')."""
    from db.session import get_write_engine  # type: ignore[import]
    engine = get_write_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "UPDATE public.dash_metric_definitions "
                "SET status = :status, updated_by = :user, updated_at = now() "
                "WHERE project_slug = :slug AND LOWER(name) = LOWER(:name) "
                "RETURNING *"
            ),
            {"status": status, "user": user, "slug": project_slug, "name": name},
        ).fetchone()
    cache_bust(project_slug)
    return _row_to_dict(row) if row else {}


def delete_definition(project_slug: str, name: str) -> dict:
    """Soft-delete: set status to 'deprecated'."""
    return set_status(project_slug, name, "deprecated")


def list_versions(project_slug: str, name: str) -> list[dict]:
    """List version history for a metric definition."""
    try:
        from db.session import get_write_engine  # type: ignore[import]
        engine = get_write_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT v.* FROM public.dash_metric_versions v "
                    "JOIN public.dash_metric_definitions d ON d.id = v.metric_id "
                    "WHERE v.project_slug = :slug AND LOWER(v.name) = LOWER(:name) "
                    "ORDER BY v.created_at DESC"
                ),
                {"slug": project_slug, "name": name},
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_versions failed: %s", exc)
        return []


def rollback(
    project_slug: str, name: str, version: int, user: str | None = None
) -> dict:
    """Restore a metric definition from a specific historical snapshot."""
    try:
        from db.session import get_write_engine  # type: ignore[import]
        engine = get_write_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT v.snapshot FROM public.dash_metric_versions v "
                    "JOIN public.dash_metric_definitions d ON d.id = v.metric_id "
                    "WHERE v.project_slug = :slug AND LOWER(v.name) = LOWER(:name) "
                    "  AND (v.snapshot->>'version')::int = :ver "
                    "ORDER BY v.created_at LIMIT 1"
                ),
                {"slug": project_slug, "name": name, "ver": version},
            ).fetchone()
        if not row:
            return {"ok": False, "error": "VERSION_NOT_FOUND"}
        snap = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        snap["status"] = snap.get("status", "draft")
        return save_definition(project_slug, snap, user=user)
    except Exception as exc:
        logger.warning("rollback failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


# ── Column catalog ────────────────────────────────────────────────────────────

def column_catalog(
    project_slug: str,
    table: str | None = None,
    max_distinct: int = 60,
) -> list[dict]:
    """Introspect the project schema; return per-column metadata.

    Per column:
        table, column, dtype, distinct (int|None),
        samples (list of up to max_distinct DISTINCT TRIM values)

    Only text/categorical columns with COUNT(DISTINCT) < max_distinct get sampled.
    """
    try:
        engine, schema = resolve_engine(project_slug)
        insp = inspect(engine)
        tables = insp.get_table_names(schema=schema)
        if table:
            tables = [t for t in tables if t == table]

        out: list[dict] = []
        with engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '20s'"))
            for tname in tables:
                try:
                    cols = insp.get_columns(tname, schema=schema)
                except Exception:
                    continue
                for col in cols:
                    col_name = col["name"]
                    dtype_str = str(col["type"])
                    is_text = any(
                        kw in dtype_str.upper()
                        for kw in ("TEXT", "VARCHAR", "CHAR", "ENUM")
                    )
                    distinct_val: int | None = None
                    samples: list = []

                    if is_text:
                        try:
                            cnt_row = conn.execute(
                                text(
                                    f'SELECT COUNT(DISTINCT TRIM("{col_name}")) '
                                    f'FROM "{schema}"."{tname}"'
                                )
                            ).fetchone()
                            distinct_val = int(cnt_row[0]) if cnt_row else None
                        except Exception:
                            distinct_val = None

                        if distinct_val is not None and distinct_val < max_distinct:
                            try:
                                samp_rows = conn.execute(
                                    text(
                                        f'SELECT DISTINCT TRIM("{col_name}") '
                                        f'FROM "{schema}"."{tname}" '
                                        f'WHERE "{col_name}" IS NOT NULL '
                                        f'ORDER BY 1 LIMIT {max_distinct}'
                                    )
                                ).fetchall()
                                samples = [r[0] for r in samp_rows]
                            except Exception:
                                samples = []

                    out.append({
                        "table": tname,
                        "column": col_name,
                        "dtype": dtype_str,
                        "distinct": distinct_val,
                        "samples": samples,
                    })
        return out
    except Exception as exc:
        logger.warning("column_catalog failed: %s", exc)
        return []
