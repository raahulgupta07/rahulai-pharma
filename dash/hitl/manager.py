"""HITL manager — persistent pause/resume on confirm.

All inserts use ``CAST(:x AS jsonb)`` (per RTK conventions). Engine via
``db.session.get_sql_engine()`` (NullPool, PgBouncer-safe).
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Engine accessor ─────────────────────────────────────────────────────
def _engine():
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        from db import get_sql_engine  # type: ignore
        return get_sql_engine()


# ── Public API ──────────────────────────────────────────────────────────
def create_request(
    project_slug: str,
    agent: str,
    operation: str,
    details: Optional[Dict[str, Any]] = None,
    requested_by: str = "system",
    ttl_seconds: int = 3600,
) -> str:
    """Create a pending HITL request, return request_id."""
    rid = f"hitl_{secrets.token_hex(4)}"
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.dash_hitl_requests
                  (request_id, project_slug, agent_name, operation,
                   details, state, requested_by, expires_at)
                VALUES
                  (:rid, :ps, :agent, :op,
                   CAST(:details AS jsonb), 'pending', :rb,
                   now() + (:ttl || ' seconds')::interval)
                """
            ),
            {
                "rid": rid,
                "ps": project_slug,
                "agent": agent,
                "op": operation,
                "details": json.dumps(details or {}),
                "rb": requested_by,
                "ttl": str(int(ttl_seconds)),
            },
        )
    logger.info("hitl: created request %s (project=%s agent=%s)", rid, project_slug, agent)
    return rid


def get_request(request_id: str) -> Optional[Dict[str, Any]]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT request_id, project_slug, agent_name, operation,
                       details, state, requested_by, responded_by,
                       response_at, created_at, expires_at
                  FROM public.dash_hitl_requests
                 WHERE request_id = :rid
                """
            ),
            {"rid": request_id},
        ).mappings().first()
        return dict(row) if row else None


def _transition(request_id: str, responder: str, new_state: str,
                extra: Optional[Dict[str, Any]] = None) -> bool:
    eng = _engine()
    with eng.begin() as conn:
        # Merge extra into details (audit-friendly).
        if extra:
            conn.execute(
                text(
                    """
                    UPDATE public.dash_hitl_requests
                       SET state = :st,
                           responded_by = :rb,
                           response_at = now(),
                           details = COALESCE(details, '{}'::jsonb)
                                     || CAST(:ex AS jsonb)
                     WHERE request_id = :rid
                       AND state = 'pending'
                    """
                ),
                {"st": new_state, "rb": responder,
                 "ex": json.dumps(extra), "rid": request_id},
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE public.dash_hitl_requests
                       SET state = :st,
                           responded_by = :rb,
                           response_at = now()
                     WHERE request_id = :rid
                       AND state = 'pending'
                    """
                ),
                {"st": new_state, "rb": responder, "rid": request_id},
            )
        # Confirm change
        row = conn.execute(
            text("SELECT state FROM public.dash_hitl_requests WHERE request_id = :rid"),
            {"rid": request_id},
        ).first()
    return bool(row and row[0] == new_state)


def approve(request_id: str, responder: str = "user") -> bool:
    return _transition(request_id, responder, "approved")


def reject(request_id: str, responder: str = "user", reason: str = "") -> bool:
    return _transition(request_id, responder, "rejected",
                       extra={"reject_reason": reason} if reason else None)


def list_pending(project_slug: Optional[str] = None,
                 limit: int = 50) -> List[Dict[str, Any]]:
    eng = _engine()
    limit = max(1, min(200, int(limit)))
    with eng.connect() as conn:
        if project_slug:
            rows = conn.execute(
                text(
                    """
                    SELECT request_id, project_slug, agent_name, operation,
                           details, state, requested_by, created_at, expires_at
                      FROM public.dash_hitl_requests
                     WHERE state = 'pending'
                       AND project_slug = :ps
                     ORDER BY created_at DESC
                     LIMIT :lim
                    """
                ),
                {"ps": project_slug, "lim": limit},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT request_id, project_slug, agent_name, operation,
                           details, state, requested_by, created_at, expires_at
                      FROM public.dash_hitl_requests
                     WHERE state = 'pending'
                     ORDER BY created_at DESC
                     LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).mappings().all()
    return [dict(r) for r in rows]


def _maybe_expire(request_id: str) -> None:
    """Mark request as expired if past expires_at and still pending."""
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.dash_hitl_requests
                   SET state = 'expired'
                 WHERE request_id = :rid
                   AND state = 'pending'
                   AND expires_at < now()
                """
            ),
            {"rid": request_id},
        )


async def wait_for_response(request_id: str,
                            timeout_seconds: int = 300,
                            poll_interval: float = 2.0) -> Dict[str, Any]:
    """Poll until the request leaves the 'pending' state or timeout.

    Returns the full request row dict. If the wait times out, the request is
    marked as 'expired' and the row is returned in that state.
    """
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    while True:
        row = get_request(request_id)
        if row is None:
            return {"request_id": request_id, "state": "not_found"}
        if row["state"] != "pending":
            return row
        if time.monotonic() >= deadline:
            _maybe_expire(request_id)
            return get_request(request_id) or {"request_id": request_id, "state": "expired"}
        await asyncio.sleep(poll_interval)
