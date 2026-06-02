"""Chat-scope ContextVars used to auto-write artifact links from tool hooks.

The chat endpoint (`app/projects.py`) sets CUR_SESSION_ID + CUR_PROJECT_SLUG
around `team.run`. Tools (`run_sql_query`, `apply_skill`) read these to write
links into `dash.dash_links` (Obsidian-style bidirectional links).

Fail-soft: any helper that can't reach the DB silently drops the write.
"""
from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Iterable

logger = logging.getLogger(__name__)

CUR_SESSION_ID: ContextVar[str | None] = ContextVar("CUR_SESSION_ID", default=None)
CUR_PROJECT_SLUG: ContextVar[str | None] = ContextVar("CUR_PROJECT_SLUG", default=None)


def _write_link_direct(
    src_type: str, src_id: str,
    dst_type: str, dst_id: str,
    rel: str,
    project_slug: str | None = None,
) -> None:
    """Direct DB insert (cheaper than HTTP roundtrip). Fail-soft.

    Uses ON CONFLICT DO NOTHING for idempotency.
    """
    if not (src_type and src_id and dst_type and dst_id and rel):
        return
    try:
        from db.session import get_write_engine
        from sqlalchemy import text as _t
        eng = get_write_engine()
        with eng.begin() as c:
            c.execute(
                _t("""
                    INSERT INTO dash.dash_links
                        (src_type, src_id, dst_type, dst_id, rel, project_slug)
                    VALUES
                        (:st, :si, :dt, :di, :rel, :ps)
                    ON CONFLICT (src_type, src_id, dst_type, dst_id, rel)
                    DO NOTHING
                """),
                {"st": src_type, "si": str(src_id),
                 "dt": dst_type, "di": str(dst_id),
                 "rel": rel, "ps": project_slug},
            )
    except Exception:
        logger.debug("dash_links insert skipped", exc_info=True)


# Naive table-ref extractor: pulls names after FROM/JOIN. Strips quotes + schema.
_TBL_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_."]*)',
    re.IGNORECASE,
)


def extract_table_refs(sql: str) -> list[str]:
    """Return unique table names referenced in a SQL string (best-effort)."""
    if not sql:
        return []
    out: set[str] = set()
    for m in _TBL_RE.finditer(sql):
        raw = m.group(1).strip().strip(';').strip('"').strip("'")
        # strip schema prefix
        if '.' in raw:
            raw = raw.split('.')[-1].strip('"').strip("'")
        if raw:
            out.add(raw)
    return sorted(out)


def link_chat_cites_tables(tables: Iterable[str]) -> None:
    """Write chat → cites → table links for current session."""
    sid = CUR_SESSION_ID.get()
    slug = CUR_PROJECT_SLUG.get()
    if not sid:
        return
    for t in tables:
        _write_link_direct(
            src_type="chat", src_id=sid,
            dst_type="table", dst_id=t,
            rel="cites",
            project_slug=slug,
        )


def link_chat_uses_skill(skill_id: int | str, skill_name: str | None = None) -> None:
    """Write chat → uses → skill link for current session."""
    sid = CUR_SESSION_ID.get()
    slug = CUR_PROJECT_SLUG.get()
    if not sid:
        return
    _write_link_direct(
        src_type="chat", src_id=sid,
        dst_type="skill", dst_id=str(skill_id),
        rel="uses",
        project_slug=slug,
    )
