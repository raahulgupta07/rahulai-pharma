"""SkillRefinery — per-tool utility tracking + patch loader.

Phase 1: track_utility() helper + 60s background flusher thread.
Phases 2+: scoring, patches, shadow validation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time  # noqa: F401
from contextvars import ContextVar
from typing import Any, Callable

logger = logging.getLogger(__name__)

# In-memory invocation buffer. Flushed every FLUSH_INTERVAL_S by a
# background daemon thread.
_BUFFER: list[dict[str, Any]] = []
_BUFFER_LOCK = threading.Lock()
_BUFFER_MAX = 5000

FLUSH_INTERVAL_S = 60

# Per-request context. Chat endpoint sets these before calling the team
# so every tool invocation knows its project + user. Decorator falls back
# to whatever the caller passes explicitly.
_CTX_PROJECT: ContextVar[str | None] = ContextVar("sr_project", default=None)
_CTX_USER: ContextVar[int | None] = ContextVar("sr_user", default=None)
_CTX_AGENT: ContextVar[str | None] = ContextVar("sr_agent", default=None)
# Phase 8 — embed/RLS user attributes (e.g. {store_id:"MUM01", role:"manager"}).
_CTX_USER_ATTRS: ContextVar[dict | None] = ContextVar("sr_user_attrs", default=None)
_CTX_EXTERNAL_USER: ContextVar[str | None] = ContextVar("sr_external_user", default=None)
# Phase 2 — visibility policy intent ("private" default; "network" when caller opts in).
_CTX_QUERY_INTENT: ContextVar[str] = ContextVar("sr_query_intent", default="private")
# Phase 7A — viewer identity for cross-store read audit log.
_CTX_VIEWER_USER_ID: ContextVar[int | None] = ContextVar("sr_viewer_user_id", default=None)
_CTX_VIEWER_SCOPE_ID: ContextVar[str | None] = ContextVar("sr_viewer_scope_id", default=None)
# Phase: embed response style (consumer|developer). Read by dash/instructions.py
# to tone down tags / code blocks / agent routing chatter for end-user widgets.
_CTX_EMBED_RESPONSE_STYLE: ContextVar[str | None] = ContextVar("sr_embed_response_style", default=None)


def set_request_context(project_slug: str | None = None,
                        user_id: int | None = None,
                        agent: str | None = None,
                        user_attrs: dict | None = None,
                        external_user: str | None = None,
                        query_intent: str | None = None,
                        viewer_user_id: int | None = None,
                        viewer_scope_id: str | None = None,
                        embed_response_style: str | None = None) -> None:
    """Bind per-request context (project, user, agent, RLS attrs) for downstream tools."""
    if project_slug is not None:
        _CTX_PROJECT.set(project_slug)
    if user_id is not None:
        _CTX_USER.set(user_id)
    if agent is not None:
        _CTX_AGENT.set(agent)
    if user_attrs is not None:
        _CTX_USER_ATTRS.set(user_attrs)
    if external_user is not None:
        _CTX_EXTERNAL_USER.set(external_user)
    if query_intent is not None:
        _CTX_QUERY_INTENT.set(query_intent)
    if viewer_user_id is not None:
        _CTX_VIEWER_USER_ID.set(viewer_user_id)
    if viewer_scope_id is not None:
        _CTX_VIEWER_SCOPE_ID.set(viewer_scope_id)
    if embed_response_style is not None:
        _CTX_EMBED_RESPONSE_STYLE.set(embed_response_style)
        # Mirror to canonical ContextVar in dash.embed so instructions.py
        # (and any other reader) sees the value without depending on
        # skill_refinery internals.
        try:
            from dash.embed import EMBED_RESPONSE_STYLE as _CANON_STYLE
            _CANON_STYLE.set(embed_response_style)
        except Exception:
            pass


def get_embed_response_style() -> str | None:
    """Returns 'consumer' / 'developer' / None. Used by instructions.py to
    tone the agent reply for end-user embeds."""
    return _CTX_EMBED_RESPONSE_STYLE.get()


def get_viewer_user_id() -> int | None:
    return _CTX_VIEWER_USER_ID.get()


def get_viewer_scope_id() -> str | None:
    return _CTX_VIEWER_SCOPE_ID.get()


def get_user_attrs() -> dict | None:
    """Tools call this to discover RLS attributes set on the current request."""
    return _CTX_USER_ATTRS.get()


def get_external_user() -> str | None:
    return _CTX_EXTERNAL_USER.get()


def get_query_intent() -> str:
    return _CTX_QUERY_INTENT.get()


_ENGINE = None


def _get_engine():
    """NullPool engine that can write to public.dash_* (bypasses RO guard)."""
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine as _sa_create_engine
        from sqlalchemy.pool import NullPool
        from db import db_url
        _ENGINE = _sa_create_engine(db_url, poolclass=NullPool)
    return _ENGINE


def _flush_buffer_to_db() -> int:
    from sqlalchemy import text

    with _BUFFER_LOCK:
        if not _BUFFER:
            return 0
        rows = list(_BUFFER)
        _BUFFER.clear()

    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dash.dash_tool_utility_scores "
                    "(tool_name, agent, project_slug, user_id, args_hash, success, "
                    " latency_ms, error_class, error_message, feedback, retry_count, ts) "
                    "VALUES (:tool_name, :agent, :project_slug, :user_id, :args_hash, "
                    " :success, :latency_ms, :error_class, :error_message, :feedback, "
                    " :retry_count, COALESCE(:ts, NOW()))"
                ),
                rows,
            )
        return len(rows)
    except Exception as e:
        logger.warning("skill_refinery: buffer flush failed (%d rows lost): %s", len(rows), e)
        return 0


def _buffer_append(entry: dict[str, Any]) -> None:
    with _BUFFER_LOCK:
        if len(_BUFFER) >= _BUFFER_MAX:
            del _BUFFER[: len(_BUFFER) - _BUFFER_MAX + 1]
        _BUFFER.append(entry)


def _hash_args(args: tuple, kwargs: dict) -> str:
    try:
        payload = json.dumps([args, kwargs], default=str, sort_keys=True)[:4096]
    except Exception:
        payload = repr((args, kwargs))[:4096]
    return hashlib.sha1(payload.encode("utf-8", "ignore")).hexdigest()[:16]


def _classify_result(result: Any) -> tuple[bool, str | None, str | None]:
    """Heuristic: tool functions return strings; ones starting with ERROR/Error are failures."""
    if isinstance(result, str):
        head = result.lstrip()[:32].lower()
        if head.startswith(("error", "no rows", "failed", "❌")):
            return False, "ToolReturnedError", result[:500]
    return True, None, None


def tracked(tool_name: str,
            fn: Callable,
            *args: Any,
            agent: str | None = None,
            project_slug: str | None = None,
            user_id: int | None = None,
            **kwargs: Any) -> Any:
    """Run fn(*args, **kwargs) and record telemetry. Never raises."""
    t0 = time.monotonic()
    err_class = err_msg = None
    success = True
    result: Any = None

    # Forward agent/project_slug/user_id back into kwargs IF fn's signature
    # accepts them. Caller (Agno) may have passed project_slug intending it
    # for the underlying tool function (e.g. auto_visualize(question, project_slug)),
    # not the telemetry layer. Without this, fn never sees project_slug.
    try:
        import inspect as _inspect
        params = _inspect.signature(fn).parameters
        has_var_kw = any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values())
        if project_slug is not None and (has_var_kw or "project_slug" in params) and "project_slug" not in kwargs:
            kwargs["project_slug"] = project_slug
        if agent is not None and (has_var_kw or "agent" in params) and "agent" not in kwargs:
            kwargs["agent"] = agent
        if user_id is not None and (has_var_kw or "user_id" in params) and "user_id" not in kwargs:
            kwargs["user_id"] = user_id
    except Exception:
        pass

    try:
        result = fn(*args, **kwargs)
        ok, ec, em = _classify_result(result)
        if not ok:
            success = False
            err_class, err_msg = ec, em
    except Exception as e:
        success = False
        err_class = type(e).__name__
        err_msg = str(e)[:500]
        # re-raise so the agent sees the failure
        latency_ms = int((time.monotonic() - t0) * 1000)
        _record(tool_name, agent, project_slug, user_id, args, kwargs,
                success, latency_ms, err_class, err_msg)
        raise

    latency_ms = int((time.monotonic() - t0) * 1000)
    _record(tool_name, agent, project_slug, user_id, args, kwargs,
            success, latency_ms, err_class, err_msg)
    return result


def _record(tool_name, agent, project_slug, user_id,
            args, kwargs, success, latency_ms, err_class, err_msg):
    _buffer_append({
        "tool_name": tool_name,
        "agent": agent or _CTX_AGENT.get(),
        "project_slug": project_slug or _CTX_PROJECT.get(),
        "user_id": user_id if user_id is not None else _CTX_USER.get(),
        "args_hash": _hash_args(args, kwargs),
        "success": success,
        "latency_ms": latency_ms,
        "error_class": err_class,
        "error_message": err_msg,
        "feedback": 0,
        "retry_count": 0,
        "ts": None,
    })


# ── Background flusher (singleton, daemon) ────────────────────────────
_FLUSHER_STARTED = False
_FLUSHER_LOCK = threading.Lock()


def _flusher_loop():
    while True:
        try:
            time.sleep(FLUSH_INTERVAL_S)
            n = _flush_buffer_to_db()
            if n:
                logger.info("skill_refinery: flushed %d invocations", n)
        except Exception as e:
            logger.warning("skill_refinery flusher loop error: %s", e)


def start_flusher() -> None:
    """Spawn the periodic flusher thread (idempotent, daemon)."""
    global _FLUSHER_STARTED
    with _FLUSHER_LOCK:
        if _FLUSHER_STARTED:
            return
        if os.environ.get("SKILL_REFINERY_DISABLED") == "1":
            return
        t = threading.Thread(target=_flusher_loop, name="skill-refinery-flusher", daemon=True)
        t.start()
        _FLUSHER_STARTED = True
        logger.info("skill_refinery: flusher started (interval=%ds)", FLUSH_INTERVAL_S)


# Auto-start on import. Safe under uvicorn workers — each worker gets its own
# buffer + thread. Cheap (one daemon per process) and avoids requiring app/main.py edits.
start_flusher()


# ── Phase 2 — Utility scoring ──────────────────────────────────────────
SCORE_WINDOW_DAYS = 14
LATENCY_NORM_MS = 5000   # 5s = 0; 0ms = 1; linearly clipped


# ── Phase 5 — Active patch loader ──────────────────────────────────────
_PATCH_CACHE: dict[tuple[str, str | None], dict] = {}
_PATCH_CACHE_TS: float = 0.0
_PATCH_CACHE_TTL = 60.0  # seconds


def _get_active_patch(tool_name: str, project_slug: str | None = None) -> dict | None:
    """Return active (applied, not reverted) patch for a tool.

    Lookup order: project-specific patch first, then global (project_slug=NULL).
    Cached for 60s to avoid hammering the DB on every tool build.
    """
    global _PATCH_CACHE, _PATCH_CACHE_TS
    from sqlalchemy import text

    now = time.monotonic()
    if now - _PATCH_CACHE_TS > _PATCH_CACHE_TTL:
        _PATCH_CACHE = {}
        _PATCH_CACHE_TS = now

    key = (tool_name, project_slug)
    if key in _PATCH_CACHE:
        return _PATCH_CACHE[key] or None

    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT new_description, default_args, version "
                "FROM public.dash_tool_patches "
                "WHERE tool_name = :t "
                "  AND (project_slug = :s OR project_slug IS NULL) "
                "  AND applied = TRUE AND reverted = FALSE "
                "ORDER BY (project_slug IS NULL), version DESC "
                "LIMIT 1"
            ), {"t": tool_name, "s": project_slug}).first()
    except Exception as e:
        logger.warning("skill_refinery: patch lookup failed for %s: %s", tool_name, e)
        _PATCH_CACHE[key] = {}
        return None

    if not row:
        _PATCH_CACHE[key] = {}
        return None

    desc, args, ver = row
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    patch = {"new_description": desc, "default_args": args or {}, "version": ver}
    _PATCH_CACHE[key] = patch
    return patch


def apply_patch_to_description(tool_name: str, default_description: str,
                               project_slug: str | None = None) -> str:
    """Return patched description if active patch exists, else default."""
    p = _get_active_patch(tool_name, project_slug)
    return p["new_description"] if p and p.get("new_description") else default_description


def merge_default_args(tool_name: str, kwargs: dict,
                       project_slug: str | None = None) -> dict:
    """Merge active patch default_args into kwargs (user values win)."""
    p = _get_active_patch(tool_name, project_slug)
    if not p or not p.get("default_args"):
        return kwargs
    merged = dict(p["default_args"])
    merged.update(kwargs)  # caller's values override patch defaults
    return merged


def patch_tool_object(tool_obj, tool_name: str, project_slug: str | None = None) -> None:
    """In-place override of an agno @tool object's description.

    Used for tools built via factories where we can't compose the description
    at @tool decoration time (introspect_schema, search_all).
    """
    p = _get_active_patch(tool_name, project_slug)
    if p and p.get("new_description") and hasattr(tool_obj, "description"):
        try:
            tool_obj.description = p["new_description"]
        except Exception as e:
            logger.warning("skill_refinery: could not patch %s.description: %s", tool_name, e)


def invalidate_patch_cache() -> None:
    """Force next _get_active_patch to refetch (call from apply/discard endpoints)."""
    global _PATCH_CACHE, _PATCH_CACHE_TS
    _PATCH_CACHE = {}
    _PATCH_CACHE_TS = 0.0


def auto_track(tool_obj, agent: str | None = None, project_slug: str | None = None,
               name: str | None = None):
    """Replace an agno Function/Tool's entrypoint with a tracked() wrapper.

    Idempotent — tools already wrapped are left alone (marked via _sr_tracked).
    Returns the same tool_obj for chaining.
    """
    if tool_obj is None or getattr(tool_obj, "_sr_tracked", False):
        return tool_obj
    if not hasattr(tool_obj, "entrypoint"):
        return tool_obj
    original = tool_obj.entrypoint
    if not callable(original):
        return tool_obj
    tn = name or getattr(tool_obj, "name", None) or getattr(original, "__name__", "anon_tool")

    def _wrapped(*args, **kwargs):
        # Build kwargs for tracked() — caller's project_slug/agent always win
        # to avoid duplicate-keyword TypeErrors. ContextVars still capture
        # project_slug for telemetry via _record() fallback.
        call_kwargs = dict(kwargs)
        if "project_slug" not in call_kwargs and project_slug is not None:
            call_kwargs["project_slug"] = project_slug
        if "agent" not in call_kwargs and agent is not None:
            call_kwargs["agent"] = agent
        return tracked(tn, original, *args, **call_kwargs)

    try:
        tool_obj.entrypoint = _wrapped
        try:
            tool_obj._sr_tracked = True
        except Exception:
            pass
    except Exception as e:
        logger.warning("skill_refinery: auto_track failed for %s: %s", tn, e)
    return tool_obj


def _wrap_plain_callable(fn, agent, project_slug, name):
    """Return a new function that delegates to tracked(fn). Preserves __name__/__doc__."""
    import functools

    @functools.wraps(fn)
    def _w(*args, **kwargs):
        # Caller's project_slug/agent always win — avoids duplicate kwarg.
        call_kwargs = dict(kwargs)
        if "project_slug" not in call_kwargs and project_slug is not None:
            call_kwargs["project_slug"] = project_slug
        if "agent" not in call_kwargs and agent is not None:
            call_kwargs["agent"] = agent
        return tracked(name, fn, *args, **call_kwargs)

    _w._sr_tracked = True  # type: ignore[attr-defined]
    _w._sr_original = fn   # type: ignore[attr-defined]
    return _w


def auto_track_list(tools, agent: str | None = None, project_slug: str | None = None):
    """Wrap every tool in the list IN PLACE.

    Two paths:
    - agno Function/Tool objects (have .entrypoint) → swap entrypoint
    - plain Python @tool-decorated callables (no .entrypoint) → replace with wrapped fn
    """
    for i, t in enumerate(tools):
        try:
            if t is None or getattr(t, "_sr_tracked", False):
                continue
            if hasattr(t, "entrypoint") and callable(getattr(t, "entrypoint")):
                auto_track(t, agent=agent, project_slug=project_slug)
            elif callable(t) and not hasattr(t, "name"):
                # Plain function — swap in list with wrapped variant.
                name = getattr(t, "__name__", "anon_tool")
                tools[i] = _wrap_plain_callable(t, agent, project_slug, name)
            elif callable(t):
                # Has .name but no entrypoint — uncommon. Try to wrap __call__.
                name = getattr(t, "name", None) or getattr(t, "__name__", "anon_tool")
                tools[i] = _wrap_plain_callable(t, agent, project_slug, name)
        except Exception as e:
            logger.warning("skill_refinery: list wrap error at %d: %s", i, e)
    return tools


def compute_utility_scores(project_slug: str | None = None,
                           window_days: int = SCORE_WINDOW_DAYS) -> list[dict]:
    """Aggregate tool telemetry into per-tool utility scores.

    Score = 0.5*success + 0.3*feedback + 0.2*(1 - normalized_latency).
    Feedback defaults to neutral (0.5) when no thumbs signal yet.
    Returns the list of rows written. Upserts into dash_tool_scores.
    """
    from sqlalchemy import text

    eng = _get_engine()
    flush_first = _flush_buffer_to_db()  # capture in-flight rows before scoring
    if flush_first:
        logger.info("skill_refinery: flushed %d before scoring", flush_first)

    where = ["ts >= NOW() - INTERVAL ':d days'".replace(":d", str(int(window_days)))]
    params: dict = {}
    if project_slug:
        where.append("project_slug = :slug")
        params["slug"] = project_slug
    where_sql = " AND ".join(where)

    sql = f"""
        SELECT tool_name,
               COUNT(*)                                                  AS calls,
               SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)              AS fails,
               AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END)              AS success_rate,
               AVG(NULLIF(feedback,0)::float)                            AS fb_avg,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)   AS p50,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)  AS p95,
               (ARRAY_AGG(error_message ORDER BY ts DESC)
                FILTER (WHERE error_message IS NOT NULL))[1]             AS last_err
        FROM dash.dash_tool_utility_scores
        WHERE {where_sql}
        GROUP BY tool_name
    """

    with eng.begin() as conn:
        rows = conn.execute(text(sql), params).fetchall()

        out = []
        for r in rows:
            tool_name, calls, fails, sr, fb_avg, p50, p95, last_err = r
            success_pct = float(sr or 0) * 100.0
            # feedback: -1..+1 → 0..1; neutral 0.5 if no signal
            fb_norm = ((float(fb_avg) + 1.0) / 2.0) if fb_avg is not None else 0.5
            feedback_pct = fb_norm * 100.0
            lat_norm = max(0.0, min(1.0, 1.0 - (float(p50 or 0) / LATENCY_NORM_MS)))
            score = 0.5 * success_pct + 0.3 * feedback_pct + 0.2 * (lat_norm * 100.0)

            row = {
                "tool_name": tool_name,
                "project_slug": project_slug,
                "score": round(score, 2),
                "success_rate": round(success_pct, 2),
                "feedback_score": round(feedback_pct, 2),
                "latency_p50_ms": int(p50 or 0),
                "latency_p95_ms": int(p95 or 0),
                "calls": int(calls),
                "fails": int(fails),
                "last_error": (last_err or "")[:500] if last_err else None,
                "window_days": int(window_days),
            }
            out.append(row)

            conn.execute(
                text(
                    "INSERT INTO public.dash_tool_scores "
                    "(tool_name, project_slug, score, success_rate, feedback_score, "
                    " latency_p50_ms, latency_p95_ms, calls, fails, last_error, "
                    " window_days, computed_at) "
                    "VALUES (:tool_name, :project_slug, :score, :success_rate, :feedback_score, "
                    " :latency_p50_ms, :latency_p95_ms, :calls, :fails, :last_error, "
                    " :window_days, NOW()) "
                    "ON CONFLICT (tool_name, project_slug) DO UPDATE SET "
                    " score=EXCLUDED.score, success_rate=EXCLUDED.success_rate, "
                    " feedback_score=EXCLUDED.feedback_score, "
                    " latency_p50_ms=EXCLUDED.latency_p50_ms, latency_p95_ms=EXCLUDED.latency_p95_ms, "
                    " calls=EXCLUDED.calls, fails=EXCLUDED.fails, "
                    " last_error=EXCLUDED.last_error, window_days=EXCLUDED.window_days, "
                    " computed_at=NOW()"
                ),
                row,
            )

    logger.info("skill_refinery: scored %d tools (project=%s)", len(out), project_slug or "*")
    return out
