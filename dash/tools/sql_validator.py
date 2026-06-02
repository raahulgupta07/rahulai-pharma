"""
Central SQL validator + auto-fixer for LLM-generated SQL.

All code that takes LLM-generated SQL MUST call `validate_and_fix()` before
persisting or executing. Closes the hallucination-class bug (LLM emits SQL
referencing nonexistent tables/columns or missing Postgres dialect casts).

Public API:
    validate_and_fix(sql, project_slug, *, schema=None, strict=True) -> dict
    get_schema_hint(project_slug, schema=None) -> str  # for prompt injection
    invalidate_cache(project_slug)                     # after schema changes
"""
from __future__ import annotations
import re
import time
import logging
from typing import Optional

logger = logging.getLogger("sql_validator")

# --- Schema cache (5-min TTL) ---
_TTL_S = 300.0
_cache: dict[str, tuple[float, dict[str, list[tuple[str, str]]]]] = {}
_cache_stats = {"hits": 0, "misses": 0}


def invalidate_cache(project_slug: str) -> None:
    """Drop cached schema for a project (call after ingest/ALTER)."""
    _cache.pop(_norm_slug(project_slug), None)


def get_cache_stats() -> dict:
    """Return current schema-cache hit/miss counters + derived hit_rate + size."""
    hits = int(_cache_stats.get("hits", 0))
    misses = int(_cache_stats.get("misses", 0))
    total = hits + misses
    hit_rate = (hits / total) if total else 0.0
    return {
        "cache_size": len(_cache),
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hit_rate, 4),
    }


def _emit_event(
    kind: str,
    *,
    project_slug: Optional[str],
    source: str,
    table_name: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Fail-soft event emit. Writes to dash.dash_sql_validator_events.

    Telemetry only — never raises. Used by validator + Q&A gen + chat path
    to surface auto-fix / qa-drop / chat-autofix / reject counters into the
    SQL validator stats endpoint.
    """
    try:
        from db.session import get_write_engine  # public+dash writes
        from sqlalchemy import text as _sa_text
        import json as _json
        eng = get_write_engine()
        with eng.begin() as c:
            c.execute(_sa_text(
                "INSERT INTO dash.dash_sql_validator_events"
                "(project_slug, kind, source, table_name, details) "
                "VALUES (:p, :k, :s, :t, CAST(:d AS jsonb))"
            ), {
                "p": project_slug,
                "k": kind,
                "s": source,
                "t": table_name,
                "d": _json.dumps(details or {}),
            })
    except Exception:
        pass


def _norm_slug(slug: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", (slug or "").lower())[:63]


def _load_schema(project_slug: str, schema: Optional[str] = None) -> dict[str, list[tuple[str, str]]]:
    """Returns {table_name: [(col_name, dtype), ...]} for project's schema.
    Cached 5 min. Fail-soft → empty dict on DB error."""
    schema = schema or _norm_slug(project_slug)
    now = time.time()
    cached = _cache.get(schema)
    if cached and cached[0] > now:
        _cache_stats["hits"] = _cache_stats.get("hits", 0) + 1
        return cached[1]
    _cache_stats["misses"] = _cache_stats.get("misses", 0) + 1
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text
        eng = get_sql_engine()
        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :s ORDER BY table_name, ordinal_position"
            ), {"s": schema}).fetchall()
        for tbl, col, dtype in rows:
            out.setdefault(tbl, []).append((col, dtype))
    except Exception as e:
        logger.debug(f"_load_schema({schema}) failed: {e}")
    _cache[schema] = (now + _TTL_S, out)
    return out


def get_schema_hint(project_slug: str, schema: Optional[str] = None, max_tables: int = 20, max_cols_per_table: int = 40) -> str:
    """Compact schema string for LLM prompt injection.
    Format: 'table_name (col1: dtype, col2: dtype, ...)'"""
    schema_map = _load_schema(project_slug, schema)
    if not schema_map:
        return "(no tables found in project schema)"
    lines: list[str] = []
    for tbl in sorted(schema_map.keys())[:max_tables]:
        cols = schema_map[tbl][:max_cols_per_table]
        col_str = ", ".join(f"{c}:{_short_dtype(d)}" for c, d in cols)
        lines.append(f"  {tbl} ({col_str})")
    return "TABLES:\n" + "\n".join(lines)


def _short_dtype(dtype: str) -> str:
    """Compact dtype label for prompt."""
    d = (dtype or "").lower()
    if "char" in d or "text" in d:
        return "TEXT"
    if "int" in d:
        return "INT"
    if "numeric" in d or "decimal" in d or "real" in d or "double" in d or "float" in d:
        return "NUM"
    if "timestamp" in d or "date" in d or "time" in d:
        return "DATE"
    if "bool" in d:
        return "BOOL"
    if "json" in d:
        return "JSON"
    return d.upper()[:8]


def _levenshtein(a: str, b: str) -> int:
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(name: str, candidates: list[str], cutoff_ratio: float = 0.4) -> Optional[str]:
    """Closest candidate by edit distance. Returns None if none close enough."""
    if not candidates or not name:
        return None
    name_l = name.lower()
    best = min(candidates, key=lambda c: _levenshtein(name_l, c.lower()))
    dist = _levenshtein(name_l, best.lower())
    if dist <= max(1, int(len(name) * cutoff_ratio)):
        return best
    return None


# --- Auto-fixes ---

def _autofix_date_trunc_text(sql: str, schema_map: dict[str, list[tuple[str, str]]]) -> tuple[str, list[str]]:
    """date_trunc('month', text_col) → date_trunc('month', text_col::date)
    Only fixes when col exists + is TEXT in any table."""
    fixes: list[str] = []
    text_cols = {c.lower() for cols in schema_map.values() for c, d in cols if "char" in (d or "").lower() or "text" in (d or "").lower()}
    if not text_cols:
        return sql, fixes
    # Match date_trunc('unit', <expr>) where expr is a bare identifier
    pat = re.compile(r"\b(date_trunc|date_part|extract)\s*\(\s*('[^']+'|[a-z_]+)\s*,\s*([a-z_][a-z_0-9.]*)\s*\)", re.IGNORECASE)
    def repl(m: re.Match) -> str:
        fn, arg1, col_ref = m.group(1), m.group(2), m.group(3)
        # Bare col or schema.col — strip qualifier for lookup
        bare = col_ref.split(".")[-1].lower()
        if bare in text_cols and "::date" not in col_ref and "::timestamp" not in col_ref:
            fixes.append(f"cast TEXT column `{col_ref}` to ::date inside {fn}()")
            return f"{fn}({arg1}, {col_ref}::date)"
        return m.group(0)
    new_sql = pat.sub(repl, sql)
    return new_sql, fixes


def _autofix_text_comparison(sql: str, schema_map: dict[str, list[tuple[str, str]]]) -> tuple[str, list[str]]:
    """text_col >= timestamp_literal → text_col::date >= timestamp_literal"""
    fixes: list[str] = []
    text_cols = {c.lower() for cols in schema_map.values() for c, d in cols if "char" in (d or "").lower() or "text" in (d or "").lower()}
    if not text_cols:
        return sql, fixes
    # col >= 'YYYY-MM-DD' or CURRENT_DATE or NOW()
    pat = re.compile(
        r"\b([a-z_][a-z_0-9]*)\s*([<>=!]=?|<|>)\s*(CURRENT_DATE|CURRENT_TIMESTAMP|NOW\s*\(\s*\)|'[0-9]{4}-[0-9]{2}-[0-9]{2}[^']*')",
        re.IGNORECASE,
    )
    def repl(m: re.Match) -> str:
        col, op, rhs = m.group(1), m.group(2), m.group(3)
        if col.lower() in text_cols:
            fixes.append(f"cast TEXT column `{col}` to ::date for comparison with date literal")
            return f"{col}::date {op} {rhs}"
        return m.group(0)
    new_sql = pat.sub(repl, sql)
    return new_sql, fixes


# --- Main API ---

def validate_and_fix(
    sql: str,
    project_slug: str,
    *,
    schema: Optional[str] = None,
    strict: bool = True,
    dialect: str = "postgres",
) -> dict:
    """Validate + auto-fix LLM-generated SQL.

    Returns:
        {
            "ok": bool,           # safe to execute?
            "sql": str,           # original or fixed
            "fixes_applied": [str],
            "errors": [str],
            "warnings": [str],
            "tables_used": [str],
            "columns_used": [str],
            "unknown_tables": [str],
            "unknown_columns": [str],
            "schema_hint": str,   # for retry prompt
        }
    """
    result = {
        "ok": False, "sql": sql, "fixes_applied": [], "errors": [], "warnings": [],
        "tables_used": [], "columns_used": [], "unknown_tables": [], "unknown_columns": [],
        "schema_hint": "",
    }
    if not sql or not sql.strip():
        result["errors"].append("empty SQL")
        return result

    schema = schema or _norm_slug(project_slug)
    schema_map = _load_schema(project_slug, schema)
    result["schema_hint"] = get_schema_hint(project_slug, schema)

    # 1. Parse via sqlglot
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        result["errors"].append(f"parse failed: {str(e)[:200]}")
        return result

    # 2. Extract table + column refs
    real_tables = set(schema_map.keys())
    real_cols_by_table = {t: {c.lower() for c, _ in schema_map[t]} for t in schema_map}
    all_real_cols = {c.lower() for cols in schema_map.values() for c, _ in cols}

    tables_used: list[str] = []
    columns_used: list[str] = []
    unknown_tables: list[str] = []
    unknown_columns: list[str] = []

    # Walk Tables (skip CTEs)
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for tbl in tree.find_all(exp.Table):
        name = tbl.name.lower()
        if name in cte_names:
            continue
        tables_used.append(name)
        if real_tables and name not in real_tables:
            sugg = _suggest(name, list(real_tables))
            if sugg:
                unknown_tables.append(f"{name} (did you mean: {sugg}?)")
            else:
                unknown_tables.append(name)

    # Walk Columns
    for col in tree.find_all(exp.Column):
        cname = col.name.lower()
        if not cname or cname == "*":
            continue
        columns_used.append(cname)
        if all_real_cols and cname not in all_real_cols:
            # Skip aliases defined in CTEs / subqueries (heuristic)
            # Skip if it's a function arg quoted literal
            sugg = _suggest(cname, sorted(all_real_cols))
            if sugg:
                unknown_columns.append(f"{cname} (did you mean: {sugg}?)")
            else:
                unknown_columns.append(cname)

    result["tables_used"] = sorted(set(tables_used))
    result["columns_used"] = sorted(set(columns_used))
    result["unknown_tables"] = sorted(set(unknown_tables))
    result["unknown_columns"] = sorted(set(unknown_columns))

    # 3. Auto-fix Postgres dialect issues
    fixed_sql = sql
    fixed_sql, f1 = _autofix_date_trunc_text(fixed_sql, schema_map)
    fixed_sql, f2 = _autofix_text_comparison(fixed_sql, schema_map)
    fixes = f1 + f2
    if fixed_sql != sql:
        result["sql"] = fixed_sql
        result["fixes_applied"] = fixes

    # 4. Decision
    if unknown_tables and strict:
        result["errors"].append(f"unknown table(s): {', '.join(result['unknown_tables'])}")
    if unknown_columns and strict:
        # Soft-warn if many cols are unknown (probably an alias or fn we can't resolve)
        if len(unknown_columns) > 5:
            result["warnings"].append(f"{len(unknown_columns)} cols couldn't be resolved (may be aliases): {', '.join(result['unknown_columns'][:5])}…")
        else:
            result["errors"].append(f"unknown column(s): {', '.join(result['unknown_columns'])}")

    # 5. EXPLAIN-validate as final gate (catches anything regex/sqlglot missed)
    if not result["errors"]:
        try:
            from db.session import get_sql_engine
            from sqlalchemy import text
            eng = get_sql_engine()
            with eng.begin() as conn:
                conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
                conn.execute(text(f"EXPLAIN {fixed_sql}"))
        except Exception as e:
            err_str = str(e).split("\n")[0][:300]
            result["errors"].append(f"EXPLAIN failed: {err_str}")

    result["ok"] = not result["errors"]

    # Telemetry: emit auto_fix event when validation passed AND fixes were applied.
    # Fail-soft inside _emit_event.
    if result["ok"] and result.get("fixes_applied"):
        try:
            _tbls = result.get("tables_used") or []
            _emit_event(
                "auto_fix",
                project_slug=project_slug,
                source="validator",
                table_name=_tbls[0] if _tbls else None,
                details={"fixes": result["fixes_applied"]},
            )
        except Exception:
            pass

    return result
