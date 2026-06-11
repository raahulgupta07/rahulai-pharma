"""Schema-drift guard for cached SQL (metric shortcut / golden corpus). [P0]

A pinned Q→SQL pair is served by re-running the SQL live, so the *number* is
always fresh. The real risk is the *schema* moving under a pinned SQL — a column
renamed / retyped / repurposed since the pair was promoted — so the SQL silently
returns a wrong-shaped or wrong-semantic result with NO LLM in the loop to catch
it. A hard break (missing column/table) already errors → the caller falls
through to the agent; this guard covers the silent-drift case.

We stamp the source tables' schema hash at promote time (`golden.promote`) and
re-check it at serve time (`try_metric_shortcut`). On drift we skip the shortcut
and let the agent re-plan against the new schema.

Schema fingerprint = `dash_table_metadata.col_hash` (cols-only, set by
`app/upload.save_fingerprint` on every ingest). Row-count changes do NOT trip the
guard — that is exactly what the live SQL re-run handles correctly.

Fail-soft: any error → returns "" (gate disabled for that call), never raises.
Legacy pairs without a stored hash are unaffected (serve as before).
"""
from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# FROM/JOIN <schema.>?<table> — bare table name captured.
_TBL_RE = re.compile(r"\b(?:FROM|JOIN)\s+(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)", re.I)


def sql_source_tables(sql: str) -> list[str]:
    """Lowercased, de-duped table names referenced in FROM/JOIN of `sql`.

    CTE aliases are harmless — they simply won't be found in
    dash_table_metadata, so they contribute nothing to the hash.
    """
    if not sql:
        return []
    seen: list[str] = []
    for m in _TBL_RE.finditer(sql):
        t = m.group(1).lower()
        if t not in seen:
            seen.append(t)
    return seen


def live_schema_hash(project_slug: str, tables: list[str]) -> str:
    """Combined schema fingerprint (col_hash) of `tables` from
    dash_table_metadata. Order-independent.

    Returns "" when NO listed table is found in metadata — caller must then
    NOT gate (can't compare → don't block on missing metadata).
    """
    tables = [t for t in (tables or []) if t]
    if not tables:
        return ""
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(_text(
                "SELECT table_name, col_hash FROM public.dash_table_metadata "
                "WHERE project_slug = :s AND table_name = ANY(:t)"
            ), {"s": project_slug, "t": tables}).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_schema_hash failed for %s: %s", project_slug, exc)
        return ""
    pairs = sorted((r[0], r[1] or "") for r in rows)
    if not pairs:
        return ""
    joined = "|".join(f"{name}:{ch}" for name, ch in pairs)
    return hashlib.sha256(joined.encode()).hexdigest()


def schema_hash_for_sql(project_slug: str, sql: str) -> str:
    """Schema hash of every source table referenced by `sql` ("" if none known)."""
    return live_schema_hash(project_slug, sql_source_tables(sql))
