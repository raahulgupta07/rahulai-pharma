"""Pre/post hook framework for Dash agent tools.

Public API
----------
    @pre_hook(name, priority=50, applies_to=["*"|"glob_pattern"])
    def my_pre(ctx: HookContext) -> HookResult: ...

    @post_hook(name, priority=50, applies_to=["*"])
    def my_post(ctx: HookContext, result: Any) -> HookResult: ...

    wrapped = apply_hooks(fn)         # works for sync + async callables
    wrapped(*args, **kwargs)

Behavior
--------
* When the env flag ``EXPERIMENTAL_AGI`` is *not* "1", :func:`apply_hooks`
  returns the original callable unchanged — zero overhead, no DB writes,
  no behavior change.
* When enabled, all matching pre-hooks run in priority order (low first).
  A ``decision='block'`` short-circuits with a structured error dict.
  ``decision='mutate'`` replaces ``args`` / ``kwargs`` for the next hook
  and ultimately the wrapped function.
* Post-hooks run after the function returns and may mutate the result.
* Audit rows go to ``dash.dash_hook_audit`` via a fire-and-forget background
  buffer (batch flush every 5s, max 200 buffered) so the hot path never
  blocks on DB I/O.

Context vars (read-only here, set by chat endpoint elsewhere):
    current_run_id, current_project_slug, current_user_id, current_agent_name
"""
from __future__ import annotations

import asyncio
import contextvars
import fnmatch
import functools
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# ── Context vars ────────────────────────────────────────────────────────────
current_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "dash_hook_run_id", default=None
)
current_project_slug: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "dash_hook_project_slug", default=None
)
current_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "dash_hook_user_id", default=None
)
current_agent_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "dash_hook_agent_name", default=None
)


# ── Public dataclasses ──────────────────────────────────────────────────────
@dataclass
class HookContext:
    tool_name: str
    agent_name: Optional[str] = None
    project_slug: Optional[str] = None
    user_id: Optional[int] = None
    run_id: Optional[str] = None
    args: list = field(default_factory=list)        # mutable
    kwargs: dict = field(default_factory=dict)      # mutable


@dataclass
class HookResult:
    decision: str = "pass"  # 'pass' | 'block' | 'mutate'
    reason: Optional[str] = None
    mutated_args: Optional[list] = None
    mutated_kwargs: Optional[dict] = None
    mutated_result: Any = None
    metadata: Optional[dict] = None


@dataclass
class _Hook:
    name: str
    fn: Callable
    priority: int
    applies_to: tuple[str, ...]
    kind: str  # 'pre' | 'post'


# ── Registry ────────────────────────────────────────────────────────────────
_pre_hooks: list[_Hook] = []
_post_hooks: list[_Hook] = []
_registry_lock = threading.RLock()


def _flag_enabled() -> bool:
    return os.environ.get("EXPERIMENTAL_AGI") == "1"


def _matches(patterns: Iterable[str], tool_name: str) -> bool:
    for p in patterns:
        if p == "*" or fnmatch.fnmatchcase(tool_name, p):
            return True
    return False


def _register(kind: str, name: str, fn: Callable, priority: int, applies_to):
    if applies_to is None:
        applies_to = ("*",)
    elif isinstance(applies_to, str):
        applies_to = (applies_to,)
    else:
        applies_to = tuple(applies_to) or ("*",)
    h = _Hook(name=name, fn=fn, priority=priority, applies_to=applies_to, kind=kind)
    with _registry_lock:
        bucket = _pre_hooks if kind == "pre" else _post_hooks
        # de-dup by name (re-register replaces)
        bucket[:] = [x for x in bucket if x.name != name]
        bucket.append(h)
        bucket.sort(key=lambda x: x.priority)
    return fn


def pre_hook(name: str, priority: int = 50, applies_to=None):
    """Decorator: register a pre-tool hook."""
    def deco(fn: Callable) -> Callable:
        _register("pre", name, fn, priority, applies_to)
        return fn
    return deco


def post_hook(name: str, priority: int = 50, applies_to=None):
    """Decorator: register a post-tool hook."""
    def deco(fn: Callable) -> Callable:
        _register("post", name, fn, priority, applies_to)
        return fn
    return deco


def clear_hooks() -> None:
    """Test helper: drop all registered hooks."""
    with _registry_lock:
        _pre_hooks.clear()
        _post_hooks.clear()


def list_hooks() -> dict[str, list[str]]:
    with _registry_lock:
        return {
            "pre": [h.name for h in _pre_hooks],
            "post": [h.name for h in _post_hooks],
        }


# ── Audit buffer ────────────────────────────────────────────────────────────
_audit_buffer: list[dict] = []
_audit_lock = threading.Lock()
_AUDIT_MAX = 200
_AUDIT_FLUSH_INTERVAL = 5.0
_flusher_started = False
_flusher_lock = threading.Lock()


def _audit_enqueue(row: dict) -> None:
    with _audit_lock:
        if len(_audit_buffer) >= _AUDIT_MAX:
            # drop oldest to bound memory
            del _audit_buffer[: max(1, _AUDIT_MAX // 4)]
        _audit_buffer.append(row)
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    with _flusher_lock:
        if _flusher_started:
            return
        t = threading.Thread(
            target=_flusher_loop, name="dash-hook-audit-flusher", daemon=True
        )
        t.start()
        _flusher_started = True


def _flusher_loop() -> None:
    while True:
        time.sleep(_AUDIT_FLUSH_INTERVAL)
        try:
            flush_now()
        except Exception:
            logger.debug("hook audit flush failed", exc_info=True)


def flush_now() -> int:
    """Flush buffered audit rows to dash.dash_hook_audit. Returns rows written.

    Safe to call from tests. Silently no-ops if DB unavailable (best-effort).
    """
    with _audit_lock:
        if not _audit_buffer:
            return 0
        batch = list(_audit_buffer)
        _audit_buffer.clear()
    try:
        from sqlalchemy import text  # type: ignore
        from db.session import get_sql_engine  # type: ignore
    except Exception:
        logger.debug("hook audit: sqlalchemy/db.session unavailable; dropping %d", len(batch))
        return 0
    try:
        eng = get_sql_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dash.dash_hook_audit "
                    "(hook_name, hook_kind, tool_name, agent_name, project_slug, "
                    " user_id, run_id, decision, reason, latency_ms, metadata) "
                    "VALUES (:hook_name, :hook_kind, :tool_name, :agent_name, "
                    " :project_slug, :user_id, :run_id, :decision, :reason, "
                    " :latency_ms, CAST(:metadata AS JSONB))"
                ),
                batch,
            )
        return len(batch)
    except Exception as e:
        logger.warning("hook audit write failed: %s", e)
        return 0


def _record(
    *,
    hook_name: str,
    hook_kind: str,
    tool_name: str,
    agent_name: Optional[str],
    project_slug: Optional[str],
    user_id: Optional[int],
    run_id: Optional[str],
    decision: str,
    reason: Optional[str],
    latency_ms: int,
    metadata: Optional[dict] = None,
) -> None:
    if not _flag_enabled():
        return
    try:
        meta_str = json.dumps(metadata or {}, default=str)
    except Exception:
        meta_str = "{}"
    _audit_enqueue(
        {
            "hook_name": hook_name,
            "hook_kind": hook_kind,
            "tool_name": tool_name,
            "agent_name": agent_name,
            "project_slug": project_slug,
            "user_id": user_id,
            "run_id": run_id,
            "decision": decision,
            "reason": reason,
            "latency_ms": int(latency_ms),
            "metadata": meta_str,
        }
    )


# ── Wrapping ────────────────────────────────────────────────────────────────
class HookBlocked(Exception):
    """Raised internally; converted to a structured dict for the caller."""

    def __init__(self, hook_name: str, reason: str):
        super().__init__(f"blocked by {hook_name}: {reason}")
        self.hook_name = hook_name
        self.reason = reason


def _structured_block(hook_name: str, reason: str, tool_name: str) -> dict:
    return {
        "ok": False,
        "blocked": True,
        "hook": hook_name,
        "tool": tool_name,
        "reason": reason,
    }


def _build_ctx(tool_name: str, args: tuple, kwargs: dict) -> HookContext:
    return HookContext(
        tool_name=tool_name,
        agent_name=current_agent_name.get(),
        project_slug=current_project_slug.get(),
        user_id=current_user_id.get(),
        run_id=current_run_id.get(),
        args=list(args),
        kwargs=dict(kwargs),
    )


def _matching(bucket: list[_Hook], tool_name: str) -> list[_Hook]:
    with _registry_lock:
        return [h for h in bucket if _matches(h.applies_to, tool_name)]


def _normalize_result(r) -> HookResult:
    if r is None:
        return HookResult(decision="pass")
    if isinstance(r, HookResult):
        return r
    # tolerate plain dicts
    if isinstance(r, dict):
        return HookResult(
            decision=r.get("decision", "pass"),
            reason=r.get("reason"),
            mutated_args=r.get("mutated_args"),
            mutated_kwargs=r.get("mutated_kwargs"),
            mutated_result=r.get("mutated_result"),
            metadata=r.get("metadata"),
        )
    return HookResult(decision="pass")


def _run_pre_hooks(ctx: HookContext) -> Optional[dict]:
    """Run pre-hooks in priority order. Returns block-payload dict or None."""
    for h in _matching(_pre_hooks, ctx.tool_name):
        t0 = time.perf_counter()
        decision = "pass"
        reason: Optional[str] = None
        try:
            res = _normalize_result(h.fn(ctx))
            decision = res.decision
            reason = res.reason
            if decision == "mutate":
                if res.mutated_args is not None:
                    ctx.args = list(res.mutated_args)
                if res.mutated_kwargs is not None:
                    ctx.kwargs = dict(res.mutated_kwargs)
        except Exception as e:
            decision = "error"
            reason = f"{type(e).__name__}: {e}"
            logger.exception("pre-hook %s raised", h.name)
        finally:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            _record(
                hook_name=h.name, hook_kind="pre", tool_name=ctx.tool_name,
                agent_name=ctx.agent_name, project_slug=ctx.project_slug,
                user_id=ctx.user_id, run_id=ctx.run_id,
                decision=decision, reason=reason, latency_ms=latency_ms,
            )
        if decision == "block":
            return _structured_block(h.name, reason or "blocked", ctx.tool_name)
    return None


def _run_post_hooks(ctx: HookContext, result: Any) -> Any:
    cur = result
    for h in _matching(_post_hooks, ctx.tool_name):
        t0 = time.perf_counter()
        decision = "pass"
        reason: Optional[str] = None
        try:
            res = _normalize_result(h.fn(ctx, cur))
            decision = res.decision
            reason = res.reason
            if decision == "mutate" and res.mutated_result is not None:
                cur = res.mutated_result
            elif decision == "block":
                # Post-hook block replaces result with structured error.
                cur = _structured_block(h.name, reason or "blocked", ctx.tool_name)
        except Exception as e:
            decision = "error"
            reason = f"{type(e).__name__}: {e}"
            logger.exception("post-hook %s raised", h.name)
        finally:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            _record(
                hook_name=h.name, hook_kind="post", tool_name=ctx.tool_name,
                agent_name=ctx.agent_name, project_slug=ctx.project_slug,
                user_id=ctx.user_id, run_id=ctx.run_id,
                decision=decision, reason=reason, latency_ms=latency_ms,
            )
        if decision == "block":
            break
    return cur


def apply_hooks(fn: Callable, *, tool_name: Optional[str] = None) -> Callable:
    """Wrap ``fn`` so all matching pre/post hooks fire around invocations.

    Returns ``fn`` unchanged when EXPERIMENTAL_AGI is not enabled, so this
    is safe to apply unconditionally at registration time.
    """
    if not _flag_enabled():
        return fn

    name = tool_name or getattr(fn, "__name__", "anonymous_tool")

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            ctx = _build_ctx(name, args, kwargs)
            blocked = _run_pre_hooks(ctx)
            if blocked is not None:
                return blocked
            result = await fn(*ctx.args, **ctx.kwargs)
            return _run_post_hooks(ctx, result)

        async_wrapper.__dash_hooked__ = True  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        ctx = _build_ctx(name, args, kwargs)
        blocked = _run_pre_hooks(ctx)
        if blocked is not None:
            return blocked
        result = fn(*ctx.args, **ctx.kwargs)
        return _run_post_hooks(ctx, result)

    sync_wrapper.__dash_hooked__ = True  # type: ignore[attr-defined]
    return sync_wrapper


__all__ = [
    "HookContext", "HookResult",
    "pre_hook", "post_hook",
    "apply_hooks",
    "flush_now", "clear_hooks", "list_hooks",
    "current_run_id", "current_project_slug",
    "current_user_id", "current_agent_name",
]
