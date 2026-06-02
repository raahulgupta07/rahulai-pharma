"""Shared column metadata cache. Single source of truth for `is_constant`,
`is_lineage`, role classification, length stats per (project_slug, table).

Replaces scattered inline COUNT(DISTINCT)/AVG(LENGTH) queries across:
- KG cell-value extractor (knowledge_graph.py)
- Training QA generator (upload.py _generate_sample_queries)
- Dashboard time-axis picker (dashboards/agent.py)
- Business rules generator

PR rule: any module deciding "is this column free-text / constant / dimension"
MUST read from get_column_metadata, never inline its own query.

Cached per (slug, table, max_updated_at) — 5min TTL. Cheap.
"""
from __future__ import annotations
import time
import logging
from sqlalchemy import text
from dash.utils.column_metadata import is_lineage_column

log = logging.getLogger(__name__)

_CACHE: dict[tuple, tuple[float, dict]] = {}  # (slug, table) → (expires_at, metadata)
_TTL_S = 300.0  # 5 min


def get_column_metadata(conn, slug: str, table: str) -> dict[str, dict]:
    """Return {col_name: {is_constant, is_lineage, distinct_n, total, avg_chars, max_chars, avg_bytes, role}}.

    Args:
        conn: live SQLAlchemy connection (caller manages lifecycle).
        slug: project slug = schema name.
        table: target table.

    Returns:
        dict keyed by column name. Empty dict on any error (fail-soft).
    """
    key = (slug, table)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    out: dict[str, dict] = {}
    try:
        # 1. Get all columns + types
        cols = conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t"
        ), {"s": slug, "t": table}).fetchall()
        if not cols:
            return out

        for col_name, dtype in cols:
            entry = {
                "name": col_name,
                "dtype": dtype,
                "is_lineage": is_lineage_column(col_name),
                "is_constant": False,
                "distinct_n": None,
                "total": None,
                "avg_chars": None,
                "max_chars": None,
                "avg_bytes": None,
                "role": "unknown",
            }
            try:
                if dtype in ("text", "character varying", "character"):
                    # Text columns: get all stats in one query.
                    row = conn.execute(text(
                        f'SELECT COUNT(*), COUNT(DISTINCT "{col_name}"), '
                        f'AVG(LENGTH("{col_name}"))::int, MAX(LENGTH("{col_name}")), '
                        f'AVG(OCTET_LENGTH("{col_name}"))::int '
                        f'FROM "{slug}"."{table}" WHERE "{col_name}" IS NOT NULL'
                    )).fetchone()
                    if row:
                        entry["total"] = row[0]
                        entry["distinct_n"] = row[1]
                        entry["avg_chars"] = row[2]
                        entry["max_chars"] = row[3]
                        entry["avg_bytes"] = row[4]
                        entry["is_constant"] = (row[1] or 0) <= 1
                        # Role assignment matches column_classifier rules
                        if entry["is_lineage"]:
                            entry["role"] = "lineage"
                        elif (entry["avg_chars"] or 0) > 25 or (entry["max_chars"] or 0) > 60 or (entry["avg_bytes"] or 0) > 50:
                            entry["role"] = "free_text"
                        elif (row[1] or 0) > 50 or ((row[1] or 0) / max(row[0] or 1, 1)) > 0.5:
                            entry["role"] = "skip"
                        else:
                            entry["role"] = "dimension"
                else:
                    # Numeric/date/etc: just check constant.
                    row = conn.execute(text(
                        f'SELECT COUNT(*), COUNT(DISTINCT "{col_name}") FROM "{slug}"."{table}" WHERE "{col_name}" IS NOT NULL'
                    )).fetchone()
                    if row:
                        entry["total"] = row[0]
                        entry["distinct_n"] = row[1]
                        entry["is_constant"] = (row[1] or 0) <= 1
                        entry["role"] = "measure" if dtype in ("integer", "bigint", "numeric", "double precision", "real") else "dim_or_date"
            except Exception as e:
                log.debug("col stat failed for %s.%s: %s", table, col_name, e)
            out[col_name] = entry
    except Exception as e:
        log.warning("get_column_metadata failed for %s.%s: %s", slug, table, e)
        return {}

    _CACHE[key] = (now + _TTL_S, out)
    return out


def invalidate(slug: str, table: str | None = None) -> None:
    """Drop cache entries for project (or single table)."""
    if table is None:
        keys = [k for k in _CACHE if k[0] == slug]
    else:
        keys = [(slug, table)]
    for k in keys:
        _CACHE.pop(k, None)
