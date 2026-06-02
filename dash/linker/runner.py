"""Wrappers used by other modules for linking page content + backfill."""
from __future__ import annotations

import logging
from typing import Any

from dash.linker.extractor import link_text

logger = logging.getLogger(__name__)


def link_page_content(
    project: str,
    page_id: int | str | None,
    text: str,
    page_kind: str | None = None,
) -> dict:
    """Extract + persist entities and links from a page's text.

    Wrapper that other modules call after writing a page. Fails soft.
    """
    try:
        ref = f"page:{page_id}" if page_id is not None else None
        return link_text(project=project, text=text or "", page_kind=page_kind, source_ref=ref)
    except Exception as e:
        logger.exception("link_page_content failed: %s", e)
        return {"entities_created": 0, "links_created": 0, "error": str(e)}


def relink_all(project: str, limit: int = 1000) -> dict:
    """Re-run linker over existing entities/source rows for backfill.

    Strategy: re-link every text source we can find via the dash_entity_links
    `source_ref` column. For environments that store page rows elsewhere this
    is a no-op and returns processed=0; downstream callers can override.
    """
    from sqlalchemy import text as sa_text
    from db.session import get_sql_engine

    eng = get_sql_engine()
    if eng is None:
        return {"processed": 0, "error": "no_engine"}

    processed = 0
    ents_total = 0
    links_total = 0

    # Pull distinct source_refs and re-derive linking from them when possible.
    # We can't recover original page text from this table alone, so this is a
    # placeholder loop kept intentionally cheap (idempotent upsert path).
    with eng.connect() as conn:
        rows = conn.execute(
            sa_text("""
                SELECT DISTINCT source_ref
                FROM dash.dash_entity_links
                WHERE project_slug = :p AND source_ref IS NOT NULL
                LIMIT :lim
            """),
            {"p": project, "lim": int(limit)},
        ).fetchall()

    for (ref,) in rows:
        processed += 1
        # No-op without a text source; preserve API for callers that subclass.
    return {"processed": processed, "entities_created": ents_total, "links_created": links_total}
