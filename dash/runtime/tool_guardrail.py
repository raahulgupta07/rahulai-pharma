"""Tool-call guardrail — kill identical-args retry loops.

Pattern lifted from NousResearch/hermes-agent agent/tool_guardrails.py.

Per-turn state machine: tracks (a) exact-args failure repeats, (b) per-tool
failure count, (c) idempotent-tool no-progress (same result hash).
Returns block decision + synthetic message back to LLM telling it to change
strategy. Fail-soft: any internal error → allow (never break tool dispatch).

Wiring:
  - Reset state at chat-turn start: `reset_for_session(session_id)`
  - Check before tool exec: `check(tool_name, args)` → ToolGuardrailDecision
  - Record outcome after exec: `record(tool_name, args, success, result_hash)`

Env:
  TOOL_GUARDRAIL_DISABLED=1                 → bypass entirely (fail-open)
  TOOL_GUARDRAIL_EXACT_BLOCK_AFTER=3        → block after N identical-args fails
  TOOL_GUARDRAIL_TOOL_HALT_AFTER=8          → halt tool after N total fails
  TOOL_GUARDRAIL_IDEMPOTENT_NO_PROGRESS=3   → warn after N same-result successes
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────────────────────

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default

EXACT_BLOCK_AFTER = _env_int("TOOL_GUARDRAIL_EXACT_BLOCK_AFTER", 3)
TOOL_HALT_AFTER = _env_int("TOOL_GUARDRAIL_TOOL_HALT_AFTER", 8)
IDEMPOTENT_NO_PROGRESS = _env_int("TOOL_GUARDRAIL_IDEMPOTENT_NO_PROGRESS", 3)
DISABLED = os.getenv("TOOL_GUARDRAIL_DISABLED", "0") == "1"

# Tools that are read-only / idempotent (same args → same result is suspicious).
IDEMPOTENT_TOOLS: set[str] = {
    "introspect_schema",
    "discover_tables",
    "search_all",
}

# ───────────────────────────────────────────────────────────────────────────
# State
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class _State:
    exact_failures: dict[str, int] = field(default_factory=dict)   # call_hash → count
    tool_failures: dict[str, int] = field(default_factory=dict)    # tool_name → count
    last_result_hash: dict[str, str] = field(default_factory=dict) # tool_name → hash
    same_result_count: dict[str, int] = field(default_factory=dict)# tool_name → count

# Per-session state. Keyed by session_id (or "global" fallback).
# Bounded: oldest evicted at 256 entries.
_states: dict[str, _State] = {}
_state_order: list[str] = []
_lock = threading.Lock()
_MAX_SESSIONS = 256


def _get_state(session_id: str) -> _State:
    with _lock:
        if session_id not in _states:
            _states[session_id] = _State()
            _state_order.append(session_id)
            while len(_state_order) > _MAX_SESSIONS:
                evicted = _state_order.pop(0)
                _states.pop(evicted, None)
        return _states[session_id]


def reset_for_session(session_id: str) -> None:
    """Call at start of each chat turn / agent run."""
    with _lock:
        _states.pop(session_id, None)
        if session_id in _state_order:
            _state_order.remove(session_id)


# ───────────────────────────────────────────────────────────────────────────
# Hashing
# ───────────────────────────────────────────────────────────────────────────

def _hash(tool_name: str, args: Any) -> str:
    try:
        payload = json.dumps(
            {"t": tool_name, "a": args},
            sort_keys=True, default=str, ensure_ascii=False,
        )
    except Exception:
        payload = f"{tool_name}::{args!r}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _result_hash(result: Any) -> str:
    try:
        s = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception:
        s = repr(result)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class ToolGuardrailDecision:
    action: Literal["allow", "block", "halt", "warn"]
    synthetic_result: str | None = None  # JSON string returned to LLM in place of exec
    reason: str | None = None


_ALLOW = ToolGuardrailDecision(action="allow")


def check(tool_name: str, args: Any, session_id: str = "global") -> ToolGuardrailDecision:
    """Pre-flight check before tool execution.

    Returns ToolGuardrailDecision. On `action="block"` or `"halt"`, caller MUST
    return `decision.synthetic_result` to the LLM instead of executing the tool.
    """
    if DISABLED:
        return _ALLOW
    try:
        st = _get_state(session_id)
        key = _hash(tool_name, args)

        # Gate 1: exact-args repeat failures
        exact_count = st.exact_failures.get(key, 0)
        if exact_count >= EXACT_BLOCK_AFTER:
            msg = (
                f'{{"ok": false, "error": "BLOCKED_BY_GUARDRAIL", '
                f'"message": "Blocked {tool_name}: same arguments failed {exact_count} times. '
                f'Change strategy — try different arguments, a different tool, or ask the user '
                f'for clarification. Do NOT retry with the same arguments.", '
                f'"hint": "Vary your approach before calling this tool again."}}'
            )
            return ToolGuardrailDecision(
                action="block",
                synthetic_result=msg,
                reason=f"exact_args_failed_{exact_count}x",
            )

        # Gate 2: tool-level halt
        tool_count = st.tool_failures.get(tool_name, 0)
        if tool_count >= TOOL_HALT_AFTER:
            msg = (
                f'{{"ok": false, "error": "TOOL_HALTED", '
                f'"message": "Halted {tool_name}: failed {tool_count} times this turn. '
                f'Try a completely different tool or stop and explain the limitation to the user.", '
                f'"hint": "Stop using this tool for now."}}'
            )
            return ToolGuardrailDecision(
                action="halt",
                synthetic_result=msg,
                reason=f"tool_failed_{tool_count}x",
            )

        return _ALLOW
    except Exception as e:
        logger.debug(f"[guardrail] check failed: {e}")
        return _ALLOW


def record(
    tool_name: str,
    args: Any,
    *,
    success: bool,
    result: Any = None,
    session_id: str = "global",
) -> ToolGuardrailDecision:
    """Record outcome after tool execution.

    Returns ToolGuardrailDecision — usually allow, but may return warn if
    idempotent tool is yielding no new info (same result N times).
    """
    if DISABLED:
        return _ALLOW
    try:
        st = _get_state(session_id)
        key = _hash(tool_name, args)

        if not success:
            st.exact_failures[key] = st.exact_failures.get(key, 0) + 1
            st.tool_failures[tool_name] = st.tool_failures.get(tool_name, 0) + 1
            # success resets idempotent counter but failure leaves it alone
            return _ALLOW

        # Success: reset failure counters for this exact key
        st.exact_failures.pop(key, None)

        # Idempotent tool no-progress check
        if tool_name in IDEMPOTENT_TOOLS and result is not None:
            rh = _result_hash(result)
            last = st.last_result_hash.get(tool_name)
            if last == rh:
                st.same_result_count[tool_name] = st.same_result_count.get(tool_name, 0) + 1
                if st.same_result_count[tool_name] >= IDEMPOTENT_NO_PROGRESS:
                    msg = (
                        f'{{"ok": true, "warning": "NO_NEW_INFO", '
                        f'"message": "{tool_name} returned the same result {st.same_result_count[tool_name]} times. '
                        f'You already have this info — proceed with what you know instead of calling again."}}'
                    )
                    return ToolGuardrailDecision(
                        action="warn",
                        synthetic_result=msg,
                        reason="idempotent_no_progress",
                    )
            else:
                st.last_result_hash[tool_name] = rh
                st.same_result_count[tool_name] = 0

        return _ALLOW
    except Exception as e:
        logger.debug(f"[guardrail] record failed: {e}")
        return _ALLOW


# ───────────────────────────────────────────────────────────────────────────
# Convenience context-manager wrapper
# ───────────────────────────────────────────────────────────────────────────

class guard:
    """Context manager wrapper for tool exec.

    Usage:
        with guard("run_sql_query", {"sql": query}, session_id=sid) as g:
            if g.blocked:
                return g.synthetic_result
            result = actual_exec(query)
            g.success(result)
            return result
        # on exception: g.failure() called automatically
    """
    def __init__(self, tool_name: str, args: Any, session_id: str = "global"):
        self.tool_name = tool_name
        self.args = args
        self.session_id = session_id
        self.blocked = False
        self.synthetic_result: str | None = None
        self._recorded = False

    def __enter__(self):
        decision = check(self.tool_name, self.args, session_id=self.session_id)
        if decision.action in ("block", "halt"):
            self.blocked = True
            self.synthetic_result = decision.synthetic_result
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._recorded:
            record(
                self.tool_name, self.args,
                success=(exc_type is None and not self.blocked),
                session_id=self.session_id,
            )
        return False  # never swallow exceptions

    def success(self, result: Any = None) -> ToolGuardrailDecision:
        self._recorded = True
        return record(
            self.tool_name, self.args,
            success=True, result=result, session_id=self.session_id,
        )

    def failure(self) -> None:
        self._recorded = True
        record(self.tool_name, self.args, success=False, session_id=self.session_id)
