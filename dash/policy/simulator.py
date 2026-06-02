"""Phase 5A — visibility policy preview simulator + draft validation.

simulate(): preview rewritten SQL + sample rows for a (user, scope, intent) tuple,
optionally with a draft policy not yet saved.

validate_policy(): synthetic matrix run across roles/users/scopes; flags failures
so admin can decide to force-save or fix.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy import text

from .schema import VisibilityPolicy
from .engine import PolicyEngine
from .loader import load_policy
from .roles import (
    get_user_role,
    get_role_intents,
    list_roles,
    list_user_roles,
)

logger = logging.getLogger(__name__)

_ROW_CAP = 50
_MATRIX_CAP = 12
_INTENT_RANK = {"private": 0, "network": 1, "public": 2}


def _engine():
    from app.auth import _engine as eng
    return eng


def _cap_intent(requested: str, allowed: list[str]) -> tuple[str, bool]:
    """Cap requested intent to most permissive in allowed set. Returns (intent, capped)."""
    requested = (requested or "private").lower()
    allowed = [a for a in (allowed or []) if a in _INTENT_RANK]
    if not allowed:
        return "private", requested != "private"
    if requested in allowed:
        return requested, False
    # pick most permissive available <= requested rank, else lowest
    req_rank = _INTENT_RANK.get(requested, 0)
    candidates = [a for a in allowed if _INTENT_RANK[a] <= req_rank]
    if candidates:
        chosen = max(candidates, key=lambda a: _INTENT_RANK[a])
    else:
        chosen = min(allowed, key=lambda a: _INTENT_RANK[a])
    return chosen, True


def _scope_user_attrs(user_id: int, project_slug: str, scope_id: str) -> dict:
    """Resolve user_attrs from dash_user_scopes row."""
    if not scope_id:
        return {}
    try:
        with _engine().connect() as conn:
            row = conn.execute(text("""
                SELECT scope_id, scope_label, role
                FROM public.dash_user_scopes
                WHERE user_id=:u AND project_slug=:s AND scope_id=:sid
                LIMIT 1
            """), {"u": user_id, "s": project_slug, "sid": scope_id}).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    attrs: dict[str, Any] = {
        "scope_id": row[0],
        "scope_label": row[1],
        "role": row[2],
    }
    # Common shorthand keys used by RLS filters
    sid = row[0] or ""
    attrs["store_id"] = sid
    attrs["region"] = sid
    return attrs


def _project_schema(project_slug: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", (project_slug or "").lower())[:63]


def _first_table(project_slug: str) -> Optional[str]:
    """Discover any table in the project schema via information_schema."""
    schema = _project_schema(project_slug)
    if not schema:
        return None
    try:
        with _engine().connect() as conn:
            row = conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=:s AND table_type='BASE TABLE'
                ORDER BY table_name LIMIT 1
            """), {"s": schema}).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def simulate(
    project_slug: str,
    user_id: int,
    scope_id: str,
    intent: str,
    sql: str,
    draft_policy: Optional[VisibilityPolicy | dict] = None,
) -> dict:
    """Preview a query: apply RLS + visibility policy and return capped rows."""
    out: dict[str, Any] = {
        "rewritten_sql": sql,
        "rows": [],
        "cols": [],
        "downgraded_fields": [],
        "allowed_intent": intent,
        "capped_intent": False,
        "error": None,
    }
    try:
        # a) role → allowed intents → cap
        role = get_user_role(user_id, project_slug)
        allowed = get_role_intents(project_slug, role)
        allowed_intent, capped = _cap_intent(intent, allowed)
        out["allowed_intent"] = allowed_intent
        out["capped_intent"] = capped

        # b) user_attrs from scope
        user_attrs = _scope_user_attrs(user_id, project_slug, scope_id)

        # c) draft or saved policy
        if draft_policy is not None:
            if isinstance(draft_policy, dict):
                policy = VisibilityPolicy(**draft_policy)
            else:
                policy = draft_policy
        else:
            policy = load_policy(project_slug)

        # d) RLS rewrite then policy apply
        rls_sql = sql
        try:
            from dash.rls.rewriter import rewrite as _rls_rewrite
            rls_sql = _rls_rewrite(sql, project_slug, user_attrs)
        except PermissionError as pe:
            out["error"] = f"RLS denied: {pe}"
            return out
        except Exception as e:
            logger.warning(f"simulator RLS rewrite failed: {e}")

        rewritten = rls_sql
        downgraded: list[str] = []
        if policy is not None:
            try:
                rewritten, downgraded = PolicyEngine().apply(rls_sql, policy, allowed_intent)
            except Exception as e:
                logger.warning(f"simulator policy apply failed: {e}")
                rewritten = rls_sql

        out["rewritten_sql"] = rewritten
        out["downgraded_fields"] = list(downgraded or [])

        # e/f) execute against project RO engine; bypass listener via private intent + empty attrs
        try:
            from db import get_project_readonly_engine
            from dash.tools.skill_refinery import set_request_context
        except Exception as e:
            out["error"] = f"engine unavailable: {e}"
            return out

        eng = get_project_readonly_engine(project_slug)
        # Make listener a no-op by setting query_intent=private + empty attrs.
        set_request_context(query_intent="private", user_attrs={})
        try:
            with eng.connect() as conn:
                res = conn.execute(text(rewritten))
                cols = list(res.keys())
                rows = []
                for i, r in enumerate(res):
                    if i >= _ROW_CAP:
                        break
                    rows.append({c: _coerce(v) for c, v in zip(cols, r)})
                out["cols"] = cols
                out["rows"] = rows
        except Exception as e:
            out["error"] = f"execution failed: {e}"
            return out
    except Exception as e:
        out["error"] = f"simulate failed: {e}"
    return out


def _coerce(v: Any) -> Any:
    """Make values JSON-serializable best-effort."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return str(v)
    except Exception:
        return None


def validate_policy(project_slug: str, draft_policy: VisibilityPolicy | dict) -> dict:
    """Synthetic matrix: run a smoke SQL across role × user × scope.

    Returns {ok, failures:[...], warnings:[...]}.
    """
    failures: list[dict] = []
    warnings: list[str] = []

    table = _first_table(project_slug)
    if not table:
        warnings.append("no tables discovered via information_schema; matrix skipped")
        return {"ok": True, "failures": [], "warnings": warnings}

    schema = _project_schema(project_slug)
    smoke_sql = f'SELECT * FROM "{schema}"."{table}" LIMIT 5'

    roles = list_roles(project_slug) or []
    user_assignments = list_user_roles(project_slug) or []
    if not roles or not user_assignments:
        warnings.append("no roles or user assignments; matrix skipped")
        return {"ok": True, "failures": [], "warnings": warnings}

    by_role: dict[str, list[dict]] = {}
    for ua in user_assignments:
        by_role.setdefault(ua.get("role_name") or "", []).append(ua)

    cells = 0
    for r in roles:
        role_name = r.get("role_name")
        users = by_role.get(role_name) or []
        if not users:
            continue
        intents = r.get("allowed_intents") or ["private"]
        for ua in users:
            uid = ua.get("user_id")
            if not uid:
                continue
            scopes = _list_user_scopes(uid, project_slug)
            if not scopes:
                scopes = [""]
            for scope_id in scopes:
                for intent in intents:
                    if cells >= _MATRIX_CAP:
                        warnings.append(f"matrix capped at {_MATRIX_CAP} cells")
                        return {"ok": not failures, "failures": failures, "warnings": warnings}
                    cells += 1
                    res = simulate(project_slug, uid, scope_id, intent, smoke_sql, draft_policy)
                    err = res.get("error")
                    if err:
                        failures.append({
                            "role": role_name,
                            "scope_id": scope_id,
                            "intent": intent,
                            "sql": smoke_sql,
                            "error": err,
                        })
                        continue
                    # heuristic: only fail on private intent if zero rows (network/public may legitimately drop fields)
                    if intent == "private" and not res.get("rows"):
                        failures.append({
                            "role": role_name,
                            "scope_id": scope_id,
                            "intent": intent,
                            "sql": smoke_sql,
                            "error": "no rows visible at private intent",
                        })

    return {"ok": not failures, "failures": failures, "warnings": warnings}


def _list_user_scopes(user_id: int, project_slug: str) -> list[str]:
    try:
        with _engine().connect() as conn:
            rows = conn.execute(text("""
                SELECT scope_id FROM public.dash_user_scopes
                WHERE user_id=:u AND project_slug=:s
                ORDER BY is_default DESC, scope_id ASC
            """), {"u": user_id, "s": project_slug}).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
