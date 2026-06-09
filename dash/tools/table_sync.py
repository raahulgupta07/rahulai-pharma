"""Shared resolver: pick the table that matches the CURRENT uploaded data.

Every pharma tool and the gateway Outlets picker need to point at the same
stock/catalog table — the one from the *latest* upload, not whatever Postgres
happens to return first. Data lands as dated tables (e.g. balance_stock_07052026,
then balance_stock_08062026 on the next upload); the authoritative "which is
current" signal is `public.dash_table_metadata.updated_at`, written NOW() on
every ingest.

Resolution order:
  1. tables in `schema` that have ALL required columns,
  2. ordered by dash_table_metadata.updated_at DESC (newest upload first),
  3. tie-break / no-metadata fallback: table_name DESC.

A short TTL cache keeps hot paths cheap while still picking up a new upload
within seconds (no process restart needed — that was the old bug).
"""
from __future__ import annotations

import time

# slug == schema name for the single-tenant product, so dash_table_metadata
# rows are keyed by project_slug == schema.
_TTL = 30.0
_CACHE: dict[tuple[str, tuple[str, ...]], tuple[str, float]] = {}

# column-sets that identify each logical table.
# article_code is REQUIRED in STOCK_COLS so the resolver can't pick the derived
# shop_flat table (it keys on art_key, not article_code) even if shop_flat ever
# carries a dash_table_metadata row. The real source stock table
# (balance_stock_*) always has site_code + stock_qty + article_code. 2026-06-09.
STOCK_COLS = ("site_code", "stock_qty", "article_code")
CATALOG_COLS = ("brand_name", "generic_name")        # shop/graph catalog
INDICATION_COLS = ("generic_name", "indication")     # chemist catalog

# psycopg (%s positional) and SQLAlchemy (named) variants of the same query.
_SQL_PG = """
SELECT c.table_name
FROM information_schema.columns c
LEFT JOIN public.dash_table_metadata m
  ON m.project_slug = %s AND m.table_name = c.table_name
WHERE c.table_schema = %s AND c.column_name = ANY(%s)
GROUP BY c.table_name, m.updated_at
HAVING count(DISTINCT c.column_name) = %s
ORDER BY m.updated_at DESC NULLS LAST, c.table_name DESC
LIMIT 1
"""

_SQL_SA = """
SELECT c.table_name
FROM information_schema.columns c
LEFT JOIN public.dash_table_metadata m
  ON m.project_slug = :schema AND m.table_name = c.table_name
WHERE c.table_schema = :schema AND c.column_name = ANY(:cols)
GROUP BY c.table_name, m.updated_at
HAVING count(DISTINCT c.column_name) = :ncols
ORDER BY m.updated_at DESC NULLS LAST, c.table_name DESC
LIMIT 1
"""


def _key(schema: str, cols) -> tuple[str, tuple[str, ...]]:
    return (schema, tuple(sorted(set(cols))))


def _cache_get(key):
    v = _CACHE.get(key)
    if v and (time.time() - v[1]) < _TTL:
        return v[0]
    return None


def latest_table(cur, schema: str, required_cols, *, use_cache: bool = True):
    """psycopg cursor variant. Returns the bare table_name (no schema), or None.

    Caller is responsible for quoting (e.g. f'"{schema}"."{name}"').
    """
    key = _key(schema, required_cols)
    if use_cache:
        cached = _cache_get(key)
        if cached:
            return cached
    cols = list(required_cols)
    cur.execute(_SQL_PG, (schema, schema, cols, len(set(cols))))
    row = cur.fetchone()
    name = row[0] if row else None
    if name:
        _CACHE[key] = (name, time.time())
    return name


def latest_table_sa(conn, schema: str, required_cols, *, use_cache: bool = True):
    """SQLAlchemy connection variant. Returns the bare table_name, or None."""
    from sqlalchemy import text
    key = _key(schema, required_cols)
    if use_cache:
        cached = _cache_get(key)
        if cached:
            return cached
    cols = list(required_cols)
    name = conn.execute(
        text(_SQL_SA),
        {"schema": schema, "cols": cols, "ncols": len(set(cols))},
    ).scalar()
    if name:
        _CACHE[key] = (name, time.time())
    return name
