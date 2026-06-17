"""
dash.ingest.schema_events
==========================

Phase-0 helpers for the self-adapting schema.

Two fail-soft utilities used by the dynamic-schema pipeline:

* ``log_schema_event`` — audit every schema change (a column added / removed /
  renamed / retyped, an empty load, an adapt or a rebuild) into the append-only
  ``public.dash_schema_events`` ledger so a human can later see exactly what the
  ingest pipeline did to a table's shape.
* ``snapshot_table_bak`` — before a destructive mutation, snapshot the current
  table into a ``"<table>__bak"`` copy (mirrors the existing inline pre-replace
  backup in ``app/upload.py``) so a bad adapt/rebuild can be restored.

BOTH helpers are FAIL-SOFT: any error is logged at WARNING and swallowed — they
never raise into the caller, so an audit/backup failure can never block ingest.

Public API
----------
log_schema_event   : insert one audit row (never raises)
snapshot_table_bak : copy a table to "<table>__bak" (returns bool, never raises)
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text

# Platform metadata in `public` (dash_schema_events) requires the unguarded
# read-write engine — get_sql_engine() blocks public-schema writes by design.
from db.session import get_write_engine  # CACHED SHARED — NEVER .dispose()

logger = logging.getLogger(__name__)


def log_schema_event(
    project_slug: str,
    table_name: str,
    event: str,
    detail: dict | None = None,
) -> None:
    """Append one row to ``public.dash_schema_events`` (fail-soft, never raises).

    Parameters
    ----------
    project_slug : owning project slug.
    table_name   : the table whose schema changed.
    event        : one of 'added' | 'removed' | 'renamed' | 'type_changed' |
                   'empty' | 'adapt' | 'rebuild' (not enforced — passed through).
    detail       : arbitrary JSON-able context, serialized into the ``detail``
                   jsonb column (``None`` → ``{}``).
    """
    try:
        engine = get_write_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.dash_schema_events "
                    "(project_slug, table_name, event, detail) "
                    "VALUES (:project_slug, :table_name, :event, :detail ::jsonb)"
                ),
                {
                    "project_slug": project_slug,
                    "table_name": table_name,
                    "event": event,
                    "detail": json.dumps(detail or {}),
                },
            )
    except Exception as exc:  # fail-soft — auditing must never block ingest
        logger.warning(
            "log_schema_event failed for %s.%s (%s): %s",
            project_slug,
            table_name,
            event,
            exc,
        )
        return


def snapshot_table_bak(schema: str, table: str) -> bool:
    """Snapshot ``"schema"."table"`` into ``"schema"."table__bak"`` (fail-soft).

    Drops any prior ``__bak`` (CASCADE) then recreates it as a full copy of the
    source table — mirrors the inline pre-replace backup in ``app/upload.py``.

    Returns ``True`` on success, ``False`` on failure (including the guard
    against backing up a backup). Never raises.

    NOTE: identifiers are interpolated (double-quoted) into the SQL, NOT bound as
    parameters — *schema*/*table* are internal, already-validated names (bound
    params cannot be used for identifiers anyway), matching the existing inline
    backup code.
    """
    # Guard: never back up a backup.
    if table.endswith("__bak"):
        return False

    try:
        engine = get_write_engine()
        with engine.begin() as conn:
            conn.execute(
                text(f'DROP TABLE IF EXISTS "{schema}"."{table}__bak" CASCADE')
            )
            conn.execute(
                text(
                    f'CREATE TABLE "{schema}"."{table}__bak" '
                    f'AS SELECT * FROM "{schema}"."{table}"'
                )
            )
        return True
    except Exception as exc:  # fail-soft — backup must never block the mutation
        logger.warning("snapshot_table_bak failed for %s.%s: %s", schema, table, exc)
        return False
