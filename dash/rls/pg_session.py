"""Layer 3: Postgres SET LOCAL session vars per query.

For projects with mode='pg_rls', sets app.<key>=<value> as session vars so
Postgres RLS policies (USING (... = current_setting('app.store_id', true)::int))
can enforce isolation at DB level.

Defense-in-depth: even if Layer 2 rewriter is bypassed, Postgres still denies.
"""
import logging
import re as _re
from sqlalchemy import event as _sa_event, text
from dash.tools.skill_refinery import get_user_attrs

_log = logging.getLogger(__name__)
_RLS_ENABLED_PROJECTS_CACHE: dict[str, dict] = {}  # slug -> {ts, active, keys}

_VALID_ATTR_KEY = _re.compile(r"^[a-z_][a-z0-9_]{0,30}$")


def _is_pg_rls(project_slug: str) -> tuple[bool, list[str]]:
    """Returns (active, allowed_attr_keys) for this project."""
    import time
    cached = _RLS_ENABLED_PROJECTS_CACHE.get(project_slug)
    if cached and time.time() - cached["ts"] < 60:
        return cached["active"], cached["keys"]
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from db import db_url
    eng = create_engine(db_url, poolclass=NullPool)
    try:
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT enabled, mode, user_attr_keys FROM dash_project_rls_config
                WHERE project_slug=:s
            """), {"s": project_slug}).mappings().first()
        active = bool(row and row["enabled"] and row["mode"] == "pg_rls")
        keys = list(row["user_attr_keys"]) if row and row["user_attr_keys"] else []
    finally:
        eng.dispose()
    _RLS_ENABLED_PROJECTS_CACHE[project_slug] = {"ts": time.time(), "active": active, "keys": keys}
    return active, keys


def attach_pg_rls_session(engine, project_slug: str):
    """Attach 'begin' listener that issues SET LOCAL app.<key>=<val> on each transaction.

    No-op if mode != pg_rls or no user_attrs in context.
    Safe to call alongside the rewriter's before_cursor_execute listener.
    """
    @_sa_event.listens_for(engine, "begin")
    def _set_session_attrs(conn):
        try:
            active, allowed = _is_pg_rls(project_slug)
            if not active:
                return
            attrs = get_user_attrs() or {}
            if not attrs:
                # default_deny is enforced by RLS policy itself; we just skip SETs
                return
            for k, v in attrs.items():
                if k not in allowed:
                    continue  # extra attrs ignored
                if not _VALID_ATTR_KEY.match(k):
                    continue  # paranoia
                conn.execute(text(f"SET LOCAL app.{k} = :v"), {"v": str(v)})
        except Exception as e:
            _log.warning(f"pg_rls session attach failed (passthrough): {e}")


def invalidate_cache(project_slug: str | None = None):
    if project_slug:
        _RLS_ENABLED_PROJECTS_CACHE.pop(project_slug, None)
    else:
        _RLS_ENABLED_PROJECTS_CACHE.clear()
