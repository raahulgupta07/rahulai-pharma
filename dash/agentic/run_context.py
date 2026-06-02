"""Dash-OS Phase 7 — RunContext: consolidated ContextVar surface.

Replaces the 12 scattered ContextVars across hits/hooks/RLS/embed/audit
with one dataclass. Preserves backwards-compat by re-exporting the
individual ContextVars from dash.agentic.hooks.

Usage:
    from dash.agentic.run_context import RunContext, set_context, get_context

    with set_context(RunContext(project_slug="my-proj", user_id=42, ...)):
        # all downstream code sees consistent context
        ...
"""
from __future__ import annotations

import contextlib
import logging
import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    run_id: str = field(default_factory=lambda: "run_" + secrets.token_hex(8))
    project_slug: Optional[str] = None
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    agent_name: Optional[str] = None
    scope_id: Optional[str] = None
    query_intent: Optional[str] = None        # 'private' | 'network' | 'public'
    user_attrs: Optional[Dict[str, Any]] = None
    external_user: Optional[str] = None
    embed_id: Optional[str] = None
    trigger_kind: str = "chat"                # 'chat' | 'schedule' | 'workflow' | 'channel' | 'eval'

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "project_slug": self.project_slug,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "scope_id": self.scope_id,
            "query_intent": self.query_intent,
            "user_attrs": self.user_attrs,
            "external_user": self.external_user,
            "embed_id": self.embed_id,
            "trigger_kind": self.trigger_kind,
        }


_current: ContextVar[Optional[RunContext]] = ContextVar("dash_run_context", default=None)


def get_context() -> Optional[RunContext]:
    return _current.get()


def get_or_create() -> RunContext:
    rc = _current.get()
    if rc is None:
        rc = RunContext()
        _current.set(rc)
    return rc


@contextlib.contextmanager
def set_context(rc: RunContext) -> Iterator[RunContext]:
    """Set + propagate to legacy ContextVars in dash.agentic.hooks for
    backwards-compat with code that still reads them individually."""
    token = _current.set(rc)
    legacy_tokens = []
    try:
        try:
            from dash.agentic.hooks import (
                current_run_id, current_project_slug, current_user_id,
                current_agent_name,
            )
            legacy_tokens.append((current_run_id, current_run_id.set(rc.run_id)))
            legacy_tokens.append((current_project_slug, current_project_slug.set(rc.project_slug)))
            legacy_tokens.append((current_user_id, current_user_id.set(rc.user_id)))
            legacy_tokens.append((current_agent_name, current_agent_name.set(rc.agent_name)))
        except Exception:
            pass
        try:
            from dash.agentic.hitl import current_run_id as hitl_run_id
            legacy_tokens.append((hitl_run_id, hitl_run_id.set(rc.run_id)))
        except Exception:
            pass
        yield rc
    finally:
        for cvar, tk in legacy_tokens:
            try:
                cvar.reset(tk)
            except Exception:
                pass
        _current.reset(token)


def audit(rc: Optional[RunContext] = None) -> None:
    """Best-effort: persist this context's snapshot to dash_run_context_audit."""
    rc = rc or get_context()
    if rc is None:
        return
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            eng = get_sql_engine()
        except Exception:
            return
    if eng is None:
        return
    try:
        import json as _json
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_run_context_audit
                      (run_id, project_slug, user_id, agent_name, scope_id,
                       query_intent, user_attrs, external_user, trigger_kind)
                    VALUES (:rid, :ps, :uid, :ag, :sc,
                            :qi, CAST(:ua AS jsonb), :eu, :tk)
                    """
                ),
                {
                    "rid": rc.run_id, "ps": rc.project_slug, "uid": rc.user_id,
                    "ag": rc.agent_name, "sc": rc.scope_id,
                    "qi": rc.query_intent,
                    "ua": _json.dumps(rc.user_attrs) if rc.user_attrs else None,
                    "eu": rc.external_user, "tk": rc.trigger_kind,
                },
            )
    except Exception as e:
        logger.warning("run_context audit failed: %s", e)
