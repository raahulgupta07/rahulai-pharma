"""
Brain entry versioning + rollback (Ontology Phase D)
====================================================

Every create/update/delete on `dash_company_brain` writes a snapshot to
`dash_brain_versions` in the SAME transaction, so versioning never desyncs
from the main table.

Public endpoints:
    GET  /api/brain/{id}/history
    GET  /api/brain/{id}/version/{version}
    POST /api/brain/{id}/rollback/{version}
    GET  /api/brain/changes/recent

Helper:
    snapshot_version(conn, brain_id, change_type, changed_by, change_reason, row=None)
        Insert a version row inside an open transaction. Reads current row state
        if `row` not supplied. Returns the new version number.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brain", tags=["BrainVersions"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_brain_versions_table() -> None:
    """Create version table if migration has not been applied yet."""
    with _engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_brain_versions (
                id BIGSERIAL PRIMARY KEY,
                brain_id BIGINT NOT NULL,
                version INT NOT NULL,
                category TEXT,
                name TEXT,
                definition TEXT,
                project_slug TEXT,
                user_id BIGINT,
                metadata JSONB,
                change_type TEXT NOT NULL,
                changed_by BIGINT,
                change_reason TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_brain_versions_brain_id "
            "ON public.dash_brain_versions (brain_id, version DESC)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_brain_versions_changed_by "
            "ON public.dash_brain_versions (changed_by)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_brain_versions_created_at "
            "ON public.dash_brain_versions (created_at DESC)"
        ))
        conn.commit()


def init_brain_versions() -> None:
    """Initialize version table. Call during app lifespan."""
    try:
        _bootstrap_brain_versions_table()
    except Exception as e:
        logger.warning(f"Brain versions init skipped: {e}")


# ---------------------------------------------------------------------------
# Snapshot helper (called by brain.py inside transactions)
# ---------------------------------------------------------------------------

def _read_current_row(conn: Connection, brain_id: int) -> dict | None:
    row = conn.execute(text(
        "SELECT id, category, name, definition, metadata, project_slug, user_id "
        "FROM public.dash_company_brain WHERE id = :id"
    ), {"id": brain_id}).fetchone()
    if not row:
        return None
    meta = row[4] if isinstance(row[4], dict) else (json.loads(row[4]) if row[4] else {})
    return {
        "id": row[0],
        "category": row[1],
        "name": row[2],
        "definition": row[3],
        "metadata": meta,
        "project_slug": row[5],
        "user_id": row[6],
    }


def _next_version(conn: Connection, brain_id: int) -> int:
    cur = conn.execute(text(
        "SELECT COALESCE(MAX(version), 0) FROM public.dash_brain_versions WHERE brain_id = :id"
    ), {"id": brain_id}).scalar()
    return int(cur or 0) + 1


def snapshot_version(
    conn: Connection,
    brain_id: int,
    change_type: str,
    changed_by: int | None,
    change_reason: str | None = None,
    row: dict[str, Any] | None = None,
) -> int:
    """Insert a version snapshot for `brain_id` inside an OPEN transaction.

    Caller is responsible for the surrounding transaction (commit/rollback).
    Returns the new version number.

    `row` may be supplied (e.g. a pre-delete read) to capture state that has
    already been removed from the main table; otherwise the current row is read.
    """
    if change_type not in ("create", "update", "delete", "rollback"):
        raise ValueError(f"invalid change_type: {change_type}")

    if row is None:
        row = _read_current_row(conn, brain_id)

    if row is None:
        # Nothing to snapshot — caller passed bad id and didn't supply row.
        return 0

    version = _next_version(conn, brain_id)
    metadata_json = json.dumps(row.get("metadata") or {})

    conn.execute(text("""
        INSERT INTO public.dash_brain_versions (
            brain_id, version, category, name, definition,
            project_slug, user_id, metadata,
            change_type, changed_by, change_reason
        ) VALUES (
            :brain_id, :version, :category, :name, :definition,
            :project_slug, :user_id, CAST(:metadata AS JSONB),
            :change_type, :changed_by, :change_reason
        )
    """), {
        "brain_id": brain_id,
        "version": version,
        "category": row.get("category"),
        "name": row.get("name"),
        "definition": row.get("definition"),
        "project_slug": row.get("project_slug"),
        "user_id": row.get("user_id"),
        "metadata": metadata_json,
        "change_type": change_type,
        "changed_by": changed_by,
        "change_reason": change_reason,
    })
    return version


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _is_super_admin(user: dict) -> bool:
    # admin tier OR super (brain rollback also allowed for original author below)
    try:
        from app.auth import SUPER_ADMIN
        return bool(user.get("is_admin")) or user.get("username") == SUPER_ADMIN
    except Exception:
        return False


def _user_id(user: dict) -> int | None:
    return user.get("user_id") or user.get("id")


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------

def _row_to_dict(r: Any) -> dict:
    meta = r[7] if isinstance(r[7], dict) else (json.loads(r[7]) if r[7] else {})
    return {
        "id": r[0],
        "brain_id": r[1],
        "version": r[2],
        "category": r[3],
        "name": r[4],
        "definition": r[5],
        "project_slug": r[6],
        "metadata": meta,
        "user_id": r[8],
        "change_type": r[9],
        "changed_by": r[10],
        "change_reason": r[11],
        "created_at": str(r[12]) if r[12] else None,
    }


_SELECT_COLS = (
    "id, brain_id, version, category, name, definition, project_slug, "
    "metadata, user_id, change_type, changed_by, change_reason, created_at"
)


@router.get("/{brain_id}/history")
def list_history(brain_id: int, request: Request, limit: int = 50, offset: int = 0):
    """List versions for a brain entry, newest first."""
    _get_user(request)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    with _engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM public.dash_brain_versions "
            "WHERE brain_id = :id ORDER BY version DESC LIMIT :lim OFFSET :off"
        ), {"id": brain_id, "lim": limit, "off": offset}).fetchall()
        total = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_brain_versions WHERE brain_id = :id"
        ), {"id": brain_id}).scalar() or 0

    return {
        "versions": [_row_to_dict(r) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{brain_id}/version/{version}")
def get_version(brain_id: int, version: int, request: Request):
    """Return a single version snapshot."""
    _get_user(request)
    with _engine.connect() as conn:
        row = conn.execute(text(
            f"SELECT {_SELECT_COLS} FROM public.dash_brain_versions "
            "WHERE brain_id = :id AND version = :v"
        ), {"id": brain_id, "v": version}).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    return _row_to_dict(row)


@router.get("/changes/recent")
def recent_changes(
    request: Request,
    days: int = 14,
    category: str | None = None,
    user_id: int | None = None,
):
    """Global feed of recent brain changes for audit page."""
    _get_user(request)
    days = max(1, min(int(days or 14), 365))

    q = (
        f"SELECT {_SELECT_COLS} FROM public.dash_brain_versions "
        "WHERE created_at >= now() - (:days || ' days')::interval"
    )
    params: dict[str, Any] = {"days": str(days)}
    if category:
        q += " AND category = :cat"
        params["cat"] = category
    if user_id is not None:
        q += " AND changed_by = :uid"
        params["uid"] = int(user_id)
    q += " ORDER BY created_at DESC LIMIT 500"

    with _engine.connect() as conn:
        rows = conn.execute(text(q), params).fetchall()
    return {"changes": [_row_to_dict(r) for r in rows], "days": days}


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

@router.post("/{brain_id}/rollback/{version}")
def rollback_to_version(brain_id: int, version: int, request: Request):
    """Restore a brain entry to the named version.

    - Super-admin OR original creator (matching `changed_by` on v1) is allowed.
    - Idempotent: rolling back to current state is a no-op.
    - Implementation: read snapshot → UPDATE main row → snapshot new version
      with change_type='rollback'. All in one transaction.
    """
    user = _get_user(request)
    uid = _user_id(user)

    with _engine.begin() as conn:
        snap = conn.execute(text(
            "SELECT category, name, definition, project_slug, user_id, metadata "
            "FROM public.dash_brain_versions "
            "WHERE brain_id = :id AND version = :v"
        ), {"id": brain_id, "v": version}).fetchone()
        if not snap:
            raise HTTPException(404, "Version not found")

        current = _read_current_row(conn, brain_id)
        if not current:
            raise HTTPException(404, "Brain entry not found (deleted?). Cannot rollback in-place.")

        # Authorization: super admin OR original author of v1.
        if not _is_super_admin(user):
            v1 = conn.execute(text(
                "SELECT changed_by FROM public.dash_brain_versions "
                "WHERE brain_id = :id AND version = 1"
            ), {"id": brain_id}).fetchone()
            original_author = v1[0] if v1 else None
            if original_author is None or uid is None or int(original_author) != int(uid):
                raise HTTPException(403, "Super admin or original author only")

        snap_meta = snap[5] if isinstance(snap[5], dict) else (json.loads(snap[5]) if snap[5] else {})

        # Idempotent check — if main row already matches snapshot, return current version.
        cur_meta = current.get("metadata") or {}
        if (
            current.get("category") == snap[0]
            and current.get("name") == snap[1]
            and current.get("definition") == snap[2]
            and current.get("project_slug") == snap[3]
            and (current.get("user_id") or None) == (snap[4] or None)
            and json.dumps(cur_meta, sort_keys=True) == json.dumps(snap_meta, sort_keys=True)
        ):
            latest = conn.execute(text(
                "SELECT COALESCE(MAX(version), 0) FROM public.dash_brain_versions WHERE brain_id = :id"
            ), {"id": brain_id}).scalar() or 0
            return {"status": "noop", "version": int(latest), "reason": "already at requested state"}

        # Restore main row.
        conn.execute(text("""
            UPDATE public.dash_company_brain
               SET category = :category,
                   name = :name,
                   definition = :definition,
                   project_slug = :project_slug,
                   user_id = :user_id,
                   metadata = CAST(:metadata AS JSONB),
                   updated_at = now()
             WHERE id = :id
        """), {
            "id": brain_id,
            "category": snap[0],
            "name": snap[1],
            "definition": snap[2],
            "project_slug": snap[3],
            "user_id": snap[4],
            "metadata": json.dumps(snap_meta),
        })

        new_version = snapshot_version(
            conn,
            brain_id=brain_id,
            change_type="rollback",
            changed_by=uid,
            change_reason=f"reverted to v{version}",
        )

    return {"status": "rolled_back", "restored_from_version": version, "new_version": new_version}
