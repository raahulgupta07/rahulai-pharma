"""Context-exhaustion guards — pure helpers, no DB.

Two fail-open helpers protect the chat hot path from blowing the context
window:

  cap_tool_result(payload, max_tokens=None)
      Truncate an over-large tool result (list of rows / string / dict) to a
      token budget, appending a sentinel so the agent knows to paginate.

  trim_stale_tool_results(messages, keep_turns=None)
      Drop the *content* of tool-result messages older than `keep_turns`
      conversation turns, replacing each with a short stub. Keeps the most
      recent turns intact.

Both are pure (no DB, no network), never raise, and no-op when the env
``CONTEXT_GUARDS_DISABLED`` is truthy. Token estimate is the cheap
``len(str(x)) // 4`` heuristic (≈4 chars/token).
"""

from __future__ import annotations

import os

__all__ = ["cap_tool_result", "trim_stale_tool_results"]


def _enabled() -> bool:
    """False when CONTEXT_GUARDS_DISABLED is truthy (1/true/yes)."""
    return str(os.getenv("CONTEXT_GUARDS_DISABLED", "")).strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _max_tokens(override: int | None) -> int:
    if override is not None:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            pass
    try:
        return max(1, int(os.getenv("TOOL_RESULT_MAX_TOKENS", "25000")))
    except (TypeError, ValueError):
        return 25000


def _keep_turns(override: int | None) -> int:
    if override is not None:
        try:
            return max(0, int(override))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(os.getenv("CONTEXT_EDIT_KEEP_TURNS", "6")))
    except (TypeError, ValueError):
        return 6


def _est_tokens(s: str) -> int:
    return len(s) // 4


def cap_tool_result(payload, max_tokens: int | None = None):
    """Cap a tool result to a token budget. Never raises.

    Returns ``(capped_payload, was_capped: bool, dropped_count: int)``.

    - list of rows  → keep the first K rows that fit, append a sentinel dict
      ``{"_truncated": True, "_dropped": N, "_hint": "paginate via offset"}``.
    - string        → slice to budget, append
      ``"\\n… [truncated N chars, paginate via offset]"``.
    - dict / other  → returned unchanged unless its ``str()`` exceeds budget,
      in which case the string form is sliced (best-effort).
    """
    try:
        if not _enabled():
            return payload, False, 0

        cap = _max_tokens(max_tokens)

        # ── list of rows ──────────────────────────────────────────────────
        if isinstance(payload, list):
            total_tokens = _est_tokens(str(payload))
            if total_tokens <= cap:
                return payload, False, 0
            kept: list = []
            running = 0
            for row in payload:
                rt = _est_tokens(str(row))
                # +sentinel headroom (~30 tokens) so the sentinel always fits.
                if running + rt > cap - 30:
                    break
                kept.append(row)
                running += rt
            dropped = len(payload) - len(kept)
            sentinel = {
                "_truncated": True,
                "_dropped": dropped,
                "_hint": "paginate via offset",
            }
            kept.append(sentinel)
            return kept, True, dropped

        # ── string ────────────────────────────────────────────────────────
        if isinstance(payload, str):
            if _est_tokens(payload) <= cap:
                return payload, False, 0
            keep_chars = cap * 4
            dropped = len(payload) - keep_chars
            if dropped < 0:
                dropped = 0
            capped = payload[:keep_chars] + (
                f"\n… [truncated {dropped} chars, paginate via offset]"
            )
            return capped, True, dropped

        # ── dict / other ──────────────────────────────────────────────────
        as_str = str(payload)
        if _est_tokens(as_str) <= cap:
            return payload, False, 0
        # Best-effort: return a wrapper describing the truncation.
        keep_chars = cap * 4
        dropped = max(0, len(as_str) - keep_chars)
        return (
            {
                "_truncated": True,
                "_dropped": dropped,
                "_hint": "paginate via offset",
                "_preview": as_str[:keep_chars],
            },
            True,
            dropped,
        )
    except Exception:
        # Fail-open: never break the caller.
        return payload, False, 0


def _is_tool_result(msg) -> bool:
    """Heuristic: does this message dict carry a tool result?"""
    if not isinstance(msg, dict):
        return False
    role = str(msg.get("role", "")).lower()
    if role in ("tool", "function", "tool_result", "tool-result"):
        return True
    # Some shapes mark tool results via a `tool_call_id` or `name` + content.
    if msg.get("tool_call_id") and "content" in msg:
        return True
    return False


def trim_stale_tool_results(messages, keep_turns: int | None = None):
    """Drop the content of tool-result messages older than `keep_turns` turns.

    A "turn" is counted backwards from the end of the conversation on each
    user message. Tool-result messages in turns older than `keep_turns` have
    their content replaced with a stub. The most recent `keep_turns` turns are
    left intact. Returns a NEW list. Fail-open: on any error returns the
    original `messages` unchanged.
    """
    try:
        if not _enabled() or not isinstance(messages, list) or not messages:
            return messages

        keep = _keep_turns(keep_turns)

        # Walk backwards, counting user messages as turn boundaries.
        out: list = []
        turns_seen = 0
        for msg in reversed(messages):
            role = str(msg.get("role", "")).lower() if isinstance(msg, dict) else ""
            if role == "user":
                turns_seen += 1
            stale = turns_seen > keep
            if stale and _is_tool_result(msg):
                stub = dict(msg)
                stub["content"] = f"[tool result elided — older than {keep} turns]"
                out.append(stub)
            else:
                out.append(msg)
        out.reverse()
        return out
    except Exception:
        return messages
