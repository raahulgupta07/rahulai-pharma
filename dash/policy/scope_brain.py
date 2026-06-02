"""Phase G — auto-learn scope terminology into the project Brain.

When a user creates scopes (e.g., "MUM01 = Mumbai-Bandra"), each pair
becomes a project-scoped Brain alias entry so agents know what the
short codes mean without a separate glossary step.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _ensure_brain_table(conn) -> None:
    try:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_company_brain ("
            "id BIGSERIAL PRIMARY KEY,"
            "project_slug TEXT,"
            "scope TEXT,"
            "category TEXT,"
            "name TEXT,"
            "value TEXT,"
            "source TEXT,"
            "created_at TIMESTAMPTZ DEFAULT now(),"
            "UNIQUE(project_slug, scope, category, name))"
        ))
    except Exception as e:
        logger.debug(f"_ensure_brain_table failed: {e}")


def auto_learn_scopes(project_slug: str, scopes: Iterable[tuple[str, str]]) -> int:
    """Insert each (scope_id, scope_label) into dash_company_brain as alias.

    Idempotent. Returns count of upserts attempted.
    """
    pairs = [(sid, slab) for sid, slab in scopes if sid and slab]
    if not project_slug or not pairs:
        return 0
    n = 0
    try:
        eng = _engine()
        with eng.begin() as conn:
            _ensure_brain_table(conn)
            for sid, slab in pairs:
                try:
                    conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(project_slug, scope, category, name, value, source) "
                        "VALUES (:p, 'project', 'alias', :name, :val, 'auto_scope_learn') "
                        "ON CONFLICT (project_slug, scope, category, name) DO UPDATE "
                        "SET value = EXCLUDED.value"
                    ), {"p": project_slug, "name": str(sid)[:200], "val": str(slab)[:500]})
                    n += 1
                except Exception as e:
                    logger.debug(f"scope brain upsert failed for {sid}: {e}")
    except Exception as e:
        logger.warning(f"auto_learn_scopes failed: {e}")
    return n
