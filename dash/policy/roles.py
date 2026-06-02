"""Phase 4 — visibility roles. Gates which query_intent a user may use per project."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text


def _engine():
    # Reuse auth's NullPool engine (same pattern as loader.py).
    from app.auth import _engine as eng
    return eng


def get_user_role(user_id: int, project_slug: str) -> Optional[str]:
    try:
        with _engine().connect() as conn:
            row = conn.execute(text("""
                SELECT role_name FROM public.dash_user_roles
                WHERE user_id=:u AND project_slug=:s
                ORDER BY assigned_at ASC LIMIT 1
            """), {"u": user_id, "s": project_slug}).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def get_role_intents(project_slug: str, role_name: Optional[str]) -> list[str]:
    if not role_name:
        return ["private"]
    try:
        with _engine().connect() as conn:
            row = conn.execute(text("""
                SELECT allowed_intents FROM public.dash_visibility_roles
                WHERE project_slug=:s AND role_name=:r
            """), {"s": project_slug, "r": role_name}).fetchone()
        if not row or not row[0]:
            return ["private"]
        return list(row[0])
    except Exception:
        return ["private"]


def list_roles(project_slug: str) -> list[dict]:
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text("""
                SELECT role_name, allowed_intents, description
                FROM public.dash_visibility_roles
                WHERE project_slug=:s
                ORDER BY role_name
            """), {"s": project_slug}).fetchall()
        return [
            {"role_name": r[0], "allowed_intents": list(r[1] or []), "description": r[2] or ""}
            for r in rows
        ]
    except Exception:
        return []


def upsert_role(project_slug: str, role_name: str,
                allowed_intents: list[str], description: str = "") -> None:
    intents = [i for i in (allowed_intents or []) if i in ("private", "network", "public")]
    if not intents:
        intents = ["private"]
    with _engine().connect() as conn:
        conn.execute(text("""
            INSERT INTO public.dash_visibility_roles (project_slug, role_name, allowed_intents, description)
            VALUES (:s, :r, :i, :d)
            ON CONFLICT (project_slug, role_name) DO UPDATE
              SET allowed_intents=EXCLUDED.allowed_intents,
                  description=EXCLUDED.description
        """), {"s": project_slug, "r": role_name, "i": intents, "d": description or ""})
        conn.commit()


def delete_role(project_slug: str, role_name: str) -> None:
    with _engine().connect() as conn:
        conn.execute(text("""
            DELETE FROM public.dash_visibility_roles
            WHERE project_slug=:s AND role_name=:r
        """), {"s": project_slug, "r": role_name})
        conn.commit()


def assign_user_role(user_id: int, project_slug: str, role_name: str) -> None:
    with _engine().connect() as conn:
        conn.execute(text("""
            INSERT INTO public.dash_user_roles (user_id, project_slug, role_name)
            VALUES (:u, :s, :r)
            ON CONFLICT (user_id, project_slug, role_name) DO NOTHING
        """), {"u": user_id, "s": project_slug, "r": role_name})
        conn.commit()


def unassign_user_role(user_id: int, project_slug: str, role_name: str) -> None:
    with _engine().connect() as conn:
        conn.execute(text("""
            DELETE FROM public.dash_user_roles
            WHERE user_id=:u AND project_slug=:s AND role_name=:r
        """), {"u": user_id, "s": project_slug, "r": role_name})
        conn.commit()


def list_user_roles(project_slug: str) -> list[dict]:
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text("""
                SELECT ur.user_id, u.username, ur.role_name
                FROM public.dash_user_roles ur
                LEFT JOIN public.dash_users u ON u.id = ur.user_id
                WHERE ur.project_slug=:s
                ORDER BY u.username NULLS LAST, ur.role_name
            """), {"s": project_slug}).fetchall()
        return [{"user_id": r[0], "username": r[1] or "", "role_name": r[2]} for r in rows]
    except Exception:
        return []


def replace_roles(project_slug: str, roles: list[dict]) -> None:
    """Transactional replace: delete all, then re-insert."""
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM public.dash_visibility_roles WHERE project_slug=:s"),
                     {"s": project_slug})
        for r in roles or []:
            name = (r.get("role_name") or "").strip()
            if not name:
                continue
            intents = [i for i in (r.get("allowed_intents") or []) if i in ("private", "network", "public")]
            if not intents:
                intents = ["private"]
            conn.execute(text("""
                INSERT INTO public.dash_visibility_roles (project_slug, role_name, allowed_intents, description)
                VALUES (:s, :r, :i, :d)
            """), {"s": project_slug, "r": name, "i": intents, "d": r.get("description") or ""})


def replace_user_roles(project_slug: str, assignments: list[dict]) -> None:
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM public.dash_user_roles WHERE project_slug=:s"),
                     {"s": project_slug})
        for a in assignments or []:
            uid = a.get("user_id")
            role = (a.get("role_name") or "").strip()
            if not uid or not role:
                continue
            conn.execute(text("""
                INSERT INTO public.dash_user_roles (user_id, project_slug, role_name)
                VALUES (:u, :s, :r)
                ON CONFLICT DO NOTHING
            """), {"u": int(uid), "s": project_slug, "r": role})
