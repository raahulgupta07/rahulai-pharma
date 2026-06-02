from __future__ import annotations

import json
import threading
import time
from typing import Optional

from sqlalchemy import text

from .schema import VisibilityPolicy

_CACHE_TTL_S = 300
_cache: dict[str, tuple[float, VisibilityPolicy]] = {}
_cache_lock = threading.Lock()


def _engine():
    # Reuse auth's NullPool engine to avoid double-pooling.
    from app.auth import _engine as eng
    return eng


def _ensure_visibility_policy_table() -> None:
    eng = _engine()
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_visibility_policy (
              project_slug TEXT PRIMARY KEY,
              version INT DEFAULT 1,
              policy_json JSONB NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT now(),
              updated_by INT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_visibility_policy_history (
              id BIGSERIAL PRIMARY KEY,
              project_slug TEXT NOT NULL,
              version INT,
              policy_json JSONB,
              changed_at TIMESTAMPTZ DEFAULT now(),
              changed_by INT
            )
        """))
        # Phase 4 — audit diff column (additive, idempotent).
        conn.execute(text("""
            ALTER TABLE public.dash_visibility_policy_history
              ADD COLUMN IF NOT EXISTS changed_fields JSONB DEFAULT '{}'::jsonb
        """))
        # Phase 7A — cross-store read audit log.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_visibility_read_log (
              id BIGSERIAL PRIMARY KEY,
              project_slug TEXT NOT NULL,
              viewer_user_id INT,
              viewer_scope_id TEXT,
              target_scope_id TEXT,
              intent TEXT NOT NULL,
              policy_version INT,
              sql_excerpt TEXT,
              fields_downgraded TEXT[],
              row_count INT,
              created_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_read_log_target
              ON public.dash_visibility_read_log(project_slug, target_scope_id, created_at DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_read_log_viewer
              ON public.dash_visibility_read_log(project_slug, viewer_user_id, created_at DESC)
        """))
        # Sign-off workflow — drafts awaiting 2-admin approval before publish.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_visibility_policy_drafts (
              id BIGSERIAL PRIMARY KEY,
              project_slug TEXT NOT NULL,
              policy_json JSONB NOT NULL,
              status TEXT DEFAULT 'draft',
              created_by INT,
              created_at TIMESTAMPTZ DEFAULT now(),
              submitted_at TIMESTAMPTZ,
              approvals JSONB DEFAULT '[]'::jsonb,
              required_approvals INT DEFAULT 2,
              comment TEXT
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_drafts_status
              ON public.dash_visibility_policy_drafts(project_slug, status, created_at DESC)
        """))
        conn.commit()
    _ensure_role_tables()


def _ensure_role_tables() -> None:
    eng = _engine()
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_visibility_roles (
              id SERIAL PRIMARY KEY,
              project_slug TEXT NOT NULL,
              role_name TEXT NOT NULL,
              allowed_intents TEXT[] DEFAULT ARRAY['private']::TEXT[],
              description TEXT DEFAULT '',
              created_at TIMESTAMPTZ DEFAULT now(),
              UNIQUE(project_slug, role_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_user_roles (
              id SERIAL PRIMARY KEY,
              user_id INT NOT NULL REFERENCES public.dash_users(id) ON DELETE CASCADE,
              project_slug TEXT NOT NULL,
              role_name TEXT NOT NULL,
              assigned_at TIMESTAMPTZ DEFAULT now(),
              UNIQUE(user_id, project_slug, role_name)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_user_roles_lookup
              ON public.dash_user_roles(user_id, project_slug)
        """))
        conn.commit()


def diff_policies(old: Optional[VisibilityPolicy], new: VisibilityPolicy) -> dict:
    """Compute per-audience field diff for audit. Shape: {added,removed,modified} → {audience: [col]}."""
    audiences = ("private", "network", "public")
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    modified: dict[str, list[str]] = {}
    for aud in audiences:
        old_fields = (getattr(old, aud).fields if old else {}) or {}
        new_fields = getattr(new, aud).fields or {}
        old_keys = set(old_fields.keys())
        new_keys = set(new_fields.keys())
        a = sorted(new_keys - old_keys)
        r = sorted(old_keys - new_keys)
        m = []
        for k in sorted(old_keys & new_keys):
            o = old_fields[k]
            n = new_fields[k]
            o_mode = getattr(o, "mode", None) if not isinstance(o, dict) else o.get("mode")
            n_mode = getattr(n, "mode", None) if not isinstance(n, dict) else n.get("mode")
            if o_mode != n_mode:
                m.append(k)
        if a: added[aud] = a
        if r: removed[aud] = r
        if m: modified[aud] = m
    return {"added": added, "removed": removed, "modified": modified}


def load_policy(project_slug: str) -> Optional[VisibilityPolicy]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(project_slug)
        if hit and (now - hit[0]) < _CACHE_TTL_S:
            return hit[1]
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT policy_json, version FROM public.dash_visibility_policy WHERE project_slug=:s"),
                {"s": project_slug},
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    try:
        pol = VisibilityPolicy.model_validate(raw or {})
    except Exception:
        return None
    if row[1] is not None:
        pol.version = int(row[1])
    with _cache_lock:
        _cache[project_slug] = (now, pol)
    return pol


def save_policy(project_slug: str, policy: VisibilityPolicy, user_id: int | None = None) -> int:
    eng = _engine()
    # Diff vs current persisted policy for audit (best-effort; missing → empty).
    try:
        prior = load_policy(project_slug)
    except Exception:
        prior = None
    changed_fields = diff_policies(prior, policy)
    payload = policy.model_dump()
    with eng.connect() as conn:
        cur = conn.execute(
            text("SELECT version FROM public.dash_visibility_policy WHERE project_slug=:s"),
            {"s": project_slug},
        ).fetchone()
        new_version = (int(cur[0]) + 1) if cur else 1
        payload["version"] = new_version
        payload_json = json.dumps(payload)
        changed_json = json.dumps(changed_fields)
        conn.execute(text("""
            INSERT INTO public.dash_visibility_policy (project_slug, version, policy_json, updated_at, updated_by)
            VALUES (:s, :v, CAST(:p AS JSONB), now(), :u)
            ON CONFLICT (project_slug) DO UPDATE
              SET version=EXCLUDED.version,
                  policy_json=EXCLUDED.policy_json,
                  updated_at=now(),
                  updated_by=EXCLUDED.updated_by
        """), {"s": project_slug, "v": new_version, "p": payload_json, "u": user_id})
        conn.execute(text("""
            INSERT INTO public.dash_visibility_policy_history (project_slug, version, policy_json, changed_by, changed_fields)
            VALUES (:s, :v, CAST(:p AS JSONB), :u, CAST(:c AS JSONB))
        """), {"s": project_slug, "v": new_version, "p": payload_json, "u": user_id, "c": changed_json})
        conn.commit()
    invalidate_cache(project_slug)
    return new_version


def invalidate_cache(project_slug: str) -> None:
    with _cache_lock:
        _cache.pop(project_slug, None)
