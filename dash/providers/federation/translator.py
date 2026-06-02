"""Dialect translator — convert SQL between Postgres / T-SQL / MySQL.

Primary: sqlglot.transpile() for full AST-aware translation.
Fallback: regex-based for common patterns.

Returns translated SQL + warnings list (translation imperfection).
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# Map our dialect names → sqlglot dialect names
_DIALECT_MAP = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "tsql": "tsql",
    "mssql": "tsql",
    "sqlserver": "tsql",
    "fabric": "tsql",
    "files": "duckdb",   # files queried via DuckDB
    "duckdb": "duckdb",
}


@dataclass
class TranslationResult:
    source_dialect: str
    target_dialect: str
    original_sql: str
    translated_sql: str
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


def translate(
    sql: str,
    *,
    source_dialect: str = "postgres",
    target_dialect: str = "postgres",
) -> TranslationResult:
    """Translate SQL between dialects via sqlglot. Falls back to regex."""
    src = _DIALECT_MAP.get(source_dialect.lower(), "postgres")
    tgt = _DIALECT_MAP.get(target_dialect.lower(), "postgres")

    result = TranslationResult(
        source_dialect=src,
        target_dialect=tgt,
        original_sql=sql,
        translated_sql=sql,  # default unchanged
    )

    if src == tgt:
        return result   # no-op

    # Try sqlglot.transpile()
    try:
        import sqlglot
        translated_list = sqlglot.transpile(sql, read=src, write=tgt)
        if translated_list:
            result.translated_sql = translated_list[0]
            return result
    except Exception as e:
        result.warnings.append(f"sqlglot transpile failed: {str(e)[:150]}")

    # Regex fallback
    result.translated_sql = _regex_translate(sql, src, tgt, result.warnings)
    return result


def _regex_translate(sql: str, src: str, tgt: str, warnings: list) -> str:
    """Best-effort regex translation. Very limited."""
    s = sql

    # Postgres → T-SQL
    if src == "postgres" and tgt == "tsql":
        # LIMIT N → TOP N (simple — only at start of SELECT)
        m = re.search(r"\bLIMIT\s+(\d+)\b", s, re.IGNORECASE)
        if m:
            n = m.group(1)
            s = re.sub(r"\bLIMIT\s+\d+\b", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\bSELECT\b", f"SELECT TOP {n}", s, count=1, flags=re.IGNORECASE)

        # ::type cast → CAST(x AS type)
        s = re.sub(r"(\w+)::(\w+)", r"CAST(\1 AS \2)", s)

        # NOW() → GETDATE()
        s = re.sub(r"\bNOW\(\)", "GETDATE()", s, flags=re.IGNORECASE)

        # INTERVAL — flag for manual review
        if "INTERVAL" in s.upper():
            warnings.append("INTERVAL used — needs manual DATEADD conversion")

        # Double-quoted identifiers → square brackets (best effort, risky)
        # Only for simple "name" patterns
        s = re.sub(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', r"[\1]", s)

    # T-SQL → Postgres
    elif src == "tsql" and tgt == "postgres":
        # TOP N → LIMIT N (best effort — moves to end)
        m = re.search(r"\bTOP\s+(\d+)\b", s, re.IGNORECASE)
        if m:
            n = m.group(1)
            s = re.sub(r"\bTOP\s+\d+\s*", "", s, flags=re.IGNORECASE)
            s = s.rstrip().rstrip(";") + f" LIMIT {n}"

        # GETDATE() → NOW()
        s = re.sub(r"\bGETDATE\(\)", "NOW()", s, flags=re.IGNORECASE)

        # Square brackets → double quotes
        s = re.sub(r"\[([a-zA-Z_][a-zA-Z0-9_ ]*)\]", r'"\1"', s)

        # ISNULL → COALESCE (preserve order — ISNULL takes 2 args, COALESCE takes N)
        s = re.sub(r"\bISNULL\(", "COALESCE(", s, flags=re.IGNORECASE)

    # Postgres → MySQL
    elif src == "postgres" and tgt == "mysql":
        # ::type cast → CAST(x AS type)
        s = re.sub(r"(\w+)::(\w+)", r"CAST(\1 AS \2)", s)
        # ILIKE → LIKE (case-sensitivity warning)
        if re.search(r"\bILIKE\b", s, re.IGNORECASE):
            warnings.append("ILIKE → LIKE; MySQL is case-insensitive by default")
            s = re.sub(r"\bILIKE\b", "LIKE", s, flags=re.IGNORECASE)
        # double-quoted → backticks
        s = re.sub(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', r"`\1`", s)

    # MySQL → Postgres
    elif src == "mysql" and tgt == "postgres":
        # Backticks → double quotes
        s = re.sub(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", r'"\1"', s)

    # T-SQL → MySQL
    elif src == "tsql" and tgt == "mysql":
        m = re.search(r"\bTOP\s+(\d+)\b", s, re.IGNORECASE)
        if m:
            n = m.group(1)
            s = re.sub(r"\bTOP\s+\d+\s*", "", s, flags=re.IGNORECASE)
            s = s.rstrip().rstrip(";") + f" LIMIT {n}"
        s = re.sub(r"\[([a-zA-Z_][a-zA-Z0-9_]*)\]", r"`\1`", s)

    # MySQL → T-SQL
    elif src == "mysql" and tgt == "tsql":
        m = re.search(r"\bLIMIT\s+(\d+)\b", s, re.IGNORECASE)
        if m:
            n = m.group(1)
            s = re.sub(r"\bLIMIT\s+\d+\b", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\bSELECT\b", f"SELECT TOP {n}", s, count=1, flags=re.IGNORECASE)
        s = re.sub(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", r"[\1]", s)

    else:
        warnings.append(f"no regex rules for {src} → {tgt}")

    return s


def normalize_to_canonical(sql: str, source_dialect: str) -> str:
    """Translate any dialect to Postgres (canonical form for federation engine).

    Postgres chosen as canonical because most expressive + sqlglot best support.
    """
    result = translate(sql, source_dialect=source_dialect, target_dialect="postgres")
    return result.translated_sql


def to_dialect(sql: str, target_dialect: str, *, source_dialect: str = "postgres") -> str:
    """Translate canonical (postgres) SQL to target dialect."""
    result = translate(sql, source_dialect=source_dialect, target_dialect=target_dialect)
    return result.translated_sql


def get_supported_dialects() -> list[str]:
    return list(set(_DIALECT_MAP.values()))
