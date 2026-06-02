"""Parse federated SQL → identify source prefixes.

Convention: source prefix = provider id (e.g. `fabric_42` or `pg_local`).
Tables addressed as `<provider_id>.<table_name>`.

Example:
  SELECT o.id, c.name FROM fabric_42.orders o
  JOIN postgres_local.customers c ON o.cid = c.id

Parses via sqlglot. Returns AST + list of (provider_id, table_name) refs.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TableRef:
    provider_id: str          # "fabric_42", "pg_local", "file_doc_77"
    table_name: str           # "orders"
    alias: Optional[str] = None
    full: str = ""            # original "fabric_42.orders"


@dataclass
class ParsedFederatedSQL:
    raw_sql: str
    table_refs: list[TableRef] = field(default_factory=list)
    provider_ids: set[str] = field(default_factory=set)
    is_federated: bool = False    # True if multi-source
    ast: Optional[object] = None
    error: Optional[str] = None


def parse(sql: str) -> ParsedFederatedSQL:
    """Parse SQL, find all qualified table refs."""
    result = ParsedFederatedSQL(raw_sql=sql)

    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError:
        result.error = "sqlglot not installed"
        # Regex fallback
        result.table_refs = _regex_extract_refs(sql)
        result.provider_ids = {r.provider_id for r in result.table_refs}
        result.is_federated = len(result.provider_ids) > 1
        return result

    try:
        ast = sqlglot.parse_one(sql)
        result.ast = ast
    except Exception as e:
        result.error = f"parse failed: {str(e)[:200]}"
        return result

    if ast is None:
        result.error = "empty AST"
        return result

    # Walk AST for Table nodes
    for node in ast.walk():
        if isinstance(node, exp.Table):
            db = node.args.get("db")
            tbl = node.args.get("this")
            alias_node = node.args.get("alias")

            db_name = db.name if hasattr(db, "name") and db else None
            tbl_name = tbl.name if hasattr(tbl, "name") and tbl else str(tbl) if tbl else ""
            alias_name = alias_node.name if hasattr(alias_node, "name") and alias_node else None

            if db_name and tbl_name:
                ref = TableRef(
                    provider_id=db_name,
                    table_name=tbl_name,
                    alias=alias_name,
                    full=f"{db_name}.{tbl_name}",
                )
                result.table_refs.append(ref)
                result.provider_ids.add(db_name)
            # Single-table reference (no schema prefix) — not federated

    result.is_federated = len(result.provider_ids) > 1
    return result


def _regex_extract_refs(sql: str) -> list[TableRef]:
    """Fallback when sqlglot unavailable. Best-effort."""
    refs: list[TableRef] = []
    # Pattern: word.word, capture before the dot as provider_id
    pattern = re.compile(
        r"\b([a-zA-Z_][a-zA-Z0-9_]+)\.([a-zA-Z_][a-zA-Z0-9_]+)\b",
        re.IGNORECASE,
    )
    seen = set()
    for m in pattern.finditer(sql):
        full = f"{m.group(1)}.{m.group(2)}"
        if full in seen:
            continue
        seen.add(full)
        refs.append(TableRef(
            provider_id=m.group(1),
            table_name=m.group(2),
            full=full,
        ))
    return refs


def extract_select_columns(sql: str) -> list[str]:
    """Best-effort SELECT projection extraction. Used for PII column qualification."""
    try:
        import sqlglot
        from sqlglot import expressions as exp
        ast = sqlglot.parse_one(sql)
        if not ast:
            return []
        cols = []
        for proj in ast.find_all(exp.Column):
            cols.append(proj.alias_or_name)
        return cols
    except Exception:
        return []


def is_select_only(sql: str) -> bool:
    """Verify only SELECT/WITH allowed (no DDL/DML)."""
    s = sql.strip().lower()
    if not (s.startswith("select") or s.startswith("with")):
        return False
    # Block sneaky multi-statement
    for kw in ("insert", "update", "delete", "drop", "alter",
                "truncate", "merge", "grant", "revoke", "create"):
        if re.search(rf"(^|\s|;)\s*{kw}\s+", sql, re.IGNORECASE):
            return False
    if ";" in sql.rstrip(";"):
        return False
    return True
