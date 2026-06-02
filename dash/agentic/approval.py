"""Generalized @approval decorator + approval queue helpers.

Mirrors the N-of-M sign-off pattern from ``dash/policy/signoff.py`` but is
generic over any callable. Backed by ``dash.dash_approval_requests`` /
``dash_approval_signatures`` / ``dash_approval_audit`` (migration 041).

Usage::

    from dash.agentic.approval import approval, ApprovalContext

    @approval("brain_delete", min_approvers=1, allowed_roles=["admin"])
    def delete_brain_entry(ctx: ApprovalContext, entry_id: int):
        ...

When ``EXPERIMENTAL_AGI != "1"`` the decorator becomes a pass-through — the
function is called directly and no approval row is inserted.

Self-approval is blocked (``requested_by != approver_id``).
Auto-expire via background sweeper task (every 5 min, marks expired pending
where ``now() > expires_at``).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine + helpers
# ---------------------------------------------------------------------------


def _engine() -> Engine:
    """Resolve a SQLAlchemy engine. Falls back to the global Dash engine."""
    try:
        from db.session import get_sql_engine  # type: ignore
        eng = get_sql_engine()
        if eng is not None:
            return eng
    except Exception as e:  # pragma: no cover
        logger.debug(f"get_sql_engine() unavailable: {e}")
    # Last resort: build from db.url
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.pool import NullPool
    from db import db_url  # type: ignore
    return _create_engine(db_url, poolclass=NullPool)


def _agi_enabled() -> bool:
    """Return True when ``EXPERIMENTAL_AGI=1``."""
    return os.getenv("EXPERIMENTAL_AGI", "").strip() == "1"


def _new_request_id() -> str:
    return f"apr_{secrets.token_hex(4)}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_role(user_id: int) -> Optional[str]:
    """Best-effort lookup of a user's role.

    Mirrors the role-resolution pattern used by ``dash/policy/signoff.py``
    (which delegates to ``app.auth``). Returns None if unknown.
    """
    try:
        from app.auth import SUPER_ADMIN  # type: ignore
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT username, role FROM public.dash_users WHERE id=:id"
            ), {"id": user_id}).fetchone()
        if not row:
            return None
        username, role = row[0], (row[1] if len(row) > 1 else None)
        if username == SUPER_ADMIN:
            return "super_admin"
        return role or "user"
    except Exception as e:
        logger.debug(f"_user_role({user_id}) failed: {e}")
        return None


def _audit(conn, request_id: Optional[str], event: str,
           actor_id: Optional[int] = None, metadata: Optional[dict] = None) -> None:
    try:
        conn.execute(text("""
            INSERT INTO dash.dash_approval_audit
              (request_id, event, actor_id, metadata)
            VALUES (:r, :e, :a, CAST(:m AS jsonb))
        """), {
            "r": request_id,
            "e": event,
            "a": actor_id,
            "m": json.dumps(metadata or {}),
        })
    except Exception as e:  # pragma: no cover
        logger.warning(f"approval audit insert failed: {e}")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ApprovalContext:
    """Passed as first arg to the decorated function on actual execution.

    During the *initial* decorated call the function is NOT invoked — instead
    a pending request row is written and ``ApprovalPending`` is returned.
    The function only ever runs from inside ``execute_if_ready`` once the
    signature threshold has been met, at which point an ``ApprovalContext``
    is constructed and prepended to the original kwargs.
    """
    request_id: str
    action_type: str
    requested_by: int
    project_slug: Optional[str] = None
    resource_id: Optional[str] = None
    payload: Optional[dict] = None


@dataclass
class ApprovalPending:
    request_id: str
    action_type: str
    status: str
    message: str
    expires_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ApprovalState:
    request_id: str
    status: str
    signatures: list
    required_approvers: int
    executed_result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Registry — decorated functions kept by action_type so the executor can find
# them when signatures complete.
# ---------------------------------------------------------------------------

_FN_REGISTRY: dict[str, Callable] = {}
_FN_LOCK = threading.Lock()


def _register(action_type: str, fn: Callable) -> None:
    with _FN_LOCK:
        _FN_REGISTRY[action_type] = fn


def _lookup_fn(action_type: str) -> Optional[Callable]:
    with _FN_LOCK:
        return _FN_REGISTRY.get(action_type)


# ---------------------------------------------------------------------------
# Core: insert pending request
# ---------------------------------------------------------------------------


def _create_request(
    *,
    action_type: str,
    requested_by: int,
    project_slug: Optional[str],
    resource_id: Optional[str],
    payload: dict,
    min_approvers: int,
    allowed_roles: list[str],
    expires_hours: int,
) -> dict:
    rid = _new_request_id()
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash.dash_approval_requests
              (id, project_slug, action_type, resource_id, payload,
               requested_by, required_approvers, allowed_roles,
               status, expires_at)
            VALUES
              (:id, :slug, :at, :rid, CAST(:pl AS jsonb),
               :uid, :req, CAST(:roles AS jsonb),
               'pending', now() + (:hrs || ' hours')::interval)
        """), {
            "id": rid,
            "slug": project_slug,
            "at": action_type,
            "rid": resource_id,
            "pl": json.dumps(payload, default=str),
            "uid": requested_by,
            "req": int(min_approvers),
            "roles": json.dumps(list(allowed_roles)),
            "hrs": str(int(expires_hours)),
        })
        _audit(conn, rid, "created", actor_id=requested_by, metadata={
            "action_type": action_type, "project_slug": project_slug,
            "resource_id": resource_id, "min_approvers": min_approvers,
        })
        row = conn.execute(text("""
            SELECT id, status, expires_at FROM dash.dash_approval_requests
             WHERE id=:id
        """), {"id": rid}).fetchone()
    return {
        "request_id": rid,
        "status": row[1] if row else "pending",
        "expires_at": row[2].isoformat() if row and row[2] is not None else None,
    }


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def approval(
    action_type: str,
    *,
    min_approvers: int = 1,
    allowed_roles: Optional[Iterable[str]] = None,
    expires_hours: int = 24,
) -> Callable:
    """Gate a callable behind an approval queue.

    Decorated function signature: ``fn(ctx: ApprovalContext, **kwargs) -> Any``

    On the *initial* call (when ``EXPERIMENTAL_AGI=1``) we return
    :class:`ApprovalPending`; the caller is responsible for surfacing the
    approval URL/UI to the user. Once enough signatures have been collected
    via :func:`sign`, the executor task runs the original function with an
    :class:`ApprovalContext` and stores the result in
    ``execution_result``, setting status ``executed``.

    When ``EXPERIMENTAL_AGI != "1"`` the decorator is a pass-through.
    """
    roles = list(allowed_roles or ["admin"])

    def deco(fn: Callable) -> Callable:
        _register(action_type, fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Pass-through when feature flag off.
            if not _agi_enabled():
                return fn(*args, **kwargs)

            # Identify caller. Convention: kwargs may include `requested_by`.
            requested_by = kwargs.pop("_requested_by", None) or kwargs.pop("requested_by", None)
            if requested_by is None:
                # Best-effort: surface a clear error rather than silently dropping.
                raise ValueError(
                    "@approval requires a 'requested_by' (int user_id) kwarg "
                    "when EXPERIMENTAL_AGI=1"
                )
            project_slug = kwargs.pop("_project_slug", None) or kwargs.get("project_slug")
            resource_id = kwargs.pop("_resource_id", None) or kwargs.get("resource_id")

            payload = {
                "fn": f"{fn.__module__}.{fn.__qualname__}",
                "args": [repr(a) for a in args],
                "kwargs": {k: (repr(v) if not _json_safe(v) else v) for k, v in kwargs.items()},
            }
            res = _create_request(
                action_type=action_type,
                requested_by=int(requested_by),
                project_slug=project_slug,
                resource_id=str(resource_id) if resource_id is not None else None,
                payload=payload,
                min_approvers=int(min_approvers),
                allowed_roles=roles,
                expires_hours=int(expires_hours),
            )
            return ApprovalPending(
                request_id=res["request_id"],
                action_type=action_type,
                status=res["status"],
                message=(
                    f"Approval required: {action_type}. "
                    f"{min_approvers} approver(s) needed. Request id={res['request_id']}."
                ),
                expires_at=res.get("expires_at"),
            )

        wrapper._approval_action_type = action_type  # type: ignore[attr-defined]
        wrapper._approval_min_approvers = min_approvers  # type: ignore[attr-defined]
        wrapper._approval_allowed_roles = roles  # type: ignore[attr-defined]
        wrapper._approval_wrapped = fn  # type: ignore[attr-defined]
        return wrapper

    return deco


def _json_safe(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sign / execute / query helpers
# ---------------------------------------------------------------------------


def get_request(request_id: str) -> Optional[dict]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(text("""
            SELECT id, project_slug, action_type, resource_id, payload,
                   requested_by, required_approvers, allowed_roles, status,
                   created_at, expires_at, resolved_at, execution_result
              FROM dash.dash_approval_requests
             WHERE id=:id
        """), {"id": request_id}).fetchone()
    if not row:
        return None
    cols = ["id", "project_slug", "action_type", "resource_id", "payload",
            "requested_by", "required_approvers", "allowed_roles", "status",
            "created_at", "expires_at", "resolved_at", "execution_result"]
    d = dict(zip(cols, row))
    for k in ("payload", "allowed_roles", "execution_result"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    for k in ("created_at", "expires_at", "resolved_at"):
        if d.get(k) is not None and not isinstance(d[k], str):
            try:
                d[k] = d[k].isoformat()
            except Exception:
                d[k] = str(d[k])
    return d


def list_signatures(request_id: str) -> list[dict]:
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, request_id, approver_id, decision, reason, signed_at
              FROM dash.dash_approval_signatures
             WHERE request_id=:id
          ORDER BY signed_at ASC
        """), {"id": request_id}).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "request_id": r[1],
            "approver_id": r[2],
            "decision": r[3],
            "reason": r[4],
            "signed_at": r[5].isoformat() if r[5] is not None and not isinstance(r[5], str) else r[5],
        })
    return out


def is_approved(request_id: str) -> bool:
    req = get_request(request_id)
    if not req:
        return False
    if req.get("status") in ("approved", "executed"):
        return True
    sigs = list_signatures(request_id)
    approves = [s for s in sigs if s.get("decision") == "approve"]
    rejects = [s for s in sigs if s.get("decision") == "reject"]
    return (not rejects) and (len(approves) >= int(req.get("required_approvers") or 1))


def sign(
    request_id: str,
    approver_id: int,
    decision: str,
    reason: str = "",
) -> ApprovalState:
    """Add a signature; runs ``execute_if_ready`` automatically."""
    if decision not in ("approve", "reject"):
        return ApprovalState(request_id=request_id, status="error",
                             signatures=[], required_approvers=0,
                             error=f"invalid decision: {decision}")
    req = get_request(request_id)
    if not req:
        return ApprovalState(request_id=request_id, status="error",
                             signatures=[], required_approvers=0,
                             error="request_not_found")
    if req.get("status") != "pending":
        return ApprovalState(request_id=request_id, status=req.get("status", "unknown"),
                             signatures=list_signatures(request_id),
                             required_approvers=int(req.get("required_approvers") or 1),
                             error=f"cannot sign: status is {req.get('status')}")
    # Self-approval blocked (mirrors signoff.py behaviour).
    if int(req.get("requested_by") or 0) == int(approver_id):
        return ApprovalState(request_id=request_id, status="pending",
                             signatures=list_signatures(request_id),
                             required_approvers=int(req.get("required_approvers") or 1),
                             error="self_approval_blocked")
    # Expired guard.
    try:
        exp = req.get("expires_at")
        if isinstance(exp, str):
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        else:
            exp_dt = exp  # may be datetime
        if exp_dt and exp_dt < _now():
            _expire_one(request_id)
            return ApprovalState(request_id=request_id, status="expired",
                                 signatures=list_signatures(request_id),
                                 required_approvers=int(req.get("required_approvers") or 1),
                                 error="expired")
    except Exception:
        pass

    eng = _engine()
    try:
        with eng.begin() as conn:
            conn.execute(text("""
                INSERT INTO dash.dash_approval_signatures
                  (request_id, approver_id, decision, reason)
                VALUES (:r, :a, :d, :why)
                ON CONFLICT (request_id, approver_id) DO UPDATE
                  SET decision=EXCLUDED.decision, reason=EXCLUDED.reason,
                      signed_at=now()
            """), {"r": request_id, "a": int(approver_id), "d": decision,
                   "why": reason or None})
            _audit(conn, request_id, "signed", actor_id=int(approver_id),
                   metadata={"decision": decision, "reason": reason or ""})
            if decision == "reject":
                conn.execute(text("""
                    UPDATE dash.dash_approval_requests
                       SET status='rejected', resolved_at=now()
                     WHERE id=:id AND status='pending'
                """), {"id": request_id})
                _audit(conn, request_id, "rejected", actor_id=int(approver_id),
                       metadata={"reason": reason or ""})
    except Exception as e:
        logger.exception("sign() failed")
        return ApprovalState(request_id=request_id, status="error",
                             signatures=list_signatures(request_id),
                             required_approvers=int(req.get("required_approvers") or 1),
                             error=str(e))

    # If threshold met, run executor.
    exec_result = None
    exec_error = None
    if decision == "approve" and is_approved(request_id):
        exec_result, exec_error = execute_if_ready(request_id)

    final = get_request(request_id) or req
    return ApprovalState(
        request_id=request_id,
        status=final.get("status", "pending"),
        signatures=list_signatures(request_id),
        required_approvers=int(final.get("required_approvers") or 1),
        executed_result=exec_result if exec_result is not None else final.get("execution_result"),
        error=exec_error,
    )


def execute_if_ready(request_id: str) -> tuple[Any, Optional[str]]:
    """If the request is approved, look up the registered fn and run it.

    Stores the result in ``execution_result`` and flips status to ``executed``.
    Returns ``(result, error)``.
    """
    req = get_request(request_id)
    if not req:
        return None, "request_not_found"
    if req.get("status") == "executed":
        return req.get("execution_result"), None
    if not is_approved(request_id):
        return None, None

    fn = _lookup_fn(req.get("action_type"))
    if fn is None:
        # Mark approved but unable to execute — caller may rerun later.
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(text("""
                UPDATE dash.dash_approval_requests
                   SET status='approved', resolved_at=now()
                 WHERE id=:id AND status='pending'
            """), {"id": request_id})
            _audit(conn, request_id, "approved", metadata={"executor": "missing"})
        return None, "executor_not_registered"

    payload = req.get("payload") or {}
    kwargs = payload.get("kwargs") or {}
    ctx = ApprovalContext(
        request_id=req["id"],
        action_type=req["action_type"],
        requested_by=int(req.get("requested_by") or 0),
        project_slug=req.get("project_slug"),
        resource_id=req.get("resource_id"),
        payload=payload,
    )
    target = getattr(fn, "_approval_wrapped", fn)
    result: Any = None
    err: Optional[str] = None
    try:
        if inspect.iscoroutinefunction(target):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule and wait synchronously is unsafe; fall back to new loop.
                    raise RuntimeError("async executor invoked inside running loop")
            except RuntimeError:
                pass
            result = asyncio.run(target(ctx, **kwargs))
        else:
            result = target(ctx, **kwargs)
    except Exception as e:
        err = str(e)
        logger.exception(f"approval executor failed for {request_id}")

    eng = _engine()
    with eng.begin() as conn:
        if err is None:
            conn.execute(text("""
                UPDATE dash.dash_approval_requests
                   SET status='executed', resolved_at=now(),
                       execution_result=CAST(:r AS jsonb)
                 WHERE id=:id
            """), {"id": request_id,
                   "r": json.dumps({"result": _to_jsonable(result)}, default=str)})
            _audit(conn, request_id, "executed", metadata={"ok": True})
        else:
            conn.execute(text("""
                UPDATE dash.dash_approval_requests
                   SET status='approved', resolved_at=now(),
                       execution_result=CAST(:r AS jsonb)
                 WHERE id=:id
            """), {"id": request_id,
                   "r": json.dumps({"error": err})})
            _audit(conn, request_id, "execution_failed", metadata={"error": err})
    return result, err


def _to_jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except Exception:
        return repr(v)


def cancel(request_id: str, requester_id: int) -> ApprovalState:
    """Requester cancels their own pending request."""
    req = get_request(request_id)
    if not req:
        return ApprovalState(request_id=request_id, status="error",
                             signatures=[], required_approvers=0,
                             error="request_not_found")
    if int(req.get("requested_by") or 0) != int(requester_id):
        return ApprovalState(request_id=request_id, status=req.get("status", "pending"),
                             signatures=list_signatures(request_id),
                             required_approvers=int(req.get("required_approvers") or 1),
                             error="not_requester")
    if req.get("status") != "pending":
        return ApprovalState(request_id=request_id, status=req.get("status", "pending"),
                             signatures=list_signatures(request_id),
                             required_approvers=int(req.get("required_approvers") or 1),
                             error=f"cannot cancel: status is {req.get('status')}")
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("""
            UPDATE dash.dash_approval_requests
               SET status='cancelled', resolved_at=now()
             WHERE id=:id AND status='pending'
        """), {"id": request_id})
        _audit(conn, request_id, "cancelled", actor_id=int(requester_id))
    return ApprovalState(request_id=request_id, status="cancelled",
                         signatures=list_signatures(request_id),
                         required_approvers=int(req.get("required_approvers") or 1))


def list_pending(
    project_slug: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    eng = _engine()
    clauses = ["status='pending'"]
    params: dict[str, Any] = {"lim": int(limit)}
    if project_slug:
        clauses.append("project_slug=:slug")
        params["slug"] = project_slug
    if action_type:
        clauses.append("action_type=:at")
        params["at"] = action_type
    sql = (
        "SELECT id, project_slug, action_type, resource_id, payload, "
        "requested_by, required_approvers, allowed_roles, status, "
        "created_at, expires_at FROM dash.dash_approval_requests "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC LIMIT :lim"
    )
    out = []
    with eng.connect() as conn:
        for r in conn.execute(text(sql), params).fetchall():
            d = {
                "id": r[0], "project_slug": r[1], "action_type": r[2],
                "resource_id": r[3], "payload": r[4],
                "requested_by": r[5], "required_approvers": r[6],
                "allowed_roles": r[7], "status": r[8],
                "created_at": r[9].isoformat() if r[9] is not None and not isinstance(r[9], str) else r[9],
                "expires_at": r[10].isoformat() if r[10] is not None and not isinstance(r[10], str) else r[10],
            }
            for k in ("payload", "allowed_roles"):
                if isinstance(d.get(k), str):
                    try:
                        d[k] = json.loads(d[k])
                    except Exception:
                        pass
            # Attach signature progress for the queue UI.
            sigs = list_signatures(d["id"])
            d["signatures"] = sigs
            d["signatures_collected"] = len([s for s in sigs if s.get("decision") == "approve"])
            out.append(d)
    return out


def list_audit(days: int = 14, action_type: Optional[str] = None,
               limit: int = 100) -> list[dict]:
    eng = _engine()
    params: dict[str, Any] = {"lim": int(limit), "d": int(days)}
    extra = ""
    if action_type:
        extra = """
            AND request_id IN (
              SELECT id FROM dash.dash_approval_requests WHERE action_type=:at
            )
        """
        params["at"] = action_type
    sql = (
        "SELECT id, request_id, event, actor_id, metadata, created_at "
        "FROM dash.dash_approval_audit "
        "WHERE created_at > now() - (:d || ' days')::interval " + extra +
        " ORDER BY created_at DESC LIMIT :lim"
    )
    out = []
    with eng.connect() as conn:
        for r in conn.execute(text(sql), params).fetchall():
            md = r[4]
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except Exception:
                    pass
            out.append({
                "id": r[0], "request_id": r[1], "event": r[2],
                "actor_id": r[3], "metadata": md,
                "created_at": r[5].isoformat() if r[5] is not None and not isinstance(r[5], str) else r[5],
            })
    return out


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


def _expire_one(request_id: str) -> None:
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("""
            UPDATE dash.dash_approval_requests
               SET status='expired', resolved_at=now()
             WHERE id=:id AND status='pending'
        """), {"id": request_id})
        _audit(conn, request_id, "expired")


def expire_overdue() -> int:
    """Mark all pending requests with expires_at < now() as expired.

    Returns the row count expired. Safe to call from a background task.
    """
    eng = _engine()
    with eng.begin() as conn:
        rows = conn.execute(text("""
            UPDATE dash.dash_approval_requests
               SET status='expired', resolved_at=now()
             WHERE status='pending' AND expires_at < now()
         RETURNING id
        """)).fetchall()
        for r in rows:
            _audit(conn, r[0], "expired")
    return len(rows)


_SWEEPER_TASK: Optional[asyncio.Task] = None
_SWEEPER_INTERVAL_S = 300.0  # every 5 min


async def _sweeper_loop() -> None:
    while True:
        try:
            n = expire_overdue()
            if n:
                logger.info(f"approval sweeper: expired {n} request(s)")
        except Exception as e:  # pragma: no cover
            logger.warning(f"approval sweeper error: {e}")
        await asyncio.sleep(_SWEEPER_INTERVAL_S)


def start_sweeper() -> None:
    """Start the background expiry sweeper if not already running."""
    global _SWEEPER_TASK
    if _SWEEPER_TASK is not None and not _SWEEPER_TASK.done():
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _SWEEPER_TASK = loop.create_task(_sweeper_loop())
        else:
            # Not inside a running loop — caller can manually invoke later.
            pass
    except RuntimeError:
        pass


__all__ = [
    "approval",
    "ApprovalContext",
    "ApprovalPending",
    "ApprovalState",
    "is_approved",
    "sign",
    "execute_if_ready",
    "cancel",
    "get_request",
    "list_signatures",
    "list_pending",
    "list_audit",
    "expire_overdue",
    "start_sweeper",
]
