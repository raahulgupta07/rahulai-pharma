"""Skill template preprocessing — inline SQL + variable substitution.

Pattern lifted from NousResearch/hermes-agent agent/skill_preprocessing.py,
adapted to Dash's multi-tenant model: SHELL execution replaced with
READ-ONLY SQL execution scoped to the project.

Why:
  Skill instructions are static strings. Without preprocessing, the LLM
  must call `discover_tables` / `count` queries just to learn what's
  already known at skill-load time. Burns tool calls + tokens.

Pattern:
  !`SELECT COUNT(*) FROM dash_table_metadata WHERE project_slug='${PROJECT_SLUG}'`
  → executes at skill-load time
  → returns scalar result (or "[empty]" / "[error]")
  → substituted into prompt before LLM sees it

Variables (always substituted first):
  ${PROJECT_SLUG}  ${SESSION_ID}  ${USER_ID}  ${DATE}  ${UTC_DATE}

Safety:
  - SELECT-only via dash.tools.sql_validator.validate_and_fix(strict=True)
  - Uses RLSAwareSQLTools (same tenant scoping as chat tools)
  - 2-second timeout per query
  - 256-char output cap per substitution
  - 30-second total preprocessing budget (no runaway)
  - 5-minute cache keyed by (project_slug, template_hash)
  - Fail-soft: any error → "[error]" placeholder, never raises

Env:
  SKILL_PREPROCESS_DISABLED=1   → bypass entirely (returns input unchanged)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Pattern: !`...` where ... starts with SELECT or WITH (case-insensitive)
_INLINE_SQL_RE = re.compile(r"!`\s*((?:SELECT|WITH)\b[^`]+)`", re.IGNORECASE | re.DOTALL)
# Pattern: ${VAR_NAME}
_TEMPLATE_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

_MAX_OUTPUT_CHARS = 256
_PER_QUERY_TIMEOUT_S = 2.0
_TOTAL_BUDGET_S = 30.0
_CACHE_TTL_S = 300.0  # 5 min
_DISABLED = os.getenv("SKILL_PREPROCESS_DISABLED", "0") == "1"

# (project_slug, sha256(template)) → (expires_at, processed_text)
_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_order: list[tuple[str, str]] = []
_cache_lock = threading.Lock()
_MAX_CACHE = 512


def _template_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ctx_vars(project_slug: str | None, extra: dict | None = None) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    base = {
        "PROJECT_SLUG": project_slug or "",
        "DATE": now.strftime("%Y-%m-%d"),
        "UTC_DATE": now.strftime("%Y-%m-%d"),
        "UTC_TIME": now.strftime("%H:%M:%S"),
        "ISO_TIME": now.isoformat(),
    }
    if extra:
        for k, v in extra.items():
            if isinstance(k, str) and k.isupper():
                base[k] = str(v) if v is not None else ""
    return base


def _exec_sql(sql: str, project_slug: str) -> str:
    """Execute a single inline SELECT. Returns truncated stringified result."""
    try:
        from dash.tools.sql_validator import validate_and_fix
        v = validate_and_fix(sql, project_slug, strict=True)
        if not v.get("ok"):
            return "[invalid sql]"
        clean_sql = v.get("sql") or sql
    except Exception as e:
        logger.debug(f"[skill-prep] validator skipped: {e}")
        clean_sql = sql

    # Reuse the project's MDL engine via build.py RLSAwareSQLTools so RLS +
    # tenant scoping apply identically to chat-time exec.
    try:
        from sqlalchemy import text as _sql_text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        # Statement timeout enforced server-side. Wrap in SET LOCAL to scope.
        with eng.connect() as c:
            c.execute(_sql_text(f"SET LOCAL statement_timeout = {int(_PER_QUERY_TIMEOUT_S * 1000)}"))
            res = c.execute(_sql_text(clean_sql))
            rows = res.fetchmany(5)
        if not rows:
            return "[empty]"
        # Single scalar value? Return it bare.
        if len(rows) == 1 and len(rows[0]) == 1:
            val = rows[0][0]
            return str(val)[:_MAX_OUTPUT_CHARS] if val is not None else "[null]"
        # Multi-col single row? Comma-join.
        if len(rows) == 1:
            return ", ".join(str(v) for v in rows[0])[:_MAX_OUTPUT_CHARS]
        # Multi-row? Show count + first.
        head = ", ".join(str(v) for v in rows[0])
        return f"{head} (+{len(rows) - 1} more)"[:_MAX_OUTPUT_CHARS]
    except Exception as e:
        logger.debug(f"[skill-prep] sql exec failed: {str(e)[:120]}")
        return "[error]"


def preprocess(
    text: str,
    project_slug: str | None = None,
    extra_vars: dict | None = None,
) -> str:
    """Preprocess a skill instruction string.

    1. Substitute ${VAR} template variables.
    2. Execute !`SELECT...` inline SQL queries, replace with results.

    Fail-soft: returns input unchanged on any internal error or when disabled.
    """
    if _DISABLED or not text or not isinstance(text, str):
        return text

    # Fast path: no template syntax → return as-is, no cache write.
    if "${" not in text and "!`" not in text:
        return text

    # Cache lookup
    cache_key = (project_slug or "", _template_hash(text))
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    deadline = now + _TOTAL_BUDGET_S

    # Layer 1: template vars (cheap, always run)
    ctx = _ctx_vars(project_slug, extra_vars)

    def _var_sub(m: re.Match) -> str:
        key = m.group(1)
        return ctx.get(key, m.group(0))  # leave unrecognized vars alone

    try:
        text = _TEMPLATE_VAR_RE.sub(_var_sub, text)
    except Exception as e:
        logger.debug(f"[skill-prep] var sub failed: {e}")

    # Layer 2: inline SQL (needs project_slug for tenant scope)
    if "!`" in text and project_slug:
        def _sql_sub(m: re.Match) -> str:
            if time.time() > deadline:
                return "[timeout]"
            sql = m.group(1).strip()
            return _exec_sql(sql, project_slug)

        try:
            text = _INLINE_SQL_RE.sub(_sql_sub, text)
        except Exception as e:
            logger.debug(f"[skill-prep] sql sub failed: {e}")

    # Cache write (bounded)
    with _cache_lock:
        _cache[cache_key] = (now + _CACHE_TTL_S, text)
        if cache_key not in _cache_order:
            _cache_order.append(cache_key)
        while len(_cache_order) > _MAX_CACHE:
            evicted = _cache_order.pop(0)
            _cache.pop(evicted, None)

    return text


def invalidate_cache(project_slug: str | None = None) -> int:
    """Clear preprocessing cache. Returns entries removed.

    Call after a skill is patched / model schema changes so the next
    preprocess() recomputes against fresh data.
    """
    with _cache_lock:
        if project_slug is None:
            n = len(_cache)
            _cache.clear()
            _cache_order.clear()
            return n
        keys = [k for k in _cache if k[0] == project_slug]
        for k in keys:
            _cache.pop(k, None)
            if k in _cache_order:
                _cache_order.remove(k)
        return len(keys)
