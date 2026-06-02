"""Per-provider Agno tool factory.

Given a :class:`BaseProvider` instance, build a list of Agno-compatible
``@tool``-decorated callables suffixed with the provider id so multiple
sources can coexist on the same Analyst:

    query_<pid>, describe_<pid>, sample_<pid>, profile_<pid>

All tools are read-only: the SQL string is regex-screened for write
keywords, then executed against the provider's ``engine_ro``. If the
provider is degraded or has no read engine, every tool short-circuits
with a deterministic error string (it never raises past this layer).

The factory is framework-light — it depends on Agno's ``@tool`` decorator
and SQLAlchemy ``text``. Dialect-specific introspection SQL lives here
rather than in the provider so subclasses don't have to re-implement it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Iterable

from agno.tools import tool
from sqlalchemy import text

from .base import BaseProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ROWS = 1000
MAX_BYTES = 8 * 1024  # 8KB cap on text payload returned to the agent

# Keywords whose presence anywhere in a SQL statement disqualifies it from
# the read-only path. Word boundaries prevent accidental matches inside
# identifiers (e.g. column "updated_at" must not trip "update").
_FORBIDDEN = re.compile(
    r"\b(?:DROP|ALTER|TRUNCATE|MERGE|INSERT|UPDATE|DELETE|CREATE|GRANT|REVOKE|EXEC|EXECUTE|CALL|REPLACE|RENAME|COMMENT|VACUUM|ANALYZE|LOCK|COPY|ATTACH|DETACH)\b",
    re.IGNORECASE,
)
_ALLOWED_LEAD = re.compile(r"^\s*(?:WITH|SELECT)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_read_only(sql: str) -> bool:
    """Return True iff ``sql`` is a single SELECT/CTE statement.

    Strips a trailing semicolon (common from agents) but rejects compound
    statements containing internal semicolons.
    """
    if not sql or not isinstance(sql, str):
        return False
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False
    if ";" in stripped:
        return False  # compound / stacked queries
    if not _ALLOWED_LEAD.match(stripped):
        return False
    if _FORBIDDEN.search(stripped):
        return False
    return True


def _format_rows(rows: Iterable[Any]) -> str:
    """Render SQLAlchemy rows as compact CSV text, capped at MAX_BYTES."""
    rows = list(rows)
    if not rows:
        return "(0 rows)"

    first = rows[0]
    headers: list[str]
    if hasattr(first, "_fields"):
        headers = list(first._fields)
    elif hasattr(first, "keys"):
        try:
            headers = list(first.keys())  # type: ignore[arg-type]
        except Exception:
            headers = [f"col{i}" for i in range(len(first))]
    else:
        headers = [f"col{i}" for i in range(len(first))]

    def _cell(v: Any) -> str:
        if v is None:
            return ""
        s = str(v).replace("\n", " ").replace("\r", " ")
        if "," in s or '"' in s:
            s = '"' + s.replace('"', '""') + '"'
        return s

    out_lines = [",".join(headers)]
    total = len(out_lines[0]) + 1
    truncated = False
    for r in rows:
        line = ",".join(_cell(c) for c in r)
        if total + len(line) + 1 > MAX_BYTES:
            truncated = True
            break
        out_lines.append(line)
        total += len(line) + 1

    suffix = f"\n-- {len(rows)} rows" + (" (output truncated at 8KB)" if truncated else "")
    return "\n".join(out_lines) + suffix


def _dialect_top_clause(dialect: str, n: int) -> str:
    """Return a leading ``TOP N`` for T-SQL, empty string elsewhere."""
    return f"TOP {int(n)} " if dialect == "tsql" else ""


def _dialect_limit(dialect: str, sql: str, n: int) -> str:
    """Append the right limit clause for ``dialect`` to a SELECT statement."""
    n = int(n)
    sql = sql.rstrip().rstrip(";")
    if dialect == "tsql":
        # Inject TOP after the leading SELECT if not already present.
        if re.match(r"^\s*SELECT\s+TOP\s+\d+\b", sql, re.IGNORECASE):
            return sql
        return re.sub(r"^\s*SELECT\b", f"SELECT TOP {n}", sql, count=1, flags=re.IGNORECASE)
    return f"{sql} LIMIT {n}"


def _split_table(table: str) -> tuple[str | None, str]:
    """Split ``schema.table`` into ``(schema, table)``; schema may be None."""
    if "." in table:
        schema, name = table.split(".", 1)
        return schema, name
    return None, table


def _unavailable(provider: BaseProvider) -> str:
    return (
        f"ERROR: source '{provider.name}' unavailable. "
        f"last_error: {provider.last_error or 'no read engine configured'}"
    )


# ---------------------------------------------------------------------------
# Dialect-aware introspection SQL
# ---------------------------------------------------------------------------

def _describe_sql(dialect: str) -> str:
    """SELECT statement returning columns of a table (params: schema, table)."""
    if dialect in ("postgresql", "mysql"):
        return (
            "SELECT column_name, data_type, is_nullable, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_name = :table "
            "AND (:schema IS NULL OR table_schema = :schema) "
            "ORDER BY ordinal_position"
        )
    # tsql
    return (
        "SELECT column_name, data_type, is_nullable, character_maximum_length "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = :table "
        "AND (:schema IS NULL OR TABLE_SCHEMA = :schema) "
        "ORDER BY ORDINAL_POSITION"
    )


def _row_count_sql(provider: BaseProvider, table: str) -> str:
    """Return a row-count SELECT scoped to dialect quoting rules."""
    qname = provider.qualified_table_name(table)
    if provider.dialect == "tsql":
        # sys.partitions is faster and avoids full scans on large tables.
        schema, name = _split_table(table)
        if schema:
            return (
                "SELECT SUM(p.rows) AS approx_rows "
                "FROM sys.partitions p "
                "JOIN sys.objects o ON p.object_id = o.object_id "
                "JOIN sys.schemas s ON o.schema_id = s.schema_id "
                f"WHERE o.name = '{name}' AND s.name = '{schema}' AND p.index_id IN (0,1)"
            )
        return (
            "SELECT SUM(p.rows) AS approx_rows FROM sys.partitions p "
            "JOIN sys.objects o ON p.object_id = o.object_id "
            f"WHERE o.name = '{name}' AND p.index_id IN (0,1)"
        )
    return f"SELECT COUNT(*) AS row_count FROM {qname}"


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_tools(provider: BaseProvider) -> list[Callable[..., Any]]:
    """Build the four Agno-compatible callables for ``provider``.

    The closures capture ``provider`` (not its engine), so live updates to
    ``provider.engine_ro`` / ``provider.degraded`` after rotation are
    observed by every subsequent tool call.
    """
    pid = provider.id
    dialect = provider.dialect
    src_name = provider.name

    @tool(
        name=f"query_{pid}",
        description=(
            f"Execute a SELECT-only {dialect} query against source "
            f"'{src_name}' (id={pid}). Returns up to {MAX_ROWS} rows as CSV. "
            "Read-only enforced at engine level."
        ),
    )
    def query_source(sql: str) -> str:
        if not _is_read_only(sql):
            return "ERROR: only single SELECT/CTE statements are allowed"
        eng = provider.engine_ro
        if eng is None or provider.degraded:
            return _unavailable(provider)
        try:
            with eng.connect() as conn:
                result = conn.execute(text(sql))
                try:
                    columns = list(result.keys())
                except Exception:
                    columns = []
                rows = result.fetchmany(MAX_ROWS)

                # PII enforcement (best-effort — must not break the query)
                masked_rows = rows
                audit: dict = {}
                try:
                    from .pii_mask import (
                        apply_masking_to_rows,
                        extract_qualified_cols_from_sql,
                    )
                    cfg = getattr(provider, "config", {}) or {}
                    pii_action = cfg.get("pii_action", "flag")
                    source_id = getattr(provider, "source_id", 0)
                    project_slug = getattr(provider, "project_slug", None)
                    if project_slug:
                        try:
                            qualified = extract_qualified_cols_from_sql(
                                sql, dialect=getattr(provider, "dialect", "postgresql")
                            )
                        except Exception:
                            qualified = []
                        masked_rows, audit = apply_masking_to_rows(
                            rows, columns,
                            project_slug=project_slug,
                            source_id=source_id,
                            action=pii_action,
                            qualified_columns=qualified or None,
                        )
                except Exception as _pii_err:
                    logger.warning("PII masking failed (continuing unmasked): %s", _pii_err)
                    masked_rows = rows
                    audit = {}

                if audit.get("blocked"):
                    cols_str = ", ".join(audit.get("pii_columns_present", []))
                    return f"BLOCKED: query selects PII columns ({cols_str}) and pii_action=block."

                formatted = _format_rows(masked_rows)
                if audit.get("pii_columns_present"):
                    cols_str = ", ".join(audit["pii_columns_present"])
                    n_masked = audit.get("cells_masked", 0)
                    if audit.get("action") == "mask":
                        formatted = f"# PII masked: {cols_str} ({n_masked} cells)\n" + formatted
                    else:
                        formatted = f"# WARNING: result contains PII columns: {cols_str}\n" + formatted

                # Best-effort audit log — never break the query
                try:
                    if audit.get("pii_columns_present"):
                        from db.session import get_sql_engine
                        from sqlalchemy import text as _text
                        eng2 = get_sql_engine()
                        with eng2.connect() as c2:
                            c2.execute(_text(
                                "INSERT INTO public.dash_audit_log (project_slug, action, target, details, created_at) "
                                "VALUES (:slug, 'pii_query', :tgt, :det, NOW())"
                            ), {
                                "slug": getattr(provider, "project_slug", None),
                                "tgt": f"source:{getattr(provider, 'source_id', 0)}",
                                "det": json.dumps(audit, default=str),
                            })
                            c2.commit()
                except Exception:
                    pass

                return formatted
        except Exception as e:
            logger.warning("query_%s failed: %s", pid, e)
            return f"QUERY ERROR ({dialect}): {str(e)[:300]}"

    @tool(
        name=f"describe_{pid}",
        description=(
            f"Describe a table in source '{src_name}': returns columns, "
            "data types, nullability, and an approximate row count. "
            "Accepts 'schema.table' or 'table'."
        ),
    )
    def describe_table(table: str) -> str:
        eng = provider.engine_ro
        if eng is None or provider.degraded:
            return _unavailable(provider)
        schema, name = _split_table(table)
        try:
            with eng.connect() as conn:
                cols = conn.execute(
                    text(_describe_sql(dialect)),
                    {"schema": schema, "table": name},
                ).fetchall()
                if not cols:
                    return f"Table '{table}' not found in source '{src_name}'."
                try:
                    rc_row = conn.execute(text(_row_count_sql(provider, table))).fetchone()
                    rc = rc_row[0] if rc_row else None
                except Exception as e:
                    rc = f"unknown ({str(e)[:80]})"
                header = f"-- {src_name}.{table}  rows~{rc}\n"
                return header + _format_rows(cols)
        except Exception as e:
            logger.warning("describe_%s failed: %s", pid, e)
            return f"DESCRIBE ERROR ({dialect}): {str(e)[:300]}"

    @tool(
        name=f"sample_{pid}",
        description=(
            f"Return up to N sample rows from a table in source '{src_name}'. "
            "Args: table (str), n (int, default 10, max 1000)."
        ),
    )
    def sample_table(table: str, n: int = 10) -> str:
        eng = provider.engine_ro
        if eng is None or provider.degraded:
            return _unavailable(provider)
        try:
            n = max(1, min(int(n), MAX_ROWS))
        except (TypeError, ValueError):
            n = 10
        qname = provider.qualified_table_name(table)
        sql = _dialect_limit(dialect, f"SELECT * FROM {qname}", n)
        try:
            with eng.connect() as conn:
                rows = conn.execute(text(sql)).fetchmany(n)
                return _format_rows(rows)
        except Exception as e:
            logger.warning("sample_%s failed: %s", pid, e)
            return f"SAMPLE ERROR ({dialect}): {str(e)[:300]}"

    @tool(
        name=f"profile_{pid}",
        description=(
            f"Profile a column in source '{src_name}': total count, distinct "
            "count, null count, min, and max. Args: table (str), column (str)."
        ),
    )
    def profile_column(table: str, column: str) -> str:
        eng = provider.engine_ro
        if eng is None or provider.degraded:
            return _unavailable(provider)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", column):
            return "ERROR: column must be a simple identifier (letters, digits, underscore)"
        qname = provider.qualified_table_name(table)
        # Dialect-quote the column too — reuse provider's quoting via a
        # one-segment qualified name.
        qcol = provider.qualified_table_name(column)
        sql = (
            f"SELECT COUNT(*) AS total, "
            f"COUNT(DISTINCT {qcol}) AS distinct_count, "
            f"SUM(CASE WHEN {qcol} IS NULL THEN 1 ELSE 0 END) AS null_count, "
            f"MIN({qcol}) AS min_value, MAX({qcol}) AS max_value "
            f"FROM {qname}"
        )
        try:
            with eng.connect() as conn:
                rows = conn.execute(text(sql)).fetchall()
                return _format_rows(rows)
        except Exception as e:
            logger.warning("profile_%s failed: %s", pid, e)
            return f"PROFILE ERROR ({dialect}): {str(e)[:300]}"

    return [query_source, describe_table, sample_table, profile_column]


__all__ = ["make_tools"]
