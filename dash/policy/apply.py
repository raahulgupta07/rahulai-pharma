from __future__ import annotations

from .engine import PolicyEngine
from .loader import load_policy

_engine = PolicyEngine()


def apply_policy(sql: str, project_slug: str, intent: str) -> tuple[str, list[str]]:
    pol = load_policy(project_slug)
    if pol is None:
        return sql, []
    return _engine.apply(sql, pol, intent)
