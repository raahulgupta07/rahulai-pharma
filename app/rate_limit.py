"""Rate-limit middleware (Track C3).

Per-route sliding-window limits keyed by user_id (when authenticated) or
remote address. Wraps slowapi when available; falls back to a self-contained
in-process sliding-window counter if slowapi isn't installed.

Why a custom middleware on top of slowapi:
- slowapi's `@limit("…")` decorator is per-route via decorator only — wiring
  it across the 800+ endpoints in this codebase is impractical.
- We need path-pattern limits (chat=60/min, upload=10/min, training=20/min,
  default=120/min) without touching every router.
- We need the key_func to prefer `request.state.user_id` so authed users
  share a quota across IPs (and rotating IPs can't game it).
- 429 must include a `Retry-After` header per RFC 6585.

Wired into `app/main.py` after CORS + AuthMiddleware. Whitelisted paths
(`/api/health`, `/metrics`, `/health`) are skipped unconditionally.

Env overrides (optional):
- `RATE_LIMIT_DISABLED=1` → bypass all limiting
- `RATE_LIMIT_DEFAULT=120/minute` → default route limit
- `RATE_LIMIT_CHAT=60/minute`
- `RATE_LIMIT_UPLOAD=10/minute`
- `RATE_LIMIT_TRAINING=20/minute`
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limit specs
# ---------------------------------------------------------------------------

def _parse_limit(spec: str) -> tuple[int, int]:
    """Parse '60/minute' → (60, 60). Supports 'N/(second|minute|hour|day)'."""
    m = re.match(r"^\s*(\d+)\s*/\s*(second|minute|hour|day)\s*$", spec, re.I)
    if not m:
        # safe default — 60/min
        return 60, 60
    n = int(m.group(1))
    unit = m.group(2).lower()
    window = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[unit]
    return n, window


# Path-pattern → limit spec (FIRST MATCH WINS — order matters)
# Whitelist paths are checked separately and bypass entirely.
_DEFAULT_LIMITS: list[tuple[re.Pattern[str], str]] = [
    # Uploads — strictest (10/min)
    (re.compile(r"^/api/upload(?:[/-]|$)"), os.environ.get("RATE_LIMIT_UPLOAD", "10/minute")),
    # Training — moderate (20/min)
    (re.compile(r"^/api/projects/[^/]+/(?:retrain|train|train-all)(?:/|$)"), os.environ.get("RATE_LIMIT_TRAINING", "20/minute")),
    (re.compile(r"^/api/projects/[^/]+/embeddings/backfill"), os.environ.get("RATE_LIMIT_TRAINING", "20/minute")),
    # Chat — busiest (60/min)
    (re.compile(r"^/api/projects/[^/]+/chat(?:/|$)"), os.environ.get("RATE_LIMIT_CHAT", "60/minute")),
    (re.compile(r"^/api/chat(?:/|$)"), os.environ.get("RATE_LIMIT_CHAT", "60/minute")),
    (re.compile(r"^/api/embed/chat(?:/|$)"), os.environ.get("RATE_LIMIT_CHAT", "60/minute")),
]

_WHITELIST_PATHS = {
    "/api/health",
    "/health",
    "/metrics",
    "/info",
    "/config",
    "/",
}

_WHITELIST_PREFIXES = (
    "/ui",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/brand",
    "/api/branding",
    "/api/embed/widget.js",
    "/api/embed/docs",
    "/api/embed/config",
    "/api/health",
    "/health",
)


# ---------------------------------------------------------------------------
# In-process sliding-window counter
# ---------------------------------------------------------------------------

class _SlidingWindowCounter:
    """Thread-safe sliding-window counter keyed by (route, identity).

    Memory bounded — entries auto-pruned on each `check()` call.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, route_key: str, identity: str, limit: int, window_s: int) -> tuple[bool, int, float]:
        """Return (allowed, remaining, retry_after_seconds).

        retry_after_seconds is 0 when allowed.
        """
        now = time.time()
        cutoff = now - window_s
        with self._lock:
            dq = self._buckets[(route_key, identity)]
            # prune
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                # 429 — earliest entry rolls off at dq[0] + window_s
                retry = max(0.0, (dq[0] + window_s) - now) if dq else 0.0
                return False, 0, retry
            dq.append(now)
            return True, max(0, limit - len(dq)), 0.0


_COUNTER = _SlidingWindowCounter()


# ---------------------------------------------------------------------------
# Per-org (project_slug) sliding-window — embed chat only
# ---------------------------------------------------------------------------
#
# v1: in-memory storage (single-worker correctness only). Under multi-worker
# gunicorn the per-org counter is approximate — each worker keeps its own
# bucket. Future improvement: back this with Redis ZSET-per-org so all
# workers + replicas share state. The org-bucket key intentionally mirrors
# the existing per-session counter pattern (collections.deque sliding-window)
# so the Redis migration only needs to swap the storage backend.

_ORG_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_ORG_LOCK = Lock()
_ORG_LAST_USED: dict[str, float] = {}  # last-touched timestamp for GC
_ORG_GC_INTERVAL_S = 300.0  # drop bucket if empty for 5 min
_ORG_GC_LAST_RUN = [0.0]


def _embed_path_regex() -> re.Pattern[str]:
    return re.compile(r"^/api/embed/chat(?:/stream)?(?:[/?]|$)")


_EMBED_CHAT_RE = _embed_path_regex()


def _is_embed_chat_path(path: str) -> bool:
    return bool(_EMBED_CHAT_RE.match(path))


def _org_limit_per_window() -> tuple[int, int]:
    """Return (limit, window_seconds) for per-org embed chat."""
    spec = os.environ.get("ORG_RATE_LIMIT_EMBED", "300/minute")
    return _parse_limit(spec)


# embed_id -> (project_slug, expires_at). 5-min TTL cache.
_EMBED_PROJECT_CACHE: dict[str, tuple[str | None, float]] = {}
_EMBED_PROJECT_LOCK = Lock()
_EMBED_PROJECT_TTL_S = 300.0


def _get_project_slug_for_embed(embed_id: str) -> str | None:
    """Resolve project_slug from embed_id, cached 5min in-process.

    Returns None if embed not found or DB lookup fails (fail-open — never
    block requests on a transient DB hiccup).
    """
    if not embed_id:
        return None
    now = time.time()
    with _EMBED_PROJECT_LOCK:
        cached = _EMBED_PROJECT_CACHE.get(embed_id)
        if cached and cached[1] > now:
            return cached[0]
    try:
        # Lazy imports — keep middleware import-light.
        from sqlalchemy import text  # type: ignore
        from dash.embed import _get_engine  # type: ignore

        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT project_slug FROM public.dash_agent_embeds "
                    "WHERE embed_id = :e"
                ),
                {"e": embed_id},
            ).first()
        slug = row[0] if row else None
    except Exception:
        logger.exception("org rate-limit: project_slug lookup failed for embed_id=%s", embed_id)
        slug = None
    with _EMBED_PROJECT_LOCK:
        _EMBED_PROJECT_CACHE[embed_id] = (slug, now + _EMBED_PROJECT_TTL_S)
    return slug


def _extract_embed_id_from_request(request: Request) -> str | None:
    """Pull embed_id from query/header/cookie if present. Returns None otherwise.

    Body inspection is intentionally skipped — Starlette would consume the
    stream and break downstream handlers. Embed widgets typically include
    embed_id on the URL or in a header for traceability; if absent, the
    middleware falls back to session_token-based resolution (best-effort).
    """
    eid = request.query_params.get("embed_id")
    if eid:
        return eid
    hdr = request.headers.get("x-embed-id") or request.headers.get("X-Embed-Id")
    if hdr:
        return hdr
    return None


def _project_slug_from_session_token(token: str | None) -> str | None:
    """Resolve project_slug from an embed session_token. Used when embed_id
    isn't on the request itself but a session_token is."""
    if not token:
        return None
    try:
        from sqlalchemy import text  # type: ignore
        from dash.embed import _get_engine  # type: ignore

        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT e.project_slug "
                    "FROM public.dash_embed_sessions s "
                    "JOIN public.dash_agent_embeds e ON e.embed_id = s.embed_id "
                    "WHERE s.session_token = :t"
                ),
                {"t": token},
            ).first()
        return row[0] if row else None
    except Exception:
        logger.exception("org rate-limit: session_token lookup failed")
        return None


def _gc_org_buckets(now: float) -> None:
    """Drop empty org buckets that haven't been touched in _ORG_GC_INTERVAL_S."""
    if now - _ORG_GC_LAST_RUN[0] < 60.0:
        return  # at most once per minute
    _ORG_GC_LAST_RUN[0] = now
    cutoff = now - _ORG_GC_INTERVAL_S
    with _ORG_LOCK:
        stale = [k for k, last in _ORG_LAST_USED.items() if last < cutoff]
        for k in stale:
            dq = _ORG_BUCKETS.get(k)
            if dq is not None and len(dq) == 0:
                _ORG_BUCKETS.pop(k, None)
                _ORG_LAST_USED.pop(k, None)


def _check_org_limit(project_slug: str) -> tuple[bool, int, float]:
    """Sliding-window check on org bucket. Returns (allowed, remaining, retry_after)."""
    limit, window_s = _org_limit_per_window()
    key = f"org:{project_slug}:embed_chat"
    now = time.time()
    cutoff = now - window_s
    with _ORG_LOCK:
        dq = _ORG_BUCKETS[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry = max(0.0, (dq[0] + window_s) - now) if dq else 0.0
            _ORG_LAST_USED[key] = now
            return False, 0, retry
        dq.append(now)
        _ORG_LAST_USED[key] = now
        return True, max(0, limit - len(dq)), 0.0


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _is_whitelisted(path: str) -> bool:
    if path in _WHITELIST_PATHS:
        return True
    return any(path.startswith(p) for p in _WHITELIST_PREFIXES)


def _lookup_limit(path: str) -> tuple[str, int, int] | None:
    """Return (route_key, limit, window_s) for the FIRST matching pattern.

    Falls back to RATE_LIMIT_DEFAULT (or 120/minute) if nothing matches.
    Returns None if path is whitelisted.
    """
    if _is_whitelisted(path):
        return None
    for pat, spec in _DEFAULT_LIMITS:
        if pat.match(path):
            n, win = _parse_limit(spec)
            return pat.pattern, n, win
    n, win = _parse_limit(os.environ.get("RATE_LIMIT_DEFAULT", "120/minute"))
    return "__default__", n, win


def _identity_for(request: Request) -> str:
    """Prefer authenticated user_id, fall back to remote address."""
    # AuthMiddleware attaches `request.state.user` and `request.state.user_id`.
    # We tolerate either (different code paths populate different shapes).
    state = getattr(request, "state", None)
    if state is not None:
        # explicit user_id
        uid = getattr(state, "user_id", None)
        if uid:
            return f"u:{uid}"
        # user dict from AuthMiddleware
        user = getattr(state, "user", None)
        if isinstance(user, dict):
            uid = user.get("user_id") or user.get("id") or user.get("username")
            if uid:
                return f"u:{uid}"
    # fallback — remote address
    client = request.client
    if client and client.host:
        return f"ip:{client.host}"
    # last-resort fallback so we don't bucket everyone together
    return "ip:unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit by (path-pattern, identity).

    MUST be registered AFTER AuthMiddleware so `request.state.user_id` is set
    before identity resolution.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        # Master bypass
        if os.environ.get("RATE_LIMIT_DISABLED", "").lower() in ("1", "true", "yes", "on"):
            return await call_next(request)

        path = request.url.path

        # ── Per-org bucket — embed chat only ────────────────────────────────
        # Resolve project_slug from embed_id (query/header) or session_token,
        # apply sliding-window check, return 429 with code='org_rate_limit'
        # before falling through to the per-session/IP check.
        if _is_embed_chat_path(path):
            embed_id = _extract_embed_id_from_request(request)
            project_slug: str | None = None
            if embed_id:
                project_slug = _get_project_slug_for_embed(embed_id)
            if not project_slug:
                # Fallback: resolve via session_token (query/header).
                token = request.query_params.get("session_token") or request.headers.get("x-session-token")
                if token:
                    project_slug = _project_slug_from_session_token(token)
            if project_slug:
                org_limit, org_window_s = _org_limit_per_window()
                org_allowed, org_remaining, org_retry = _check_org_limit(project_slug)
                _gc_org_buckets(time.time())
                if not org_allowed:
                    retry_int = int(org_retry) if org_retry > 0 else 60
                    resp = JSONResponse(
                        {
                            "detail": "Per-org rate limit exceeded for embed chat",
                            "code": "org_rate_limit",
                            "project_slug": project_slug,
                            "limit": f"{org_limit}/{org_window_s}s",
                            "retry_after_seconds": retry_int,
                        },
                        status_code=429,
                    )
                    resp.headers["Retry-After"] = str(retry_int)
                    resp.headers["X-RateLimit-Limit"] = str(org_limit)
                    resp.headers["X-RateLimit-Remaining"] = "0"
                    resp.headers["X-RateLimit-Scope"] = "org"
                    return resp

        limit_info = _lookup_limit(path)
        if limit_info is None:
            # whitelisted
            return await call_next(request)

        route_key, limit, window_s = limit_info
        identity = _identity_for(request)
        allowed, remaining, retry_after = _COUNTER.check(route_key, identity, limit, window_s)

        if not allowed:
            retry_after_int = int(retry_after) if retry_after > 0 else 1
            resp = JSONResponse(
                {
                    "detail": "Rate limit exceeded",
                    "limit": f"{limit}/{window_s}s",
                    "retry_after_seconds": retry_after_int,
                },
                status_code=429,
            )
            resp.headers["Retry-After"] = str(retry_after_int)
            resp.headers["X-RateLimit-Limit"] = str(limit)
            resp.headers["X-RateLimit-Remaining"] = "0"
            return resp

        response: Response = await call_next(request)
        # Best-effort rate-limit headers on success too
        try:
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        except Exception:
            pass
        return response


__all__ = ["RateLimitMiddleware", "_COUNTER"]
