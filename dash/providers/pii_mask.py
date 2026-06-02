"""Apply PII masking to query results based on column_classification.json.

Reads the classifier output for a (project_slug, source_id) pair and, given
a list of column names from the result set, returns a transformed copy with
PII values masked according to each column's masking_recommended hint.

Strategies:
- hash:        sha256 hex prefix (first 12 chars) — irreversible, idempotent
- redact:      "***REDACTED***"
- mask_email:  show first char + '***' + domain
- mask_phone:  show last 4 digits, mask rest
- generalize:  for quasi-PII like ZIP — keep first 3 digits
- truncate:    keep first 2 chars

Action modes (per source config):
- "flag" (default):     warn but don't mask
- "mask":                actively mask before returning
- "block":               refuse the query if any selected col is PII
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")


_classification_cache: dict[tuple[str, int], dict] = {}


def _load_classification(project_slug: str, source_id: int) -> dict:
    """Load and cache column_classification.json for a source."""
    key = (project_slug, source_id)
    if key in _classification_cache:
        return _classification_cache[key]
    p = KNOWLEDGE_DIR / project_slug / f"source_{source_id}" / "column_classification.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except Exception as e:
            logger.warning(f"PII mask: failed to read {p}: {e}")
    _classification_cache[key] = data
    return data


def invalidate_cache(project_slug: str | None = None, source_id: int | None = None) -> None:
    """Clear cache when classification is regenerated."""
    if project_slug is None and source_id is None:
        _classification_cache.clear()
    else:
        _classification_cache.pop((project_slug, source_id or 0), None)


# Conservativeness ranking — higher number = more conservative (preferred when
# the same column name appears in multiple tables with different strategies).
_MASK_RANK = {
    "block": 100,
    "redact": 90,
    "hash": 80,
    "hash_email": 80,
    "mask_email": 70,
    "mask_phone": 70,
    "generalize": 50,
    "truncate": 40,
}


def get_pii_columns(project_slug: str, source_id: int) -> dict[str, dict]:
    """Return {col_name: {tables: [...], masking_recommended (most conservative),
                          pii_classes: [...], tables_to_masks: {table: mask}}}.

    When multiple tables declare the same column name with different masking
    strategies, the MOST CONSERVATIVE strategy (highest rank in `_MASK_RANK`)
    is selected as the default `masking_recommended`. Per-table strategies are
    preserved in `tables_to_masks` for callers that can disambiguate via
    qualified column names (see `apply_masking_to_rows`).

    Example: same `email` column in [orders, leads] with strategies
    [hash, redact] → masking_recommended="redact" (rank 90 > 80).
    """
    data = _load_classification(project_slug, source_id)
    out: dict[str, dict] = {}
    for tbl, cols in (data or {}).items():
        if not isinstance(cols, dict):
            continue
        for col, c in cols.items():
            if not (isinstance(c, dict) and c.get("pii")):
                continue
            mask = c.get("masking_recommended") or "redact"
            entry = out.setdefault(col, {
                "tables": [],
                "masking_recommended": mask,
                "pii_classes": [],
                "tables_to_masks": {},
                # Back-compat alias: first-seen table (older callers may read
                # ['table'] directly). Multi-table info is in ['tables'].
                "table": tbl,
                "pii_class": c.get("pii_class"),
            })
            entry["tables"].append(tbl)
            entry["pii_classes"].append(c.get("pii_class"))
            entry["tables_to_masks"][tbl] = mask
            # Conservative pick — highest rank wins
            if _MASK_RANK.get(mask, 0) > _MASK_RANK.get(entry["masking_recommended"], 0):
                entry["masking_recommended"] = mask
    return out


def mask_value(val: Any, strategy: str) -> Any:
    """Apply a masking strategy to a single value. None passes through."""
    if val is None:
        return None
    s = str(val)
    if not s:
        return s

    if strategy == "redact":
        return "***REDACTED***"

    if strategy == "hash" or strategy == "hash_email":
        h = hashlib.sha256(s.encode()).hexdigest()[:12]
        if "@" in s and strategy == "hash_email":
            domain = s.rsplit("@", 1)[-1]
            return f"hashed_{h}@{domain}"
        return f"hashed_{h}"

    if strategy == "mask_email":
        if "@" not in s:
            return "***"
        local, domain = s.split("@", 1)
        if not local:
            return f"***@{domain}"
        return f"{local[0]}***@{domain}"

    if strategy == "mask_phone":
        digits = re.sub(r"\D", "", s)
        if len(digits) < 4:
            return "***"
        return f"***-***-{digits[-4:]}"

    if strategy == "generalize":
        # keep first 3 chars (ZIP, postal code, IP first octet)
        return s[:3] + "*" * max(0, len(s) - 3)

    if strategy == "truncate":
        return s[:2] + "***" if len(s) > 2 else "***"

    # default: redact
    return "***"


def apply_masking_to_rows(
    rows: list[Any],
    columns: list[str],
    project_slug: str,
    source_id: int,
    action: str = "flag",
    *,
    qualified_columns: Optional[list[str]] = None,
) -> tuple[list[Any], dict]:
    """Walk rows + columns, mask PII cells in-place per classification.

    Returns (transformed_rows, audit_info) where audit_info =
    {pii_columns_present: [...], cells_masked: int, action: str, blocked: bool}.

    Modes:
        flag  — return rows unchanged but include pii cols in audit
        mask  — replace PII cell values with masked output
        block — return empty rows + audit.blocked=True

    qualified_columns (optional): list parallel to `columns` of "table.col"
        strings extracted from the SQL projection. When provided, the masking
        strategy is resolved against the exact source table (precise) rather
        than the conservative cross-table default. Falls back per-column to
        bare-name lookup (using the conservative pick) when an entry is not
        qualified or the table is unknown.
    """
    pii_map = get_pii_columns(project_slug, source_id)
    if not pii_map or not rows:
        return rows, {"pii_columns_present": [], "cells_masked": 0, "action": action, "blocked": False}

    pii_indices: dict[int, str] = {}
    cols_present: list[str] = []
    for i, col in enumerate(columns):
        # Try qualified lookup first, if provided
        matched = False
        if qualified_columns and i < len(qualified_columns):
            qcol = qualified_columns[i]
            if isinstance(qcol, str) and "." in qcol:
                table, bare = qcol.rsplit(".", 1)
                if bare in pii_map:
                    entry = pii_map[bare]
                    strategy = entry.get("tables_to_masks", {}).get(table) \
                        or entry["masking_recommended"]
                    pii_indices[i] = strategy
                    cols_present.append(qcol)
                    matched = True
        if matched:
            continue
        # Fall back to bare-col lookup (conservative pick)
        if col in pii_map:
            pii_indices[i] = pii_map[col]["masking_recommended"]
            cols_present.append(col)
    audit = {
        "pii_columns_present": cols_present,
        "cells_masked": 0,
        "action": action,
        "blocked": False,
    }

    if not pii_indices:
        return rows, audit

    if action == "block":
        audit["blocked"] = True
        return [], audit

    if action == "flag":
        # don't transform, just report
        return rows, audit

    # action == "mask"
    transformed = []
    for r in rows:
        # Each row is a tuple/list. Mutate to a list and replace.
        try:
            new_row = list(r)
        except TypeError:
            new_row = [r]
        for idx, strategy in pii_indices.items():
            if idx < len(new_row):
                new_row[idx] = mask_value(new_row[idx], strategy)
                audit["cells_masked"] += 1
        transformed.append(tuple(new_row))
    return transformed, audit


def extract_qualified_cols_from_sql(sql: str, dialect: str = "postgresql") -> list[str]:
    """Best-effort: parse a SQL SELECT and return projection column names,
    qualified as "table.col" when an alias prefix is present.

    Examples:
        'SELECT t.email, t.id FROM orders t'  → ['orders.email', 'orders.id']
        'SELECT email, id FROM orders'         → ['email', 'id']
        'SELECT u.email AS e FROM users u'     → ['users.email']

    Returns [] on parse failure (caller should fall back to bare names).

    Limitations: the regex fallback is naive — sub-queries, CTEs, JOINs with
    multiple aliases, and complex expressions may produce misaligned or empty
    results. Wildcard SELECT (*) is not expanded. Callers should treat the
    output as advisory and rely on bare-name (conservative) lookup when
    extraction fails.
    """
    if not sql or not isinstance(sql, str):
        return []

    # Try sqlglot first (precise)
    try:
        import sqlglot
        from sqlglot import expressions as exp

        parsed = sqlglot.parse_one(sql, read=dialect)
        if parsed is None:
            raise ValueError("empty parse")

        # Build alias -> table_name map from FROM/JOIN sources
        alias_map: dict[str, str] = {}
        for tbl in parsed.find_all(exp.Table):
            tname = tbl.name
            if not tname:
                continue
            alias = tbl.alias or tname
            alias_map[alias] = tname
            alias_map[tname] = tname

        out: list[str] = []
        select = parsed.find(exp.Select)
        if select is None:
            return []
        for proj in select.expressions or []:
            # Strip outer alias (AS x)
            target = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(target, exp.Column):
                col_name = target.name
                tbl_alias = target.table
                if tbl_alias and tbl_alias in alias_map:
                    out.append(f"{alias_map[tbl_alias]}.{col_name}")
                elif tbl_alias:
                    out.append(f"{tbl_alias}.{col_name}")
                else:
                    out.append(col_name)
            else:
                # Expression / function — emit alias name if present, else ""
                name = proj.alias_or_name if hasattr(proj, "alias_or_name") else ""
                out.append(name or "")
        return out
    except ImportError:
        pass
    except Exception:
        # Fall through to regex
        pass

    # Regex fallback — basic single-level SELECT ... FROM
    try:
        m = re.search(r"SELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\s+(.+)",
                      sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return []
        proj_str = m.group(1)
        from_str = m.group(2)

        # Build a tiny alias map from FROM clause: "orders o" or "orders AS o"
        alias_map: dict[str, str] = {}
        # Stop FROM at the next clause keyword
        from_clean = re.split(
            r"\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|OFFSET|UNION|INTERSECT|EXCEPT)\b",
            from_str, maxsplit=1, flags=re.IGNORECASE,
        )[0]
        # Split on commas and JOIN keywords
        from_parts = re.split(
            r",|\b(?:LEFT|RIGHT|INNER|OUTER|FULL|CROSS)?\s*JOIN\b",
            from_clean, flags=re.IGNORECASE,
        )
        for part in from_parts:
            part = part.strip().rstrip(";")
            if not part:
                continue
            # drop ON ... condition
            part = re.split(r"\bON\b", part, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            tokens = re.findall(r"[\w\.]+", part)
            if not tokens:
                continue
            tbl = tokens[0].split(".")[-1]
            alias = tokens[-1] if len(tokens) > 1 and tokens[-1].lower() != "as" else tbl
            if len(tokens) >= 3 and tokens[-2].lower() == "as":
                alias = tokens[-1]
            alias_map[alias] = tbl
            alias_map[tbl] = tbl

        out: list[str] = []
        # Naive comma split — does not handle commas inside function calls
        for piece in proj_str.split(","):
            piece = piece.strip().rstrip(",").strip()
            # strip alias
            piece = re.split(r"\s+AS\s+|\s+", piece, maxsplit=1, flags=re.IGNORECASE)[0]
            piece = piece.strip().strip("`\"[]")
            if "." in piece:
                tbl_alias, col = piece.rsplit(".", 1)
                tbl = alias_map.get(tbl_alias, tbl_alias)
                out.append(f"{tbl}.{col}")
            else:
                out.append(piece)
        return out
    except Exception:
        return []


__all__ = [
    "apply_masking_to_rows",
    "extract_qualified_cols_from_sql",
    "get_pii_columns",
    "invalidate_cache",
    "mask_value",
]
