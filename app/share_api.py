"""Shareable read-only conversation links — FEATURE E.

Three endpoints:
  POST /api/projects/{slug}/chats/{session_id}/share         (auth) — create a
        public token + frozen snapshot of the conversation (+ optional lineage).
  GET  /api/s/{token}                                         (NO AUTH) — return
        the snapshot if the token is live (exists, not revoked, not expired).
        Must be added to AuthMiddleware SKIP_PREFIXES ("/api/s").
  POST /api/projects/{slug}/chats/{session_id}/share/revoke  (auth) — revoke.

Reads the REAL conversation store:
  - public.dash_chat_sessions  → ownership guard (session_id + user_id)
  - ai.agno_sessions.runs       → the JSONB list of runs that holds the messages
    (mirrors app/projects.py::project_session_messages).

Engine rules (CLAUDE.md):
  - WRITES to public.dash_* go through db.session.get_write_engine.
  - Reads go through db.get_sql_engine (cached shared engine — never disposed).
  - JSONB params use CAST(:x AS jsonb), never :x::jsonb.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger("dash.share")

router = APIRouter(tags=["Share"])


# ── auth helper (mirrors app/projects.py::_get_user) ─────────────────────────
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _assert_owns_session(conn, slug: str, session_id: str, user_id) -> None:
    """Confirm this user owns this session in this project. 404 if not."""
    owned = conn.execute(
        text(
            "SELECT 1 FROM public.dash_chat_sessions "
            "WHERE session_id = :sid AND user_id = :uid"
        ),
        {"sid": session_id, "uid": user_id},
    ).fetchone()
    if not owned:
        raise HTTPException(404, "Conversation not found")


def _load_messages(conn, session_id: str) -> tuple[list[dict], str | None]:
    """Reconstruct user/assistant messages from ai.agno_sessions.runs.

    Mirrors the parent-run walk in app/projects.py::project_session_messages.
    Returns (messages, title). Fail-soft → ([], None) on any error.
    """
    messages: list[dict] = []
    title: str | None = None
    try:
        row = conn.execute(
            text("SELECT runs FROM ai.agno_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        ).fetchone()
        runs = (row[0] if row and isinstance(row[0], list) else []) or []

        for run in runs:
            if not isinstance(run, dict) or run.get("parent_run_id"):
                continue
            inp = run.get("input") or {}
            user_msg = ""
            if isinstance(inp, dict):
                user_msg = inp.get("input_content", "") or inp.get("content", "")
            elif isinstance(inp, str):
                user_msg = inp
            if user_msg:
                if title is None:
                    title = user_msg[:120]
                messages.append({"role": "user", "content": user_msg})

            content = run.get("content", "")
            if content:
                messages.append({"role": "assistant", "content": content})
    except Exception:
        logger.exception("share: failed to load messages for %s", session_id)
        return [], title
    return messages, title


def _load_lineage(conn, slug: str) -> dict:
    """Best-effort data lineage for the project — datasets + ingest batches.

    Reads from public.dash_ingest_contracts + public.dash_ingest_batches when
    present. Fail-soft → {"datasets": [], "batches": []}.
    """
    lineage = {"datasets": [], "batches": []}
    try:
        rows = conn.execute(
            text(
                "SELECT DISTINCT dataset FROM public.dash_ingest_contracts "
                "WHERE project_slug = :p AND dataset IS NOT NULL "
                "ORDER BY dataset LIMIT 200"
            ),
            {"p": slug},
        ).fetchall()
        lineage["datasets"] = [r[0] for r in rows if r[0]]
    except Exception:
        pass
    try:
        rows = conn.execute(
            text(
                "SELECT batch_id, status, file_count, created_at "
                "FROM public.dash_ingest_batches WHERE project_slug = :p "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            {"p": slug},
        ).fetchall()
        lineage["batches"] = [
            {
                "batch_id": r[0],
                "status": r[1],
                "file_count": r[2],
                "created_at": str(r[3]) if r[3] else None,
            }
            for r in rows
        ]
    except Exception:
        pass
    return lineage


# ── POST create share ────────────────────────────────────────────────────────
@router.post("/api/projects/{slug}/chats/{session_id}/share")
async def create_share(slug: str, session_id: str, request: Request):
    """Create a read-only public share link for a conversation."""
    user = _get_user(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    expire_days = body.get("expire_days", 7)
    include_lineage = body.get("include_lineage", True)
    try:
        expire_days = int(expire_days)
    except Exception:
        expire_days = 7
    if expire_days < 0:
        expire_days = 0
    include_lineage = bool(include_lineage)

    from db import get_sql_engine
    from db.session import get_write_engine

    # ── read the real conversation store ────────────────────────────────────
    eng = get_sql_engine()
    with eng.connect() as conn:
        _assert_owns_session(conn, slug, session_id, user["user_id"])
        messages, title = _load_messages(conn, session_id)
        lineage = _load_lineage(conn, slug) if include_lineage else None

    if not messages:
        raise HTTPException(404, "Conversation has no messages to share")

    snapshot = {
        "project_slug": slug,
        "session_id": session_id,
        "title": title or "Shared conversation",
        "messages": messages,
        "created_at": None,  # filled by created_at column; mirrored below for viewer
    }
    if include_lineage and lineage is not None:
        snapshot["lineage"] = lineage

    token = secrets.token_urlsafe(12)

    import json as _json

    write_eng = get_write_engine()
    with write_eng.begin() as conn:
        # Compute created_at server-side and reflect it into the snapshot.
        expires_clause = (
            "now() + (:days || ' days')::interval" if expire_days > 0 else "NULL"
        )
        conn.execute(
            text(
                "INSERT INTO public.dash_shared_conversations "
                "(token, project_slug, session_id, created_by, expires_at, "
                " revoked, include_lineage, snapshot) "
                f"VALUES (:token, :slug, :sid, :uid, {expires_clause}, "
                " FALSE, :inc, CAST(:snap AS jsonb))"
            ),
            {
                "token": token,
                "slug": slug,
                "sid": session_id,
                "uid": str(user.get("username") or user.get("user_id") or ""),
                "days": str(expire_days),
                "inc": include_lineage,
                "snap": _json.dumps(snapshot),
            },
        )
        row = conn.execute(
            text(
                "SELECT expires_at FROM public.dash_shared_conversations "
                "WHERE token = :token"
            ),
            {"token": token},
        ).fetchone()

    expires_at = str(row[0]) if row and row[0] else None
    return {"token": token, "url": f"/ui/s/{token}", "expires_at": expires_at}


# ── GET public read (NO AUTH — must be in SKIP_PREFIXES) ──────────────────────
@router.get("/api/s/{token}")
def get_shared(token: str):
    """Public read of a shared conversation snapshot. No auth header required."""
    from db import get_sql_engine

    eng = get_sql_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT snapshot, revoked, expires_at, created_at, include_lineage "
                "FROM public.dash_shared_conversations WHERE token = :token"
            ),
            {"token": token},
        ).fetchone()

    if not row:
        raise HTTPException(404, "Share link not found")

    snapshot, revoked, expires_at, created_at, include_lineage = (
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
    )

    if revoked:
        raise HTTPException(410, "This share link has been revoked")

    if expires_at is not None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        exp = expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise HTTPException(410, "This share link has expired")

    snap = snapshot if isinstance(snapshot, dict) else {}
    # Surface metadata the viewer needs even if the snapshot predates a field.
    snap.setdefault("title", "Shared conversation")
    snap.setdefault("messages", [])
    if not include_lineage:
        snap.pop("lineage", None)
    snap["expires_at"] = str(expires_at) if expires_at else None
    snap["created_at"] = str(created_at) if created_at else snap.get("created_at")
    return snap


# ── POST revoke ───────────────────────────────────────────────────────────────
@router.post("/api/projects/{slug}/chats/{session_id}/share/revoke")
async def revoke_share(slug: str, session_id: str, request: Request):
    """Revoke all share links for this conversation (auth required)."""
    user = _get_user(request)

    from db import get_sql_engine
    from db.session import get_write_engine

    eng = get_sql_engine()
    with eng.connect() as conn:
        _assert_owns_session(conn, slug, session_id, user["user_id"])

    write_eng = get_write_engine()
    with write_eng.begin() as conn:
        res = conn.execute(
            text(
                "UPDATE public.dash_shared_conversations SET revoked = TRUE "
                "WHERE project_slug = :slug AND session_id = :sid"
            ),
            {"slug": slug, "sid": session_id},
        )
        revoked_count = res.rowcount or 0

    return {"status": "ok", "revoked": revoked_count}
