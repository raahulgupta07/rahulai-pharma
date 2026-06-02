"""Dash-OS Phase 6 — Secret-leak detector (post-hook).

Scans agent response text for patterns matching secrets:
- API keys: sk-..., pk_..., OPENROUTER_..., AWS_...
- JWTs: eyJ<base64>.<base64>.<base64>
- Env-var assignments leaking values: API_KEY=xxx
- Private keys: -----BEGIN ... PRIVATE KEY-----
- DB connection strings w/ passwords

Action:
- 'block' — replace whole response w/ generic refusal
- 'redact' — replace matched substrings w/ [REDACTED]
- 'log' — pass through but audit

Default: redact + audit. Block reserved for high-severity (private key, JWT).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

PATTERNS: List[Tuple[str, str, str]] = [
    # (name, regex, severity: 'block'|'redact'|'log')
    ("private_key", r"-----BEGIN[^-]+PRIVATE KEY-----[\s\S]+?-----END[^-]+PRIVATE KEY-----", "block"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "block"),
    ("openrouter_key", r"\bsk-or-[A-Za-z0-9-]{20,}\b", "block"),
    ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b", "redact"),
    ("anthropic_key", r"\bsk-ant-(?:api|admin)\d+-[A-Za-z0-9_-]{20,}\b", "redact"),
    ("stripe_secret", r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b", "block"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b", "redact"),
    ("github_pat", r"\bghp_[A-Za-z0-9]{30,}\b", "redact"),
    ("github_oauth", r"\bgho_[A-Za-z0-9]{30,}\b", "redact"),
    ("slack_token", r"\bxox[bpars]-[0-9A-Za-z-]{10,}\b", "block"),
    ("db_url_password",
     r"\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^:\s]+:[^@\s]+@[^\s]+", "redact"),
    ("env_secret_inline",
     r"\b(API_KEY|SECRET_KEY|PASSWORD|TOKEN)\s*=\s*['\"]?[A-Za-z0-9._-]{12,}", "redact"),
]


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _audit(matches: List[Dict[str, Any]], action: str) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        ctx_proj = ctx_user = ctx_run = ctx_agent = None
        try:
            from dash.agentic.hooks import (
                current_project_slug, current_user_id, current_run_id, current_agent_name,
            )
            ctx_proj = current_project_slug.get()
            ctx_user = current_user_id.get()
            ctx_run = current_run_id.get()
            ctx_agent = current_agent_name.get()
        except Exception:
            pass
        with eng.begin() as conn:
            for m in matches:
                conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_secret_leaks
                          (agent_name, project_slug, user_id, run_id,
                           pattern_matched, match_excerpt, action)
                        VALUES (:ag, :ps, :uid, :rid, :pm, :me, :a)
                        """
                    ),
                    {
                        "ag": ctx_agent, "ps": ctx_proj, "uid": ctx_user, "rid": ctx_run,
                        "pm": m["pattern"], "me": m["excerpt"], "a": action,
                    },
                )
    except Exception as e:
        logger.warning("secret leak audit failed: %s", e)


def scan(text: str) -> List[Dict[str, Any]]:
    """Return list of matches without mutating text."""
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    for name, pattern, severity in PATTERNS:
        try:
            for m in re.finditer(pattern, text, flags=re.MULTILINE):
                start = max(0, m.start() - 15)
                end = min(len(text), m.end() + 15)
                excerpt = text[start:end]
                # mask the secret portion in the excerpt
                excerpt_masked = excerpt[:15] + "[" + "*" * (m.end() - m.start()) + "]" + excerpt[-15:]
                out.append({
                    "pattern": name, "severity": severity,
                    "start": m.start(), "end": m.end(),
                    "excerpt": excerpt_masked[:300],
                })
        except re.error as e:
            logger.warning("regex %s failed: %s", name, e)
    return out


def scan_and_apply(text: str, mode: str = "auto") -> Dict[str, Any]:
    """Scan + sanitize. mode: 'auto'|'redact'|'block'|'log'.

    Returns {ok, text, matches, action_taken}.
    """
    matches = scan(text)
    if not matches:
        return {"ok": True, "text": text, "matches": [], "action_taken": "none"}

    # Determine action
    severities = {m["severity"] for m in matches}
    if mode == "block" or "block" in severities:
        action = "blocked"
        out_text = "[response blocked: detected secret-like content]"
    elif mode == "log":
        action = "logged"
        out_text = text
    else:  # auto / redact
        action = "redacted"
        out_text = text
        # Apply redactions sorted by start desc so offsets don't shift
        for m in sorted(matches, key=lambda x: -x["start"]):
            out_text = out_text[:m["start"]] + f"[REDACTED:{m['pattern']}]" + out_text[m["end"]:]

    _audit(matches, action)
    return {"ok": True, "text": out_text, "matches": matches, "action_taken": action}


# ── Integration as post-hook ─────────────────────────────────────────────
def register_as_post_hook() -> bool:
    """Register secret_leak as a post-hook applied to all tool outputs."""
    try:
        from dash.agentic.hooks import post_hook, HookContext, HookResult  # type: ignore
    except Exception:
        return False

    @post_hook(name="secret_leak_scan", priority=90)
    def _secret_post(ctx: HookContext, result: Any) -> Any:
        # only scan string-ish results
        text = None
        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            text = str(result.get("text") or result.get("response") or "")
        if not text or len(text) < 12:
            return HookResult(decision="pass") if HookResult else None
        scan_result = scan_and_apply(text)
        if scan_result["action_taken"] in ("blocked", "redacted"):
            if isinstance(result, str):
                return HookResult(decision="mutate", mutated_result=scan_result["text"])
            if isinstance(result, dict):
                new_result = dict(result)
                if "text" in new_result:
                    new_result["text"] = scan_result["text"]
                elif "response" in new_result:
                    new_result["response"] = scan_result["text"]
                new_result["_secret_leak_action"] = scan_result["action_taken"]
                return HookResult(decision="mutate", mutated_result=new_result)
        return HookResult(decision="pass") if HookResult else None

    return True
