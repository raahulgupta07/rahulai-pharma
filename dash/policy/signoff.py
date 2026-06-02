"""Sign-off workflow for visibility policy publication.

2-admin approval gate. Drafts move draft → pending → approved/rejected.
Once approvals threshold met (and no rejects), auto-publishes via save_policy().
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from .loader import _engine, save_policy, _ensure_visibility_policy_table
from .schema import VisibilityPolicy


def _row_to_dict(row) -> dict:
    if row is None:
        return None  # type: ignore[return-value]
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    # parse JSON fields if stringified
    for k in ("policy_json", "approvals"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                d[k] = None if k == "policy_json" else []
    # stringify timestamps
    for k in ("created_at", "submitted_at"):
        if d.get(k) is not None and not isinstance(d[k], str):
            try:
                d[k] = d[k].isoformat()
            except Exception:
                d[k] = str(d[k])
    return d


def create_draft(project_slug: str, policy: dict, user_id: int, comment: str = "") -> Optional[int]:
    """Insert draft. Validates via Pydantic. Returns draft_id or None on failure."""
    try:
        VisibilityPolicy(**(policy or {}))
    except Exception:
        return None
    try:
        _ensure_visibility_policy_table()
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text("""
                INSERT INTO public.dash_visibility_policy_drafts
                  (project_slug, policy_json, status, created_by, comment)
                VALUES (:s, CAST(:p AS JSONB), 'draft', :u, :c)
                RETURNING id
            """), {"s": project_slug, "p": json.dumps(policy), "u": user_id, "c": comment or ""}).fetchone()
            conn.commit()
        return int(row[0]) if row else None
    except Exception:
        return None


def submit_draft(draft_id: int, user_id: int) -> Optional[dict]:
    """Move draft → pending. Returns updated draft."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text("""
                UPDATE public.dash_visibility_policy_drafts
                   SET status='pending', submitted_at=now()
                 WHERE id=:id AND status IN ('draft','rejected')
            """), {"id": draft_id})
            conn.commit()
        return get_draft(draft_id)
    except Exception:
        return None


def _append_approval(draft_id: int, approver_user_id: int, decision: str, comment: str) -> Optional[dict]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(text("""
            SELECT id, project_slug, policy_json, status, created_by,
                   approvals, required_approvals
              FROM public.dash_visibility_policy_drafts
             WHERE id=:id
        """), {"id": draft_id}).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        if d.get("created_by") == approver_user_id:
            return {"_error": "self-approval not allowed"}
        if d.get("status") not in ("pending",):
            return {"_error": f"cannot {decision}: status is {d.get('status')}"}
        approvals = d.get("approvals") or []
        if any((a.get("approver_user_id") == approver_user_id) for a in approvals):
            return {"_error": "already recorded a decision"}
        approvals.append({
            "approver_user_id": approver_user_id,
            "decision": decision,
            "comment": comment or "",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        conn.execute(text("""
            UPDATE public.dash_visibility_policy_drafts
               SET approvals = CAST(:a AS JSONB)
             WHERE id=:id
        """), {"a": json.dumps(approvals), "id": draft_id})
        conn.commit()
        d["approvals"] = approvals
        return d


def approve_draft(draft_id: int, approver_user_id: int, comment: str = "") -> Optional[dict]:
    """Append approval. Auto-publishes when count >= required_approvals and no rejects."""
    try:
        d = _append_approval(draft_id, approver_user_id, "approve", comment)
        if not d or d.get("_error"):
            return d
        approvals = d.get("approvals") or []
        approves = [a for a in approvals if a.get("decision") == "approve"]
        rejects = [a for a in approvals if a.get("decision") == "reject"]
        required = int(d.get("required_approvals") or 2)
        if not rejects and len(approves) >= required:
            try:
                pol = VisibilityPolicy(**(d.get("policy_json") or {}))
                version = save_policy(d["project_slug"], pol, user_id=approver_user_id)
                eng = _engine()
                with eng.connect() as conn:
                    conn.execute(text("""
                        UPDATE public.dash_visibility_policy_drafts
                           SET status='published'
                         WHERE id=:id
                    """), {"id": draft_id})
                    conn.commit()
                d["status"] = "published"
                d["published_version"] = version
            except Exception as e:
                d["_publish_error"] = str(e)
        return get_draft(draft_id) or d
    except Exception:
        return None


def reject_draft(draft_id: int, approver_user_id: int, comment: str = "") -> Optional[dict]:
    """Append rejection and set status='rejected'."""
    try:
        d = _append_approval(draft_id, approver_user_id, "reject", comment)
        if not d or d.get("_error"):
            return d
        eng = _engine()
        with eng.connect() as conn:
            conn.execute(text("""
                UPDATE public.dash_visibility_policy_drafts
                   SET status='rejected'
                 WHERE id=:id
            """), {"id": draft_id})
            conn.commit()
        return get_draft(draft_id) or d
    except Exception:
        return None


def list_drafts(project_slug: str, status: Optional[str] = None) -> list[dict]:
    try:
        eng = _engine()
        with eng.connect() as conn:
            if status:
                rows = conn.execute(text("""
                    SELECT id, project_slug, policy_json, status, created_by, created_at,
                           submitted_at, approvals, required_approvals, comment
                      FROM public.dash_visibility_policy_drafts
                     WHERE project_slug=:s AND status=:st
                  ORDER BY created_at DESC
                     LIMIT 50
                """), {"s": project_slug, "st": status}).fetchall()
            else:
                rows = conn.execute(text("""
                    SELECT id, project_slug, policy_json, status, created_by, created_at,
                           submitted_at, approvals, required_approvals, comment
                      FROM public.dash_visibility_policy_drafts
                     WHERE project_slug=:s
                  ORDER BY created_at DESC
                     LIMIT 50
                """), {"s": project_slug}).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def get_draft(draft_id: int) -> Optional[dict]:
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT id, project_slug, policy_json, status, created_by, created_at,
                       submitted_at, approvals, required_approvals, comment
                  FROM public.dash_visibility_policy_drafts
                 WHERE id=:id
            """), {"id": draft_id}).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        return None
