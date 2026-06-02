"""Agno tool: query a configured connector by name with RBAC + audit.

Resolves the current viewer from `dash.tools.skill_refinery` ContextVars
(set by app.main AuthMiddleware) for RBAC. Falls back to super-admin allow
when no user is in context (e.g. background daemons).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from agno.tools import tool
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    if "id" in d and d["id"] is not None:
        d["id"] = str(d["id"])
    cfg = d.get("config")
    if isinstance(cfg, str):
        try:
            d["config"] = json.loads(cfg)
        except Exception:
            pass
    for jk in ("users_allowed", "ldap_groups_allowed"):
        v = d.get(jk)
        if isinstance(v, str):
            try:
                d[jk] = json.loads(v)
            except Exception:
                pass
    return d


def _current_viewer() -> dict | None:
    """Return enriched user dict or None if no context (treat as super-admin)."""
    try:
        from dash.tools.skill_refinery import (
            _viewer_user_id_var,  # type: ignore
        )

        uid = _viewer_user_id_var.get(None)
    except Exception:
        uid = None
        # Try alternate names
        try:
            import dash.tools.skill_refinery as sr  # type: ignore

            for name in ("viewer_user_id", "_viewer_user_id", "VIEWER_USER_ID"):
                v = getattr(sr, name, None)
                if v is not None and hasattr(v, "get"):
                    uid = v.get(None)
                    break
        except Exception:
            uid = None

    if uid is None:
        return None

    # Enrich with is_super_admin + aad_groups from dash_users
    try:
        from db.session import get_sql_engine

        eng = get_sql_engine()
        with eng.connect() as c:
            row = c.execute(
                text(
                    "SELECT id, username FROM public.dash_users WHERE id = :i"
                ),
                {"i": int(uid)},
            ).fetchone()
        if not row:
            return {"id": int(uid), "is_super_admin": False, "aad_groups": []}
        from os import getenv

        is_super = (row[1] or "") == (getenv("SUPER_ADMIN") or "admin")
        return {
            "id": int(row[0]),
            "username": row[1],
            "is_super_admin": is_super,
            "aad_groups": [],
        }
    except Exception:
        return {"id": int(uid), "is_super_admin": False, "aad_groups": []}


@tool
def query_connector(connection_name: str, sql: str) -> dict:
    """Run read-only SQL against a configured connector by name. RBAC-checked.

    Returns {rows, columns, row_count} on success, or {error: str} on failure.
    """
    from dash.connectors import audit, instantiate_client
    from dash.connectors.access import can_user_use
    from db.session import get_sql_engine

    t0 = time.time()

    # 1) Lookup connection by name
    try:
        eng = get_sql_engine()
        with eng.connect() as c:
            row = c.execute(
                text(
                    "SELECT * FROM dash.dash_connections "
                    "WHERE name = :n AND enabled = true"
                ),
                {"n": connection_name},
            ).fetchone()
    except Exception as e:
        return {"error": f"lookup failed: {e}"}
    if not row:
        return {"error": f"connection '{connection_name}' not found or disabled"}
    conn = _row_to_dict(row)
    conn_id = conn.get("id")

    # 2) Resolve viewer (None → background daemon → super-admin allow)
    viewer = _current_viewer()
    if viewer is None:
        viewer = {"id": None, "is_super_admin": True, "aad_groups": []}

    # 3) RBAC
    if not can_user_use(viewer, conn):
        return {"error": "access denied: connection not granted to your user/group"}

    # 4) Execute
    try:
        client = instantiate_client(conn)
        df = client.execute_query(sql, timeout_s=60, max_rows=5000)
        rows_out = df.to_dict("records")
        cols = list(df.columns)
        audit(
            conn_id,
            viewer.get("id"),
            "query",
            sql=sql,
            row_count=len(rows_out),
            duration_ms=int((time.time() - t0) * 1000),
        )
        return {"rows": rows_out, "columns": cols, "row_count": len(rows_out)}
    except Exception as e:
        audit(
            conn_id,
            viewer.get("id"),
            "query",
            sql=sql,
            duration_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )
        return {"error": str(e)}
