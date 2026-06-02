"""Auto-discovery cascade DELETE for project-scoped public.dash_* tables.

Auto-discovery means new dash_* tables w/ project_slug are picked up
automatically — never hand-edit a cascade list again.

Replaces the prior pattern in `app/projects.py:delete_project` of a 49-table
hardcoded list (which drifted every time a new feature added a
`project_slug`-keyed table). Now we query `information_schema.columns` once,
cache the result for 5 min, and DELETE from every match.
"""

from __future__ import annotations

import time
from sqlalchemy import text


# Module-level TTL cache keyed by the rounded-5min window timestamp.
# Cheap: a fresh query runs on cache miss; the discovered list rarely changes.
_CACHE_TTL_SECONDS = 300
_CACHE: dict = {"ts": 0.0, "tables": []}


def get_project_scoped_tables(conn) -> list[str]:
    """Discover every `public.dash_*` table that has a `project_slug` column.

    Returns a sorted list of bare table names (no schema prefix). Cached for
    5 min via a module-level dict keyed by the timestamp window; refreshes
    transparently on cache miss.
    """
    now = time.time()
    if _CACHE["tables"] and (now - _CACHE["ts"]) < _CACHE_TTL_SECONDS:
        return list(_CACHE["tables"])

    rows = conn.execute(text(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name LIKE 'dash_%'
          AND column_name = 'project_slug'
        ORDER BY table_name
        """
    )).fetchall()
    tables = sorted({r[0] for r in rows})

    _CACHE["ts"] = now
    _CACHE["tables"] = tables
    return list(tables)


# Future extension hook: tables that should be cleaned but either don't have a
# `project_slug` column or use a naming variation. Empty for now.
_EXTRA_TABLES: list[str] = []


def cascade_delete_project(conn_autocommit, slug: str) -> dict[str, int]:
    """Run `DELETE FROM public.{t} WHERE project_slug = :s` for every discovered table.

    Per-table try/except so a FK violation or permission error on table N
    does not block tables N+1..N+M. Caller must pass an AUTOCOMMIT-enabled
    connection so each DELETE is its own transaction.

    Returns `{table_name: rows_deleted_or_-1_on_error}`. Errored tables map
    to -1 so callers can distinguish "0 rows matched" from "delete failed".
    """
    tables = get_project_scoped_tables(conn_autocommit)
    # Merge in the future extension list (deduped, preserves order via dict)
    all_tables = list(dict.fromkeys(tables + _EXTRA_TABLES))

    results: dict[str, int] = {}
    for tbl in all_tables:
        try:
            res = conn_autocommit.execute(
                text(f"DELETE FROM public.{tbl} WHERE project_slug = :s"),
                {"s": slug},
            )
            results[tbl] = res.rowcount if res.rowcount is not None else 0
        except Exception:
            results[tbl] = -1
    return results
