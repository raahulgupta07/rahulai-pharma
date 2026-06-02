"""Demo callables wrapped with the @approval decorator.

Named ``_approval_examples.py`` to avoid colliding with parallel agent A who
may claim ``_examples.py`` for HITL demos.

These functions are intentionally side-effect-free stubs — they exist only
so tests can import them and the executor has something to invoke.
"""
from __future__ import annotations

from typing import Any

from .approval import ApprovalContext, approval


@approval("brain_delete", min_approvers=1, allowed_roles=["admin", "super_admin"])
def delete_brain_entry(ctx: ApprovalContext, entry_id: int) -> dict:
    """Pretend to delete a Brain entry.

    Real implementation would call ``app.brain.delete_entry(entry_id, ctx.requested_by)``
    inside ``engine.begin()``.
    """
    return {
        "ok": True,
        "deleted_entry_id": int(entry_id),
        "approved_request": ctx.request_id,
        "actor": ctx.requested_by,
    }


@approval("rls_apply", min_approvers=2, allowed_roles=["admin"])
def apply_rls_policy(ctx: ApprovalContext, slug: str, policy_json: dict) -> dict:
    """Pretend to apply an RLS policy.

    Real implementation would call ``dash.rls.pg_setup.apply(slug, policy_json)``.
    """
    return {
        "ok": True,
        "slug": slug,
        "policy_keys": sorted(list((policy_json or {}).keys())),
        "approved_request": ctx.request_id,
    }


__all__ = ["delete_brain_entry", "apply_rls_policy"]
