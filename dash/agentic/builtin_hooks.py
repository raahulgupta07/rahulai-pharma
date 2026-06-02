"""Built-in pre/post hook examples.

These are NOT auto-registered. Callers opt in via :func:`register_builtins`.
Each hook is also exported standalone so call sites can mix-and-match.

Hooks
-----
pre_cost_cap         block when ``dash.learning.cost_guard.get_status`` says
                     the project's daily cap is exceeded.
pre_pii_redact       redact SSN / email / phone from string args. Never
                     blocks — only sanitizes (mutate).
pre_inject_rls_ctx   skeleton for Track 4: stamp ``_rls_user_attrs`` into
                     kwargs from the current ContextVars.
post_text_guard      run ``dash.dashboards.agents.text_guard.sanitize_narrative``
                     on string results. Fail-soft on import error.
post_secret_leak     block + audit when result contains likely API key /
                     JWT material.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from dash.agentic.hooks import (
    HookContext,
    HookResult,
    current_project_slug,
    current_user_id,
    post_hook,
    pre_hook,
)

logger = logging.getLogger(__name__)


# ── pre: daily cost cap ─────────────────────────────────────────────────────
def pre_cost_cap(ctx: HookContext) -> HookResult:
    """Block when the project's daily cost cap is reached."""
    try:
        from dash.learning.cost_guard import get_status  # type: ignore
    except Exception:
        return HookResult(decision="pass")
    try:
        status = get_status(ctx.project_slug)
    except Exception as e:
        logger.debug("pre_cost_cap: get_status failed: %s", e)
        return HookResult(decision="pass")
    if getattr(status, "over_budget", False):
        return HookResult(
            decision="block",
            reason="daily cap reached",
            metadata={
                "cap_usd": getattr(status, "daily_cap_usd", None),
                "today_spend_usd": getattr(status, "today_spend_usd", None),
            },
        )
    return HookResult(decision="pass")


# ── pre: PII redaction ──────────────────────────────────────────────────────
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# 7-15 digit phone, optional +, optional separators (space, dash, dot, parens)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3,4}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}(?!\w)"
)


def _redact_str(s: str) -> str:
    s = _SSN_RE.sub("[REDACTED_SSN]", s)
    s = _EMAIL_RE.sub("[REDACTED_EMAIL]", s)
    s = _PHONE_RE.sub("[REDACTED_PHONE]", s)
    return s


def _walk_redact(v: Any) -> Any:
    if isinstance(v, str):
        return _redact_str(v)
    if isinstance(v, list):
        return [_walk_redact(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_walk_redact(x) for x in v)
    if isinstance(v, dict):
        return {k: _walk_redact(x) for k, x in v.items()}
    return v


def pre_pii_redact(ctx: HookContext) -> HookResult:
    """Sanitize strings inside args/kwargs. Never blocks."""
    new_args = [_walk_redact(a) for a in ctx.args]
    new_kwargs = {k: _walk_redact(v) for k, v in ctx.kwargs.items()}
    if new_args == ctx.args and new_kwargs == ctx.kwargs:
        return HookResult(decision="pass")
    return HookResult(
        decision="mutate",
        mutated_args=new_args,
        mutated_kwargs=new_kwargs,
        reason="pii redacted",
    )


# ── pre: inject RLS user attrs (skeleton for Track 4) ──────────────────────
def pre_inject_rls_context(ctx: HookContext) -> HookResult:
    """Stamp ``_rls_user_attrs`` into kwargs from ContextVars.

    Skeleton — Track 4 will fill the actual attribute resolver. For now we
    only attach what we know from the current request context.
    """
    if "_rls_user_attrs" in ctx.kwargs:
        return HookResult(decision="pass")
    attrs = {
        "user_id": current_user_id.get(),
        "project_slug": current_project_slug.get(),
    }
    new_kwargs = dict(ctx.kwargs)
    new_kwargs["_rls_user_attrs"] = attrs
    return HookResult(decision="mutate", mutated_kwargs=new_kwargs)


# ── post: text guard ────────────────────────────────────────────────────────
def post_text_guard(ctx: HookContext, result: Any) -> HookResult:
    """Sanitize narrative results via the existing text guard. Fail-soft."""
    if not isinstance(result, str):
        return HookResult(decision="pass")
    try:
        from dash.dashboards.agents.text_guard import sanitize_narrative  # type: ignore
    except Exception:
        return HookResult(decision="pass")
    try:
        intent = str(ctx.kwargs.get("intent") or "network")
        sanitized = sanitize_narrative(
            result, ctx.project_slug or "", intent
        )
    except Exception as e:
        logger.debug("post_text_guard: sanitize failed: %s", e)
        return HookResult(decision="pass")
    if sanitized == result:
        return HookResult(decision="pass")
    return HookResult(decision="mutate", mutated_result=sanitized)


# ── post: secret leak detector ─────────────────────────────────────────────
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bpk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bOPENROUTER_[A-Z0-9_]+\b"),
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"),  # JWT
]


def _scan_for_secret(v: Any) -> str | None:
    if isinstance(v, str):
        for p in _SECRET_PATTERNS:
            m = p.search(v)
            if m:
                return m.group(0)[:24]  # truncated for audit safety
        return None
    if isinstance(v, dict):
        for x in v.values():
            hit = _scan_for_secret(x)
            if hit:
                return hit
    if isinstance(v, (list, tuple)):
        for x in v:
            hit = _scan_for_secret(x)
            if hit:
                return hit
    return None


def post_secret_leak(ctx: HookContext, result: Any) -> HookResult:
    """Block when the result appears to contain API keys / JWTs."""
    hit = _scan_for_secret(result)
    if hit is None:
        return HookResult(decision="pass")
    return HookResult(
        decision="block",
        reason=f"secret pattern in result: {hit!r}",
        metadata={"sample": hit},
    )


# ── opt-in registration ────────────────────────────────────────────────────
def register_builtins() -> None:
    """Register all built-in hooks with sensible priorities."""
    pre_hook("pre_cost_cap", priority=10)(pre_cost_cap)
    pre_hook("pre_pii_redact", priority=20)(pre_pii_redact)
    pre_hook("pre_inject_rls_context", priority=30)(pre_inject_rls_context)
    post_hook("post_text_guard", priority=50)(post_text_guard)
    post_hook("post_secret_leak", priority=90)(post_secret_leak)


__all__ = [
    "pre_cost_cap", "pre_pii_redact", "pre_inject_rls_context",
    "post_text_guard", "post_secret_leak",
    "register_builtins",
]
