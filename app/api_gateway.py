"""OpenAI-compatible API gateway for external apps (e.g. a PHP storefront).

Lets any standard OpenAI client talk to the locked CityPharma analyst:

    base_url = https://<host>/api/v1
    api_key  = dash-key-XXXX           (Authorization: Bearer dash-key-XXXX)

Endpoints:
    GET  /api/v1/models             → list the single virtual model
    POST /api/v1/chat/completions   → chat.completion (OpenAI shape).
                                      stream=false → blocking JSON.
                                      stream=true  → text/event-stream of
                                      chat.completion.chunk frames + [DONE].

Mechanics: reuses the EXISTING agent pipeline (`app.projects.project_chat`) by
constructing an internal Starlette Request (form-encoded, state.user pre-set),
draining its SSE stream server-side, and collapsing the `TeamRunContent` deltas
into one answer — exactly the way the backend's own `full_content` accumulator
does (projects.py). No new agent logic.

Store scoping (Phase 0): the API key carries a StoreScope (dash/api_scope.py).
This module sets API_STORE_SCOPE around the run so the data tools can enforce
the three-tier access rule. (Tool-level masking lands in Phase 2.)
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import os
import re
import time
from collections import deque
from urllib.parse import urlencode
from uuid import uuid4

from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.requests import Request as StarletteRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["openai-compat"])

MODEL_ID = "citypharma-analyst"

# The agent's self-reported DB-failure answer (it swallows a stripped-SQL error
# and returns this prose instead of raising). Used to trigger the stock_check
# recovery fallback on store keys. See blocking path below.
_DB_FAIL_RE = re.compile(
    r"couldn'?t query the database|encountered an error|no data returned|"
    r"function .*not found|unknown sources?",
    re.IGNORECASE,
)

# --- Per-key rate limiting (Redis fixed-window, global across workers) -------
# Cap from env API_GW_RATE_PER_MIN (default 60) per 60s window. Backed by Redis
# (cp-redis) so the cap is GLOBAL across all gunicorn workers — INCR+EXPIRE on a
# per-(key, minute-bucket) counter. Falls back to an in-process per-worker deque
# only if Redis is unreachable (degraded, logged once). Only store *service
# keys* (scope_mode='store' AND via_api_key) are throttled; human/admin sessions
# and global-scope keys bypass entirely.
#
# Feature #3 — PER-OUTLET RATE LIMIT (env APIGW_PER_KEY_RATE, default "1" = on):
# When enabled the Redis key is scoped to the individual service-account (outlet
# key), so one busy outlet cannot starve the others. The cap (requests/min) is
# the same _effective_cap() value that the global limiter uses. When the env flag
# is off (APIGW_PER_KEY_RATE=0) the historic behaviour is preserved.
_RATE_WINDOW_S = 60
try:
    _RATE_CAP = int(os.getenv("API_GW_RATE_PER_MIN", "60"))
except (TypeError, ValueError):
    _RATE_CAP = 60
_RATE_HITS: dict = {}   # fallback: key_id -> deque[float]
_redis_client = None
_redis_down_logged = False

# --- Response cache config (Feature #2) -------------------------------------
# APIGW_CACHE_TTL (seconds, default 90; 0 = disabled). Caches BLOCKING
# chat/completion responses in Redis, keyed by a hash of
# (username + scope_mode + store_id + normalised last user message + model).
# The key always includes the service-account identity so one outlet can NEVER
# receive another outlet's cached (masked) answer.
try:
    _CACHE_TTL = int(os.getenv("APIGW_CACHE_TTL", "90"))
except (TypeError, ValueError):
    _CACHE_TTL = 90

# --- LLM-down fallback config (Feature #5) ----------------------------------
# APIGW_FALLBACK (default "1" = on). When the agent call raises/times-out AND
# the message matches a simple stock/availability heuristic, the gateway
# attempts a direct stock_check() lookup and returns a short no-LLM answer so
# basic counter queries keep working during an LLM provider outage.
_STOCK_HEURISTIC_RE = re.compile(
    r"\b(stock|in\s+stock|available|availability|qty|quantity|price|substitute|substitutes)\b",
    re.IGNORECASE,
)

# --- Per-outlet rate-limit feature flag -------------------------------------
def _per_key_rate_enabled() -> bool:
    return str(os.getenv("APIGW_PER_KEY_RATE", "1")).strip().lower() not in ("0", "false", "no")

# --- Live-editable rate cap (config table, 10s TTL cache) --------------------
# The effective per-minute cap is read from public.dash_apigw_config (singleton
# id=1) so a super-admin UI change takes effect within ~10s without a restart.
# Cached for 10s to avoid a DB read per request; falls back to the env-derived
# _RATE_CAP on any error (table missing, DB down, malformed value).
_CAP_CACHE_TTL_S = 10.0
_cap_cache_value: int = _RATE_CAP
_cap_cache_at: float = 0.0


def _effective_cap() -> int:
    """Current per-minute rate cap. Reads rate_per_min from the singleton
    public.dash_apigw_config, cached for 10s. Falls back to the env-derived
    _RATE_CAP on any error/missing row. Never raises."""
    global _cap_cache_value, _cap_cache_at
    now = time.time()
    if (now - _cap_cache_at) < _CAP_CACHE_TTL_S:
        return _cap_cache_value
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT rate_per_min FROM public.dash_apigw_config WHERE id = 1"
            )).fetchone()
        if row and row[0] is not None:
            _cap_cache_value = int(row[0])
        else:
            _cap_cache_value = _RATE_CAP
    except Exception:
        # Fail-soft: keep env default, refresh timestamp so we don't hammer a
        # broken DB on every request.
        _cap_cache_value = _RATE_CAP
    _cap_cache_at = now
    return _cap_cache_value


def _get_redis():
    """Lazy singleton Redis client (same URL/pattern as dash/training)."""
    global _redis_client, _redis_down_logged
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        url = os.getenv("REDIS_URL", "redis://dash-redis:6379")
        _redis_client = redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        _redis_client.ping()
        return _redis_client
    except Exception:
        if not _redis_down_logged:
            logger.warning("apigw: Redis unavailable, rate limit falls back to per-worker")
            _redis_down_logged = True
        _redis_client = None
        return None


def _user_key_id(user: dict):
    """The dash_users id the key binds to (auth returns 'user_id'; tolerate
    'id' too)."""
    return user.get("user_id") if user else None


def _rate_exceeded(kid, username: str = "") -> bool:
    """True when this key is over cap for the current minute window.

    Feature #3 — PER-OUTLET RATE LIMIT:
    When APIGW_PER_KEY_RATE is on (default), the Redis key is scoped to the
    individual service-account so one outlet cannot starve others:
        apigw:rl:key:<kid>:<bucket>   (per-key, APIGW_PER_KEY_RATE=1)
    When off the original global key is used:
        apigw:rl:<kid>:<bucket>       (historic, APIGW_PER_KEY_RATE=0)

    Falls back to an in-process per-worker sliding window when Redis is down.
    """
    cap = _effective_cap()
    r = _get_redis()
    per_key = _per_key_rate_enabled()
    if r is not None:
        try:
            bucket = int(time.time() // _RATE_WINDOW_S)
            # Per-key namespace prefix distinguishes the two modes in Redis so a
            # rolling deploy with mixed workers doesn't corrupt existing counters.
            if per_key:
                rkey = f"apigw:rl:key:{kid}:{bucket}"
            else:
                rkey = f"apigw:rl:{kid}:{bucket}"
            n = r.incr(rkey)
            if n == 1:
                r.expire(rkey, _RATE_WINDOW_S * 2)
            return n > cap
        except Exception:
            logger.exception("apigw: redis rate check failed, using fallback")
    # In-process fallback (per worker, sliding window)
    now = time.time()
    # Use a distinct in-process key prefix when per-key mode is active so the
    # two modes don't share the same deque.
    fkey = f"pk:{kid}" if per_key else kid
    dq = _RATE_HITS.get(fkey)
    if dq is None:
        dq = deque()
        _RATE_HITS[fkey] = dq
    cutoff = now - _RATE_WINDOW_S
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= cap:
        return True
    dq.append(now)
    return False


def _rate_check(user: dict) -> None:
    """Per-key throttle. Raises HTTPException(429) when the cap is exceeded.
    Skips global-scope keys and any non-API-key (human/admin) caller — only
    store-bound service keys are throttled.

    Feature #3: when APIGW_PER_KEY_RATE=1 (default) the rate window is keyed
    per service-account so one outlet cannot exhaust the cap for all others.
    """
    try:
        if not user or not user.get("via_api_key"):
            return
        if (user.get("scope_mode") or "global").strip().lower() == "global":
            return
        kid = _user_key_id(user)
        if kid is None:
            return
        username = (user.get("username") or "")
        if _rate_exceeded(kid, username=username):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded, retry shortly.",
                headers={"Retry-After": str(int(_RATE_WINDOW_S))},
            )
    except HTTPException:
        raise
    except Exception:
        # Rate limiting must never break the request on an internal error.
        logger.exception("apigw: rate check failed (fail-open)")


# --- Usage metering (fail-soft) ---------------------------------------------

def _apigw_cost(model: str, p_toks: int, c_toks: int) -> float:
    """Estimate USD cost for a gateway call from the shared per-1M price map in
    dash.settings. Substring match (model ids carry provider prefixes). Returns
    0.0 on any miss — cost is best-effort telemetry, never blocks a response."""
    try:
        from dash.settings import _MODEL_PRICES_PER_1M as prices
        m = (model or "").lower()
        rates = None
        for k, v in prices.items():
            if k in m or m in k:
                rates = v
                break
        if not rates:
            return 0.0
        return (int(p_toks or 0) * rates[0] + int(c_toks or 0) * rates[1]) / 1_000_000
    except Exception:
        return 0.0


def _log_usage(user: dict, model: str, p_toks: int, c_toks: int,
               streamed: bool, request_type: str = "chat",
               status: str = "ok", session_id: str | None = None,
               latency_ms: int | None = None) -> None:
    """Best-effort INSERT into public.dash_apigw_usage. Opens its own short
    connection via get_write_engine (platform writes to public.dash_* need the
    write engine per project rules). NEVER raises — metering can't break the
    response. Logs cost_usd + request_type ('chat'|'embedding') for the unified
    Admin Usage dashboard."""
    try:
        from sqlalchemy import text
        from db.session import get_write_engine
        # Real underlying LLM model — the caller-supplied `model` is the public
        # OpenAI alias (e.g. "citypharma-analyst"), which carries no price. Price
        # the tokens against the actual engine the gateway runs so cost_usd is
        # non-zero, and store it in engine_model for the BY-MODEL breakdown.
        try:
            from dash.settings import CHAT_MODEL as _CM, get_embedding_model as _gem
            engine_model = (_gem() if (request_type or "") == "embedding" else _CM)
        except Exception:
            engine_model = ("google/gemini-embedding-2-preview"
                            if (request_type or "") == "embedding"
                            else "google/gemini-3-flash-preview")
        eng = get_write_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_apigw_usage "
                "(key_id, service_account, store_id, scope_mode, model, engine_model, "
                " prompt_tokens, completion_tokens, total_tokens, streamed, "
                " cost_usd, request_type, status, session_id, latency_ms) "
                "VALUES (:kid, :svc, :sid, :sm, :model, :emodel, :p, :c, :t, :streamed, "
                "        :cost, :rt, :st, :sess, :lat)"
            ), {
                "kid": _user_key_id(user),
                "svc": (user or {}).get("username"),
                "sid": (user or {}).get("store_id") or (user or {}).get("site_code"),
                "sm": (user or {}).get("scope_mode") or "global",
                "model": model,
                "emodel": engine_model,
                "p": int(p_toks or 0),
                "c": int(c_toks or 0),
                "t": int((p_toks or 0) + (c_toks or 0)),
                "streamed": bool(streamed),
                "cost": _apigw_cost(engine_model, p_toks, c_toks),
                "rt": request_type or "chat",
                "st": status or "ok",
                "sess": session_id,
                "lat": int(latency_ms) if latency_ms is not None else None,
            })
            conn.commit()
    except Exception:
        logger.exception("apigw: usage log failed (ignored)")


def _sec_event(kind: str, user: dict | None, detail: str = "",
               severity: str = "WARN", meta: dict | None = None) -> None:
    """Persist a security/guardrail event (leak, blocked, rate_limited, auth_fail).
    Fail-soft — security telemetry must never break a response."""
    try:
        import json
        from sqlalchemy import text
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_security_events "
                "(kind, severity, service_account, key_id, store_id, detail, meta) "
                "VALUES (:k, :sev, :svc, :kid, :sid, :d, CAST(:m AS jsonb))"
            ), {
                "k": kind, "sev": severity,
                "svc": (user or {}).get("username"),
                "kid": _user_key_id(user),
                "sid": (user or {}).get("store_id") or (user or {}).get("site_code"),
                "d": detail[:1000] if detail else None,
                "m": json.dumps(meta or {}),
            })
            conn.commit()
    except Exception:
        logger.debug("apigw: sec_event log failed (ignored)", exc_info=True)


def _log_bodies(user: dict, session_id: str, prompt: str, answer: str,
                masked: bool) -> None:
    """Persist the chat prompt + answer for the gateway (privacy-gated by env
    APIGW_LOG_BODIES). OFF by default. Fail-soft."""
    import os
    if str(os.getenv("APIGW_LOG_BODIES", "")).strip().lower() not in ("1", "true", "yes"):
        return
    try:
        from sqlalchemy import text
        from db.session import get_write_engine
        eng = get_write_engine()
        kid = _user_key_id(user)
        svc = (user or {}).get("username")
        sid = (user or {}).get("store_id") or (user or {}).get("site_code")
        with eng.connect() as conn:
            for role, content in (("user", prompt), ("assistant", answer)):
                conn.execute(text(
                    "INSERT INTO public.dash_apigw_messages "
                    "(session_id, key_id, service_account, store_id, role, content, masked) "
                    "VALUES (:sess, :kid, :svc, :sid, :role, :content, :masked)"
                ), {
                    "sess": session_id, "kid": kid, "svc": svc, "sid": sid,
                    "role": role, "content": (content or "")[:20000],
                    "masked": bool(masked) if role == "assistant" else False,
                })
            conn.commit()
    except Exception:
        logger.debug("apigw: body log failed (ignored)", exc_info=True)


# ---------------------------------------------------------------------------
# Feature #2 — RESPONSE CACHE (env APIGW_CACHE_TTL, default 90s; 0=disabled)
# ---------------------------------------------------------------------------
# Blocking (stream=false) responses are cached in Redis keyed by a SHA-256
# hash of (username + scope_mode + store_id + normalised message + model).
# The key always includes the service-account identity so one outlet can NEVER
# receive another outlet's cached (masked) answer. Cache hits skip the agent
# entirely, add an X-Cache: HIT header, and still call _log_usage.
# Every operation is fail-soft: any Redis error falls through to a normal run.

def _cache_key(user: dict, message: str, model: str) -> str:
    """Build a Redis cache key that is scoped to the calling service-account.

    Components:
    - username       — the service-account / API-key identity
    - scope_mode     — 'store' vs 'global' (different masking tiers)
    - store_id       — the bound outlet (ensures per-store isolation)
    - message        — normalised (strip, lower-case, collapse whitespace)
    - model          — different model = different answer distribution

    A SHA-256 prefix keeps the key short; the 'apigw:cache:' namespace avoids
    collisions with the rate-limit counters.
    """
    username = (user or {}).get("username") or ""
    scope_mode = (user or {}).get("scope_mode") or "global"
    store_id = (user or {}).get("store_id") or (user or {}).get("site_code") or ""
    # Normalise: strip leading/trailing whitespace, collapse internal runs,
    # lower-case so "Is Panadol in stock?" and "is panadol in stock?" share a hit.
    norm_msg = re.sub(r"\s+", " ", message.strip()).lower()
    fingerprint = f"{username}|{scope_mode}|{store_id}|{norm_msg}|{model}"
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:48]
    return f"apigw:cache:{digest}"


def _cache_get(user: dict, message: str, model: str):
    """Return the cached response dict (OpenAI shape) or None. Fail-soft."""
    if _CACHE_TTL <= 0:
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        rkey = _cache_key(user, message, model)
        raw = r.get(rkey)
        if raw is None:
            return None
        return _json.loads(raw)
    except Exception:
        logger.debug("apigw: cache get failed (ignored)", exc_info=True)
        return None


def _cache_set(user: dict, message: str, model: str, payload: dict) -> None:
    """Store a blocking response dict in Redis with TTL=_CACHE_TTL. Fail-soft."""
    if _CACHE_TTL <= 0:
        return
    # Never cache empty / whitespace-only answers — one failed or empty run
    # would otherwise poison the key and serve "" for the whole TTL.
    try:
        _content = (payload.get("choices", [{}])[0]
                    .get("message", {}).get("content", "") or "").strip()
        if not _content:
            return
    except Exception:
        return
    try:
        r = _get_redis()
        if r is None:
            return
        rkey = _cache_key(user, message, model)
        r.setex(rkey, _CACHE_TTL, _json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.debug("apigw: cache set failed (ignored)", exc_info=True)


# ---------------------------------------------------------------------------
# Feature #5 — LLM-DOWN FALLBACK (env APIGW_FALLBACK, default "1" = on)
# ---------------------------------------------------------------------------
# When the internal agent call raises/errors AND the user's last message
# matches a simple stock/availability heuristic, attempt a direct no-LLM
# answer via stock_check() and return it in OpenAI response shape with a note
# that the AI assistant is temporarily unavailable. Blocking path only.
# If the fallback also fails, the normal error is returned. Fail-soft.

def _fallback_enabled() -> bool:
    return str(os.getenv("APIGW_FALLBACK", "1")).strip().lower() not in ("0", "false", "no")


def _is_stock_question(message: str) -> bool:
    """Heuristic: does the message look like a simple stock/availability query?"""
    return bool(_STOCK_HEURISTIC_RE.search(message or ""))


def _extract_drug_query(message: str) -> str:
    """Best-effort: extract the medicine name from a simple stock question.
    Strips common question-words and returns the remainder as the search term.
    Falls back to the full normalised message if extraction is uncertain.
    """
    # Remove common question scaffolding, punctuation, and filler.
    cleaned = re.sub(
        r"\b(is|are|do|does|we|you|have|has|check|get|any|the|for|at|my|"
        r"branch|store|outlet|available|availability|in\s+stock|stock|qty|"
        r"quantity|price|substitute|substitutes|please|how\s+much|how\s+many)\b",
        " ", message, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^a-zA-Z0-9\s\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # If nothing useful remains, use the raw message so the SQL ILIKE still works.
    return cleaned if len(cleaned) >= 3 else message.strip()


async def _llm_down_fallback(user: dict, message: str, model: str,
                             session_id: str) -> Optional[dict]:
    """Attempt a direct stock_check() answer when the LLM is unavailable.

    Returns an OpenAI-shape response dict on success, or None on failure.
    The response includes a note that the AI assistant is temporarily unavailable
    so callers know this is a degraded answer. Fail-soft — never raises.
    """
    if not _fallback_enabled():
        return None
    if not _is_stock_question(message):
        return None
    try:
        from dash.tools.pharma_shop_tool import stock_check
        drug_q = _extract_drug_query(message)
        site_code = (user or {}).get("store_id") or (user or {}).get("site_code") or ""
        result = stock_check(query=drug_q, site_code=site_code, limit=10)
        if not result.get("ok"):
            return None
        results = result.get("results") or []
        count = result.get("count", 0)
        if count == 0:
            answer_text = (
                f"No matches found for '{drug_q}'."
                " (quick lookup — AI assistant temporarily unavailable)"
            )
        else:
            lines = [
                f"**Quick stock lookup for '{drug_q}'** "
                f"(AI assistant temporarily unavailable)\n"
            ]
            in_stock_n = result.get("in_stock_count", 0)
            lines.append(f"{count} product(s) found, {in_stock_n} in stock at your branch.\n")
            for r in results[:8]:
                brand = r.get("brand") or r.get("salt") or "Unknown"
                your_stock = r.get("your_stock", 0)
                in_stock = r.get("in_stock", False)
                status = "In stock" if in_stock else "Out of stock"
                qty_part = f" ({your_stock} units)" if in_stock else ""
                lines.append(f"- **{brand}**: {status}{qty_part}")
            answer_text = "\n".join(lines)

        prompt_toks = _estimate_tokens(message)
        completion_toks = _estimate_tokens(answer_text)
        return {
            "id": f"chatcmpl-{uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": answer_text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_toks,
                "completion_tokens": completion_toks,
                "total_tokens": prompt_toks + completion_toks,
            },
            "x_session_id": session_id,
            "x_fallback": True,
        }
    except Exception:
        logger.debug("apigw: llm_down_fallback failed (ignored)", exc_info=True)
        return None


# --- Response sanitizer (belt-and-suspenders, store-locked keys only) -------
# A site_code looks like 20063-CCBRBKMY (4-5 digits, dash, alnum). For a
# store-locked key we scan the final answer for a foreign site_code (NOT the
# bound store) sitting within ~40 chars of a digit-group that looks like a
# quantity. This is defense-in-depth ON TOP of the tool-level masking — we
# prefer logging (telemetry) over aggressive redaction, and only redact the
# number tokens immediately tied to a foreign site_code.
_SITECODE_RE = re.compile(r"\b\d{4,5}-[A-Z0-9]+\b")
# A digit-group that looks like a quantity (>=2 digits, optional thousands/dec).
_QTY_RE = re.compile(r"\b\d{2,}(?:[.,]\d+)*\b")


def _sanitize_for_scope(text: str, user: dict) -> str:
    """Defense-in-depth leak guard for store-locked keys. If a FOREIGN
    site_code appears within ~40 chars of a quantity-looking number, log a
    WARNING for telemetry and redact only the adjacent number(s) to
    '[restricted]'. Conservative: legit text is never mangled, and the bound
    store's own code is ignored. Returns text unchanged for non-store scopes."""
    try:
        if not text or not user:
            return text
        if (user.get("scope_mode") or "global").strip().lower() != "store":
            return text
        # Owned set: store_ids (multi-outlet) ∪ primary store_id/site_code. Any
        # site_code in this set is the key's OWN store (Tier-1) — never foreign.
        owned = set()
        ids_csv = (user.get("store_ids") or "").strip()
        for x in ids_csv.split(","):
            if x.strip():
                owned.add(x.strip())
        bound = (user.get("store_id") or user.get("site_code") or "").strip()
        if bound:
            owned.add(bound)
        if not owned:
            return text

        foreign_spans = [
            m for m in _SITECODE_RE.finditer(text)
            if m.group(0) not in owned
        ]
        if not foreign_spans:
            return text

        # Collect quantity spans within ~40 chars of any foreign site_code.
        redact_spans: list[tuple[int, int]] = []
        for fm in foreign_spans:
            lo, hi = fm.start() - 40, fm.end() + 40
            for qm in _QTY_RE.finditer(text):
                # Skip the digits that are part of the site_code itself.
                if qm.start() >= fm.start() and qm.end() <= fm.end():
                    continue
                if qm.end() >= lo and qm.start() <= hi:
                    redact_spans.append((qm.start(), qm.end()))

        _foreign = ",".join(sorted({m.group(0) for m in foreign_spans}))
        logger.warning(
            "apigw: possible cross-store leak for key %s (bound=%s, foreign=%s)",
            _user_key_id(user), bound, _foreign,
        )
        _sec_event("leak", user,
                   f"cross-store leak: foreign={_foreign}", severity="CRIT",
                   meta={"bound": bound, "foreign": _foreign,
                         "redacted": len(redact_spans)})

        if not redact_spans:
            return text   # logged the signal; nothing tightly tied to redact

        # Redact right-to-left so offsets stay valid. De-dupe overlaps.
        redact_spans = sorted(set(redact_spans), key=lambda s: s[0], reverse=True)
        out = text
        for start, end in redact_spans:
            out = out[:start] + "[restricted]" + out[end:]
        return out
    except Exception:
        # Sanitizer must never break the response.
        logger.exception("apigw: sanitize failed (ignored)")
        return text

# API consumers (PHP / external apps) want a clean, self-contained answer — not
# the dashboard's SOURCES / WHY-this-matters / KPI-snapshot / recommendations
# scaffolding. The dashboard system-prompt pushes that layout unconditionally, so
# we prepend an API-MODE directive to the user turn to suppress it at generation
# time (works for BOTH streaming + blocking, since the prose is never emitted).
_API_STYLE_DIRECTIVE = (
    "[API MODE] You are a friendly, experienced pharmacist talking to a counter "
    "colleague through a chat app. Sound human and warm — like a real chemist who "
    "knows the shelf, NOT a database report.\n"
    "VOICE:\n"
    "• Open with one natural sentence that answers the question and reassures "
    "(e.g. \"Yes — we're well stocked on paracetamol at your branch, 5 lines on the "
    "shelf right now.\").\n"
    "• Use plain, spoken language. Contractions are good (\"we've got\", \"it's\"). "
    "First person plural (\"we have\", \"we're out of\"). No corporate filler.\n"
    "• Close with ONE short, helpful pharmacist tip when it fits — what you'd reach "
    "for first, a fast/slow mover, or a heads-up on a low line "
    "(e.g. \"If they want tablets, PARACAP 10's is your deepest stock; the syrups "
    "move slower.\"). Keep it one sentence, genuinely useful, never salesy.\n"
    "FORMAT — show the data clean, ONCE:\n"
    "• A list/breakdown of items with numbers → ONE compact markdown table "
    "(header row, separator row, one row per item). Don't also restate those rows "
    "as bullets or sentences — the table IS the statement.\n"
    "• A single value or 1–2 facts → just say it in a sentence, no table.\n"
    "• Friendly columns: Medicine · Salt · Stock · Price — not raw db names like "
    "article_code unless asked.\n"
    "NEVER print the same datum twice. Blank line before the table.\n"
    "OVERRIDE THE DASHBOARD FORMATS: even for stock / medicine-lookup / substitute "
    "answers, do NOT emit the ✅/❌ one-line-per-medicine list, and do NOT emit any "
    "[DRUG:]/[COMPOSITION:]/[STOCK:]/[EQUIV:]/[EVIDENCE:] monograph tags — those are "
    "dashboard-UI render codes and show up as raw junk in this chat app. Here, the "
    "warm sentence + clean markdown table above is the ONLY format.\n"
    "DO NOT add: a SOURCES section, 'WHY this matters', 'KPI snapshot', a 'Summary:' "
    "block, totals you had to compute yourself (skip them unless asked — they're "
    "often wrong), related/follow-up question lists, freshness/lineage notes, "
    "confidence lines, status-emoji headers, or bracketed directive tags. Warmth "
    "yes, scaffolding no.\n"
    "LANGUAGE: Reply in the SAME language as the USER QUESTION below. A Burmese "
    "question gets a fully Burmese answer (opening line, table headers, tip — all "
    "Burmese); an English question gets English. Keep brand names + Arabic digits "
    "as-is even inside Burmese.\n\n"
    "USER QUESTION:\n"
)

# Belt-and-suspenders: if the model still appends a meta block (it sometimes
# does despite the directive + quick tier), cut the answer at the first such
# heading. Matches a line that is ONLY a meta heading, after stripping markdown
# emphasis/heading markers. Used on the BLOCKING path (streaming can't retro-edit).
_API_META_HEADINGS = (
    "sources", "sources:", "why this matters", "kpi snapshot", "kpi (snapshot)",
    "related questions", "related", "follow-up questions", "next questions",
)


def _strip_api_sections(text: str) -> str:
    """Truncate a trailing dashboard meta block (SOURCES / WHY / KPI snapshot /
    related questions) the model may emit despite the API-mode directive."""
    if not text:
        return text
    lines = text.split("\n")
    cut = None
    for i, ln in enumerate(lines):
        bare = ln.strip().lstrip("#").strip().strip("*").strip().rstrip(":").strip().lower()
        if bare in _API_META_HEADINGS:
            cut = i
            break
    if cut is None:
        return text
    # Also drop an immediately-preceding horizontal rule / blank lines.
    while cut > 0 and lines[cut - 1].strip() in ("", "---", "***", "___"):
        cut -= 1
    return "\n".join(lines[:cut]).strip()


# --- Warm-chemist humanizer (store-key API answers) -------------------------
# The analyst's system prompt makes it emit raw db column names, an often-WRONG
# self-computed Total/Summary block, and a clinical headline. The user-turn
# directive can't fully override the system prompt, so we deterministically
# reshape the FINAL answer into a warm, counter-pharmacist voice here.
_FRIENDLY_HDR = {
    "brand_name": "Medicine", "brand": "Medicine", "medicine": "Medicine",
    "product": "Medicine", "name": "Medicine", "product_name": "Medicine",
    "medicine_name": "Medicine", "item": "Medicine", "drug": "Medicine",
    "generic_name": "Salt", "salt": "Salt", "salt/composition": "Salt",
    "generic": "Salt", "molecule": "Salt", "salt_composition": "Salt",
    "stock_qty": "Stock", "stock": "Stock", "qty": "Stock", "quantity": "Stock",
    "stock_quantity": "Stock", "in_stock": "Stock", "units": "Stock",
    "your_branch": "Stock", "your_stock": "Stock", "branch_stock": "Stock",
    "weighted_cost_price": "Price (MMK)", "cost": "Price (MMK)",
    "price": "Price (MMK)", "cost_price": "Price (MMK)", "unit_cost": "Price (MMK)",
}
# Columns that clutter a counter answer — drop if a friendlier sibling exists.
_DROP_HDR = {"article_code", "code", "article", "composition", "site_code",
             "status", "branch"}


def _hkey(h: str) -> str:
    """Normalize a header cell to a lookup key: lower, drop a trailing (MMK)/
    (unit) note, spaces/slashes → underscores. 'Weighted Cost Price (MMK)' →
    'weighted_cost_price'; 'Brand Name' → 'brand_name'."""
    k = (h or "").strip().lower()
    k = re.sub(r"\s*\([^)]*\)\s*$", "", k)
    k = re.sub(r"[ /\-]+", "_", k).strip("_")
    return k
_SUMMARY_LINE_RE = re.compile(
    r"^\**\s*(total\b|summary\b|highest stock\b|note\b|grand total\b|"
    r"inventory value\b|total units\b|total stock\b|total in-stock\b|"
    r"\d+\s+additional\b)", re.IGNORECASE)


def _num(s: str):
    try:
        return float(re.sub(r"[^\d.\-]", "", s))
    except Exception:
        return None


# Static field-LABEL map (English → Burmese). Deterministic cleanup for Burmese
# replies: the bilingual model writes its DATA + prose in Burmese, but the system
# format/monograph blocks keep leaking English field labels. We swap ONLY these
# fixed labels — never the data, brand names, numbers, or model prose. Ordered
# longest-first so multi-word labels match before their single-word substrings.
_MY_LABEL_MAP = [
    ("Confirm with a pharmacist before use.", "အသုံးမပြုမီ ဆေးဝါးကျွမ်းကျင်သူနှင့် တိုင်ပင်ပါ။"),
    ("Total stock quantity", "စုစုပေါင်းလက်ကျန်"),
    ("Product Details", "ထုတ်ကုန်အသေးစိတ်"),
    ("Generic Name", "ဆေးအမည်ရင်း"),
    ("Article Code", "ပစ္စည်းကုဒ်"),
    ("Side Effect", "ဘေးထွက်ဆိုးကျိုး"),
    ("Brand Name", "ကုန်အမှတ်တံဆိပ်"),
    ("Composition", "ပါဝင်ပစ္စည်း"),
    ("Indication", "အသုံးပြုရန်"),
    ("Other Branches", "အခြားဆိုင်ခွဲများ"),
    ("Category", "အမျိုးအစား"),
    ("Quantity", "အရေအတွက်"),
    ("Medicine", "ဆေး"),
    ("Dosage", "သောက်သုံးပုံ"),
    ("Summary", "အနှစ်ချုပ်"),
    ("Status", "အခြေအနေ"),
    ("Salt", "ဆေးဂုဏ်ရင်း"),
    ("Stock", "လက်ကျန်"),
    ("Price", "စျေးနှုန်း"),
    ("Total", "စုစုပေါင်း"),
    ("Cost", "ကုန်ကျစရိတ်"),
]
# snake_case DB column headers that can appear in raw tables.
_MY_COL_MAP = {
    "article_code": "ပစ္စည်းကုဒ်", "brand_name": "ကုန်အမှတ်တံဆိပ်",
    "generic_name": "ဆေးအမည်ရင်း", "stock_qty": "လက်ကျန်",
    "weighted_cost_price": "ကုန်ကျစရိတ်", "site_code": "ဆိုင်ခွဲကုဒ်",
    "composition": "ပါဝင်ပစ္စည်း", "category": "အမျိုးအစား",
}


def _localize_labels_my(text: str) -> str:
    """Swap fixed English field-labels → Burmese in an already-Burmese reply.
    Deterministic, label-only: leaves data, brand names, digits and model prose
    untouched. Case-insensitive on the label keys; longest match wins."""
    if not text:
        return text
    import re as _re
    out = text
    for col, my in _MY_COL_MAP.items():
        out = _re.sub(_re.escape(col), my, out, flags=_re.IGNORECASE)
    for eng, my in _MY_LABEL_MAP:
        out = _re.sub(r"\b" + _re.escape(eng) + r"\b", my, out, flags=_re.IGNORECASE)
    return out


def _humanize_api_answer(text: str, question: str = "") -> str:
    """Reshape a store-key stock answer into a warm counter-pharmacist reply:
    friendly table headers, no raw article_code/composition clutter, no
    self-computed (often wrong) Total/Summary block, a natural opening line, and
    one correct 'deepest stock' tip computed from the rows. Idempotent-ish: if
    there's no markdown table it returns the text mostly untouched (minus any
    stray summary block)."""
    # Bilingual guard: a Burmese question means the agent already answered in
    # Burmese (LANGUAGE rule in instructions.py + _API_STYLE_DIRECTIVE). Don't run
    # the warm ENGLISH reshape below (it would overwrite the native Burmese reply).
    # Instead apply the deterministic LABEL-ONLY localizer: swap the English field
    # labels the format/monograph blocks keep leaking → Burmese, leaving the data,
    # brand names, digits and model prose untouched. NO content translation.
    if any('က' <= c <= '႟' for c in (question or "")):
        return _localize_labels_my(text)
    if not text or "|" not in text:
        return text
    raw = text.split("\n")
    # 1) Drop the often-wrong Total/Summary/Note scaffolding lines.
    lines = [ln for ln in raw if not _SUMMARY_LINE_RE.match(ln.strip())]

    # 2) Locate the markdown table (header | sep | rows).
    def _cells(ln):
        return [c.strip() for c in ln.strip().strip("|").split("|")]
    hdr_i = None
    for i in range(len(lines) - 1):
        a, b = lines[i].strip(), lines[i + 1].strip()
        if a.startswith("|") and b.startswith("|") and set(b) <= set("|:- "):
            hdr_i = i
            break
    if hdr_i is None:
        return "\n".join(lines).strip()

    headers = _cells(lines[hdr_i])
    rows, end = [], hdr_i + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        rows.append(_cells(lines[end]))
        end += 1

    # 2b) Drop rows poisoned by a leaked tool/SQL error (store-locked keys can't
    #     run_sql_query -> the join failure text bleeds into the final cell).
    _ERR_CELL_RE = re.compile(
        r"cannot execute|can'?t execute|error|because:|not found|no data|"
        r"failed|unable to|exception|traceback", re.IGNORECASE)
    rows = [r for r in rows
            if not any(_ERR_CELL_RE.search(c or "") for c in r)]
    if not rows:
        # Whole table was error noise -> return clean prose, no broken grid.
        clean = [ln for ln in lines[:hdr_i]
                 if ln.strip() and not _ERR_CELL_RE.search(ln)]
        return "\n".join(clean).strip() or (
            "I couldn't pull that lookup just now — try a specific brand name "
            "and I'll check its shelf stock.")

    # 3) Decide kept columns: drop clutter, rename the rest to friendly labels.
    keep = [j for j, h in enumerate(headers) if _hkey(h) not in _DROP_HDR]
    if not keep:
        keep = list(range(len(headers)))
    new_hdr = [_FRIENDLY_HDR.get(_hkey(headers[j]), headers[j].strip())
               for j in keep]

    def _clean_cell(v):
        # Strip leading status glyphs the analyst stuffs into the name cell.
        return re.sub(r"^[✅❌✔✖☑\s]+", "", v or "").strip()

    def _proj(cells):
        return [_clean_cell(cells[j]) if j < len(cells) else "" for j in keep]

    tbl = ["| " + " | ".join(new_hdr) + " |",
           "|" + "|".join(["---"] * len(new_hdr)) + "|"]
    for r in rows:
        tbl.append("| " + " | ".join(_proj(r)) + " |")

    # 4) Warm opening, built from the data (not the model's headline).
    n = len(rows)
    # common salt? -> name the drug
    salt_idx = next((k for k, h in enumerate(new_hdr) if h == "Salt"), None)
    med_idx = next((k for k, h in enumerate(new_hdr) if h == "Medicine"), None)
    stock_idx = next((k for k, h in enumerate(new_hdr) if h == "Stock"), None)
    drug = "these"
    if salt_idx is not None and rows:
        # Normalize away dose-form parentheticals so "Paracetamol (Suppo)" and
        # "Paracetamol" count as one salt.
        salts = [re.sub(r"\s*\(.*?\)\s*", "", (_proj(r)[salt_idx] or "")).strip()
                 for r in rows]
        salts = [s for s in salts if s]
        uniq = set(salts)
        if len(uniq) == 1:
            drug = next(iter(uniq))
        elif salts:
            # Combo salts ("Paracetamol & Ibuprofen") share a lead molecule —
            # name the most common first word ("Paracetamol") instead of falling
            # back to a (often wrong) word lifted from the question.
            from collections import Counter
            heads = [s.split("&")[0].split(",")[0].split()[0]
                     for s in salts if s.split()]
            if heads:
                top, cnt = Counter(heads).most_common(1)[0]
                if cnt >= len(heads) / 2 and len(top) > 2:
                    drug = top
    if drug == "these" and question:
        # Last single word after of/with/for, skipping stop-words.
        m = re.search(r"\b(?:of|with|for|have|carry)\s+([A-Za-z][A-Za-z\-]{2,})",
                      question)
        if m and m.group(1).lower() not in (
                "the", "any", "our", "your", "stock", "branch", "medicine",
                "medicines", "products"):
            drug = m.group(1).strip()
    # Are ANY rows actually in stock? (catalog-match tables can be all-zero.)
    max_q = 0.0
    if stock_idx is not None:
        for r in rows:
            max_q = max(max_q, _num(_proj(r)[stock_idx]) or 0)
    in_stock = stock_idx is None or max_q > 0
    plural = "" if n == 1 else "s"

    if stock_idx is None:
        # No per-line stock column came back (store-locked key couldn't join the
        # stock table) -> claim catalog coverage, not shelf depth.
        lead = (f"We list {n} {drug} line{plural} at your branch. "
                f"Per-line stock counts weren't available on this lookup — "
                f"ask me about a specific brand for its shelf qty.")
    elif in_stock:
        lead = (f"Yes — we're stocked on {drug} at your branch, "
                f"{n} line{plural} on the shelf right now.")
    else:
        # Catalog has it but every line is zero on the shelf.
        lead = (f"We carry {drug} in the catalog, but every line is out of "
                f"stock at your branch right now — worth a transfer from another "
                f"branch or a substitute check.")

    # 5) One correct tip: deepest-stock line (computed, not model-guessed).
    #    Only when something is actually in stock.
    tip = ""
    if in_stock and med_idx is not None and stock_idx is not None and rows:
        best, bestq = None, -1.0
        for r in rows:
            pr = _proj(r)
            q = _num(pr[stock_idx]) or 0
            if q > bestq:
                bestq, best = q, pr[med_idx]
        if best and bestq > 0:
            tip = (f"\n\n💊 Tip: {best} is your deepest stock "
                   f"({int(bestq):,} units) — reach for that first.")

    return (lead + "\n\n" + "\n".join(tbl)).strip() + tip


# The agent answer is laced with exec-layout directives the frontend AnswerCard
# renders into cards (e.g. [KPI: 29,085|Total stock|N/A], [NARRATION: ...]).
# For an API consumer (PHP) we keep the *data* (narration prose + KPI numbers)
# and drop the pure-presentation directives.
_NARRATION_RE = re.compile(r"\[NARRATION:\s*([^\]]*)\]", re.IGNORECASE)
_HEADLINE_RE = re.compile(r"\[HEADLINE:\s*([^\]]*)\]", re.IGNORECASE)
_KPI_RE = re.compile(r"\[KPI:\s*([^\]]*)\]", re.IGNORECASE)
# Any remaining [UPPER_TOKEN ...] directive (RECOMMENDATION, FRESHNESS, RELATED,
# MODE, ANALYSIS, CONFIDENCE, TIER, SO_WHAT, SOURCES, EXEC, …) → drop.
_ANY_TAG_RE = re.compile(r"\[[A-Z][A-Z0-9_]*(?::[^\]]*)?\]")


def _locked_slug() -> str:
    try:
        from dash.single_agent import locked_slug
        return locked_slug()
    except Exception:
        return "citypharma"


def _require_user(request: Request) -> dict:
    """Resolve the caller from the Authorization Bearer key. 401 if missing."""
    from app.auth import get_current_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Missing or invalid API key. "
                                 "Send 'Authorization: Bearer dash-key-...'.")
    return user


def _clean_answer(text: str) -> str:
    """Turn the directive-laced agent answer into clean text for an API client.

    Keeps the data: [NARRATION: x] / [HEADLINE: x] → their inner text,
    [KPI: value|label|delta] → a 'label: value' line. Drops every other
    presentation directive. Idempotent on plain text.
    """
    if not text:
        return ""
    out = _NARRATION_RE.sub(lambda m: m.group(1).strip(), text)
    out = _HEADLINE_RE.sub(lambda m: m.group(1).strip(), out)

    def _kpi(m):
        parts = [p.strip() for p in m.group(1).split("|")]
        val = parts[0] if parts else ""
        label = parts[1] if len(parts) > 1 else ""
        # Strip a trailing status emoji/dash the UI uses (🟢 / ━ 0%).
        val = re.sub(r"\s*[━─]\s*[\d.]+%?\s*$", "", val).strip()
        if label and val:
            return f"{label}: {val}"
        return val or label

    out = _KPI_RE.sub(_kpi, out)
    out = _ANY_TAG_RE.sub("", out)          # drop remaining directives
    out = re.sub(r"[ \t]+\n", "\n", out)    # trailing spaces
    out = re.sub(r"\n{3,}", "\n\n", out)    # collapse blank runs
    # De-dupe consecutive identical lines (KPI blocks often repeat the headline).
    seen_prev = None
    lines = []
    for ln in out.split("\n"):
        s = ln.strip()
        if s and s == seen_prev:
            continue
        seen_prev = s
        lines.append(ln)
    return "\n".join(lines).strip()


def _last_user_message(messages: list) -> str:
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            # OpenAI vision-style content parts → concat text parts
            if isinstance(c, list):
                return " ".join(
                    p.get("text", "") for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
    return ""


_KNOWLEDGE_INTENT_RE = re.compile(
    r"\b(tell me more|more about|what (is|are|else)|whats|what's|explain|"
    r"describe|information|info on|details about|use(d| for|s\b)|indication|"
    r"side[- ]?effect|dosage|dose|how (does|do)|what does|composition|salt|"
    r"about (it|this|that|the))\b", re.IGNORECASE)


def _is_knowledge_intent(message: str) -> bool:
    """True when the user wants to KNOW ABOUT a drug (uses/indications/salt/
    differences) rather than a stock count. Such questions need real reasoning +
    the indications/knowledge path, not a reflexive stock_check."""
    return bool(_KNOWLEDGE_INTENT_RE.search(message or ""))


def _build_agent_message(messages: list, last: str) -> str:
    """Give the agent multi-turn context. The gateway is otherwise stateless —
    only the last user message reaches the agent — so a follow-up like "tell me
    more" loses its antecedent and the agent re-runs the obvious tool on the lone
    noun. Prepend a compact transcript of prior turns (capped) so follow-ups
    resolve references ("it", "that", "more") against the real thread."""
    prior = [m for m in (messages or [])[:-1]
             if isinstance(m, dict) and m.get("role") in ("user", "assistant")
             and isinstance(m.get("content"), str) and m.get("content").strip()]
    if not prior:
        return last
    lines = []
    for m in prior[-6:]:                      # last ~6 turns is plenty of context
        who = "User" if m["role"] == "user" else "You"
        c = " ".join(m["content"].split())
        if len(c) > 600:
            c = c[:600] + "…"
        lines.append(f"{who}: {c}")
    return (
        "[CONVERSATION SO FAR — context; the customer is continuing this thread]\n"
        + "\n".join(lines) + "\n\n"
        "[CURRENT MESSAGE — answer THIS, using the thread above to resolve "
        'references like "it", "that", "tell me more"]\n'
        + last
    )


def _derive_session_id(body: dict, messages: list) -> str:
    """Stable session id so a multi-turn PHP convo threads server-side.

    Priority: explicit body.session_id → OpenAI 'user' field → hash of prior
    turns (so resending history reuses the session) → fresh uuid for a brand
    new single-message call.
    """
    sid = (body.get("session_id") or body.get("user") or "").strip()
    if sid:
        return f"api-{sid}"[:64]
    if len(messages) > 1:
        h = hashlib.sha1(
            _json.dumps(messages[:-1], sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        return f"api-{h}"
    return f"api-{uuid4().hex[:16]}"


async def _stream_raw_content(orig_request: Request, user: dict, slug: str,
                              message: str, session_id: str, reasoning: str,
                              scope, emit_steps: bool = False) -> AsyncIterator:
    """Shared core: invoke the real chat pipeline via an internal Request and
    yield RAW answer content pieces (TeamRunContent / RunContent deltas) as they
    arrive. Mirrors projects.py full_content. Used by BOTH the blocking path
    (join → clean) and the streaming path (incremental clean → chunk).

    Yields `str` for answer content. If `emit_steps` is True, ALSO yields
    `{"_step": {"label","icon"}}` dicts for tool/reasoning activity (the live
    "what the agent is doing" strip). emit_steps is OFF for the OpenAI v1
    contract (external clients get answer-only) and only turned on for the
    internal admin Console via the `X-Agent-Steps` opt-in header.

    The store scope is set HERE (inside the generator) and reset in `finally`,
    because for the streaming path this generator runs AFTER the handler has
    returned — so the scope must live for the whole drain, not just the handler
    call. `scope` is the resolved StoreScope (from resolve_api_scope).
    """
    from app.projects import project_chat
    from dash.api_scope import API_STORE_SCOPE

    form = {
        # Prepend the API-mode style directive so the agent emits a clean,
        # self-contained answer (no SOURCES/WHY/KPI-snapshot scaffolding).
        "message": _API_STYLE_DIRECTIVE + message,
        "stream": "true",          # pipeline always streams; we drain it here
        "session_id": session_id,
        "reasoning": reasoning or "auto",
    }
    body_bytes = urlencode(form).encode()

    # Internal ASGI scope: carry app/state, pre-auth via state.user (the chat
    # endpoint's _get_user reads request.state.user, not the header).
    asgi_scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": getattr(orig_request, "scope", {}).get("scheme", "https"),
        "path": f"/api/projects/{slug}/chat",
        "raw_path": f"/api/projects/{slug}/chat".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body_bytes)).encode()),
        ],
        "app": orig_request.scope.get("app"),
        "state": {"user": user},
        "client": orig_request.scope.get("client"),
        "server": orig_request.scope.get("server"),
    }

    _sent = False

    async def receive():
        nonlocal _sent
        if not _sent:
            _sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    internal_req = StarletteRequest(asgi_scope, receive)

    # Scope lives for the WHOLE drain (the generator outlives the handler in the
    # streaming case), so set/reset it here, not in the handler.
    _tok = API_STORE_SCOPE.set(scope)
    try:
        resp = await project_chat(slug, internal_req)

        body_iter = getattr(resp, "body_iterator", None)
        if body_iter is None:
            return

        # Drain SSE — yield content from TeamRunContent / RunContent events,
        # plus (when emit_steps) a compact activity step per tool/reasoning event.
        _step_set = (
            "ToolCallStarted", "TeamToolCallStarted",
            "ReasoningStep", "TeamReasoningStep", "ReasoningStarted",
        )
        _last_step = ""
        if emit_steps:
            try:
                from app.embed_public import _step_label as _mk_step
            except Exception:
                _mk_step = None
        # ANTI-DOUBLE: in coordinate mode the analyst MEMBER streams the real
        # answer as `RunContent`, then the team LEADER re-synthesizes it as
        # `TeamRunContent` (an echo). Concatenating both = the answer printed
        # twice. So: stream member `RunContent` live; BUFFER leader
        # `TeamRunContent`; at end, drop the leader echo IF the member already
        # answered, else flush the buffered leader (bare-agent / team-of-one,
        # where TeamRunContent IS the only answer).
        _member_emitted = False
        _leader_buf: list[str] = []
        buf = ""
        cur_event = ""
        async for chunk in body_iter:
            buf += chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                cur_event = ""
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        cur_event = line[6:].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].strip()
                        if cur_event in ("TeamRunContent", "RunContent") and raw:
                            try:
                                d = _json.loads(raw)
                                c = d.get("content")
                                if isinstance(c, str) and c:
                                    if cur_event == "TeamRunContent":
                                        _leader_buf.append(c)      # echo candidate
                                    else:
                                        _member_emitted = True     # member = real answer
                                        yield c
                            except Exception:
                                pass
                        elif emit_steps and _mk_step and cur_event in _step_set and raw:
                            try:
                                d = _json.loads(raw)
                                label, icon = _mk_step(cur_event, d)
                                if label and label != _last_step:
                                    _last_step = label
                                    yield {"_step": {"label": label, "icon": icon}}
                            except Exception:
                                pass
        # Flush the buffered leader answer ONLY when no member answer streamed
        # (bare agent / team-of-one). Otherwise it's the duplicate echo → drop.
        if _leader_buf and not _member_emitted:
            yield "".join(_leader_buf)
        try:
            logger.info("apigw drain: member_emitted=%s leader_chars=%d (echo %s)",
                        _member_emitted, sum(len(x) for x in _leader_buf),
                        "dropped" if (_leader_buf and _member_emitted) else "n/a")
        except Exception:
            pass
    finally:
        API_STORE_SCOPE.reset(_tok)


async def _run_and_collect(orig_request: Request, user: dict, slug: str,
                           message: str, session_id: str, reasoning: str,
                           scope) -> str:
    """Blocking path: drain the shared generator and return the concatenated
    raw answer text. Caller runs `_clean_answer` on the result."""
    parts: list[str] = []
    async for piece in _stream_raw_content(
        orig_request, user, slug, message, session_id, reasoning, scope
    ):
        if isinstance(piece, str):     # blocking path never requests steps, but guard
            parts.append(piece)
    return "".join(parts)


def _safe_raw_prefix(raw_buffer: str) -> str:
    """Return the longest prefix of `raw_buffer` that contains no *open* (yet
    unclosed) directive bracket. A directive tag like `[NARRATION: ...]` may
    arrive split across SSE deltas — if we cleaned the whole buffer we'd leak
    the half-open `[NARR...` as literal text. So we hold everything from the
    last unmatched `[` onward until its closing `]` arrives in a later delta."""
    last_open = raw_buffer.rfind("[")
    if last_open == -1:
        return raw_buffer
    if "]" in raw_buffer[last_open:]:
        return raw_buffer            # last bracket is already closed → all safe
    return raw_buffer[:last_open]    # hold the dangling open tag


def _pending_meta_hold(safe: str) -> int:
    """If the trailing (still-incomplete) line could be the start of a dashboard
    meta heading (SOURCES / WHY / KPI snapshot / related), return how many chars
    to hold back so the streaming cleaner doesn't leak a half-written heading
    before `_strip_api_sections` can cut it. 0 = nothing to hold."""
    nl = safe.rfind("\n")
    tail = safe[nl + 1:] if nl != -1 else safe
    bare = tail.strip().lstrip("#").strip().strip("*").strip().rstrip(":").strip().lower()
    if not bare:
        return 0
    if any(h.startswith(bare) for h in _API_META_HEADINGS):
        return len(tail)
    return 0


def _incremental_clean(raw_buffer: str, already_emitted: str) -> str:
    """Incremental delta cleaner for the streaming path.

    The raw stream carries exec-layout directive tags ([NARRATION:…], [KPI:…],
    etc.) that arrive across multiple deltas, so we cannot clean a single delta
    in isolation (a tag may be half-open). Approach:

      1. Hold back any dangling open `[` tag via `_safe_raw_prefix` so we never
         emit a half-written directive as literal text.
      2. Clean that safe prefix with the same `_clean_answer` the blocking path
         uses, then return only the NEW SUFFIX vs what was already emitted.

    Tradeoff: `_clean_answer` runs once per delta on the whole safe prefix
    (O(n) per delta → O(n²) over a full response — fine for chat-sized answers).
    It is also not strictly append-only: a `[KPI: v|label|d]` collapses to
    `label: v` only once the tag closes, so the visible text up to an open tag
    is stable but a just-closed tag can rewrite the tail. We therefore only emit
    when the freshly-cleaned text still has `already_emitted` as a prefix; if it
    diverged we hold this delta and resync on a later one. The final
    blocking-style `_clean_answer` on the FULL raw buffer (in `_stream_completion`)
    is the source of truth and flushes any residual, so the client's total
    streamed text equals the blocking answer by end of stream.
    """
    safe = _safe_raw_prefix(raw_buffer)
    hold = _pending_meta_hold(safe)
    if hold:
        safe = safe[:len(safe) - hold]
    cleaned = _strip_api_sections(_clean_answer(safe))
    if not already_emitted:
        return cleaned
    if cleaned.startswith(already_emitted):
        return cleaned[len(already_emitted):]
    # Cleaned text no longer extends what we emitted (a closing tag rewrote an
    # earlier segment). Hold this delta; a later delta resyncs once it settles.
    return ""


def _estimate_tokens(text: str) -> int:
    # ~4 chars/token rough fallback when the pipeline didn't surface usage.
    return max(1, len(text or "") // 4)


def _chunk_frame(chat_id: str, created: int, model: str, *,
                 delta: dict, finish_reason: Optional[str] = None) -> str:
    """Serialize one OpenAI chat.completion.chunk SSE frame."""
    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_completion(orig_request: Request, user: dict, slug: str,
                             message: str, session_id: str, reasoning: str,
                             scope, model: str, emit_steps: bool = False,
                             log_message: str | None = None) -> AsyncIterator[str]:
    """Yield OpenAI chat.completion.chunk SSE frames, incrementally cleaned.

    First chunk delta = {"role": "assistant"} (OpenAI convention). Content
    deltas carry cleaned suffixes. Final chunk = empty delta with
    finish_reason="stop", then the `data: [DONE]` sentinel.

    When `emit_steps`, tool/reasoning activity is interleaved as chunk frames
    whose delta carries a NON-STANDARD `x_agent_step` field (no `content`).
    Official OpenAI SDKs read only `delta.content` and ignore unknown keys, so
    the v1 contract is preserved for external clients — the Console opts in via
    the `X-Agent-Steps` header to render the live activity strip.
    """
    chat_id = f"chatcmpl-{uuid4().hex[:24]}"
    _t0 = time.time()
    created = int(_t0)
    # `message` is the agent input (may carry a context preamble / intent hint);
    # `_lm` is the PURE last question — use it for the warm-voice drug-name
    # fallback, token estimate, and body-logging so the Questions log + humanizer
    # never see the [CONVERSATION SO FAR] scaffolding.
    _lm = log_message if log_message is not None else message

    # Role-only opening chunk.
    yield _chunk_frame(chat_id, created, model, delta={"role": "assistant"})

    # Store keys get the warm-chemist humanizer, which rebuilds the answer from
    # the parsed table (drops ## / ### scaffolding, Key Findings, Notable
    # Products, wrong totals). That reshape can only run on the COMPLETE text, so
    # for store keys we BUFFER the answer and emit it once at the end. Activity
    # steps still stream live, so the Console strip keeps moving. Global/BI keys
    # keep true token-by-token streaming.
    _humanize = (user or {}).get("scope_mode") == "store"

    raw_buffer = ""
    emitted = ""
    try:
        try:
            async for piece in _stream_raw_content(
                orig_request, user, slug, message, session_id, reasoning, scope,
                emit_steps=emit_steps,
            ):
                if isinstance(piece, dict):       # activity step (Console strip)
                    step = piece.get("_step")
                    if step:
                        yield _chunk_frame(chat_id, created, model,
                                           delta={"x_agent_step": step})
                    continue
                raw_buffer += piece
                if _humanize:
                    continue                      # hold — reshape at the end
                new_text = _incremental_clean(raw_buffer, emitted)
                if new_text:
                    emitted += new_text
                    yield _chunk_frame(chat_id, created, model,
                                       delta={"content": new_text})
        except Exception:
            logger.exception("api_gateway: streaming drain failed")
            # Fall through to a clean close so the client still gets [DONE].

        # Final answer text: clean → strip meta → (store keys) warm-humanize.
        final_clean = _strip_api_sections(_clean_answer(raw_buffer))
        if _humanize:
            try:
                final_clean = _humanize_api_answer(final_clean, _lm)
            except Exception:
                logger.debug("apigw: stream humanize skipped", exc_info=True)
            final_clean = _sanitize_for_scope(final_clean, user)
            if final_clean:
                emitted = final_clean
                yield _chunk_frame(chat_id, created, model,
                                   delta={"content": final_clean})
        elif final_clean.startswith(emitted) and len(final_clean) > len(emitted):
            # Emit any residual the running cleaner missed (last delta diverged).
            tail = final_clean[len(emitted):]
            emitted += tail
            yield _chunk_frame(chat_id, created, model, delta={"content": tail})

        # Terminal chunk + DONE sentinel.
        yield _chunk_frame(chat_id, created, model, delta={},
                           finish_reason="stop")
        yield "data: [DONE]\n\n"
    finally:
        # Sanitizer on the FULLY-accumulated answer. NOTE: applying mid-stream
        # is infeasible (chunks are already emitted), so for the streaming path
        # we only run the leak detector on the complete text to log telemetry —
        # we do NOT retroactively edit chunks the client already received.
        full = _clean_answer(raw_buffer)
        _sanitize_for_scope(full, user)
        # Usage metering (fail-soft). Token estimate from message + emitted.
        _log_usage(user, model, _estimate_tokens(_lm),
                   _estimate_tokens(emitted), streamed=True,
                   session_id=session_id,
                   latency_ms=int((time.time() - _t0) * 1000))
        _log_bodies(user, session_id, _lm, emitted, masked=False)


@router.get("/models")
def list_models():
    """OpenAI-compatible model list — one virtual model."""
    return {
        "object": "list",
        "data": [{
            "id": MODEL_ID,
            "object": "model",
            "created": 0,
            "owned_by": "citypharma",
        }],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completion. Blocking (stream=false) or SSE
    (stream=true → text/event-stream of chat.completion.chunk frames)."""
    user = _require_user(request)
    _t0 = time.time()

    # Per-key sliding-window throttle (store service keys only). Hard 429.
    try:
        _rate_check(user)
    except HTTPException as e:
        if e.status_code == 429:
            _log_usage(user, "", 0, 0, streamed=False, status="rate_limited")
            _sec_event("rate_limited", user, "per-key rate cap exceeded")
        raise

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON.")
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object.")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "'messages' (non-empty array) is required.")

    message = _last_user_message(messages)
    if not message:
        raise HTTPException(400, "No user message found in 'messages'.")
    if len(message) > 50000:
        raise HTTPException(413, "Message too long (max 50000 chars).")

    session_id = _derive_session_id(body, messages)
    slug = _locked_slug()
    # API default = concise (quick tier → 1 answer, no exec-card scaffolding).
    # Callers can still opt into depth by passing reasoning="deep"/"auto".
    reasoning = str(body.get("reasoning") or "fast")
    model = body.get("model") or MODEL_ID

    # (A) Multi-turn context: feed the agent the prior thread, not just the last
    # line, so "tell me more" / "what about it" resolve. `message` stays the pure
    # last question (used for fallback drug-extraction, humanize, token est).
    agent_message = _build_agent_message(messages, message)
    # (B) Knowledge-intent steering: an "about / uses / more" question must hit
    # the indications/knowledge path with real reasoning, not a reflexive
    # stock_check. Only override when the caller didn't pick a tier explicitly.
    _knowledge = (not body.get("reasoning")) and _is_knowledge_intent(message)
    if _knowledge:
        if reasoning == "fast":
            reasoning = "auto"
        agent_message = (
            "[INTENT: information request — the user wants to know ABOUT the drug "
            "(uses, indications, salt, differences), not just a stock count. "
            "Answer with pharmacist knowledge and the indications/alternatives "
            "tool when useful; only show a stock table if they ask about "
            "stock/availability.]\n\n" + agent_message
        )

    # Resolve the store scope for this run so data tools can enforce masking.
    # NOTE: the scope is set/reset INSIDE `_stream_raw_content` (the shared
    # generator), not here — for the streaming path this handler returns before
    # the drain runs, so the scope must live with the generator, not the request.
    from app.auth import resolve_api_scope
    scope = resolve_api_scope(user)

    # --- Streaming path: OpenAI-style SSE of chat.completion.chunk frames ---
    if body.get("stream"):
        # Internal Console opt-in: when X-Agent-Steps:1 (or body x_agent_steps),
        # interleave activity-step chunks. External OpenAI clients never send it
        # → byte-identical answer-only stream (v1 contract preserved).
        emit_steps = (
            request.headers.get("x-agent-steps") == "1"
            or bool(body.get("x_agent_steps"))
        )
        return StreamingResponse(
            _stream_completion(
                request, user, slug, agent_message, session_id, reasoning, scope, model,
                emit_steps=emit_steps, log_message=message,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",   # disable nginx/proxy buffering
                "x-session-id": session_id,
            },
        )

    # --- Blocking path ---------------------------------------------------
    # Feature #2 — RESPONSE CACHE: check Redis before running the agent.
    # Cache is keyed by (username + scope_mode + store_id + message + model)
    # so one outlet can never receive another outlet's cached masked answer.
    _cache_hit = _cache_get(user, agent_message, model)
    if _cache_hit is not None:
        logger.debug("apigw: cache HIT for user=%s", (user or {}).get("username"))
        # Still meter usage on cache hits so the usage dashboard reflects traffic.
        _log_usage(user, model,
                   _cache_hit.get("usage", {}).get("prompt_tokens", 0),
                   _cache_hit.get("usage", {}).get("completion_tokens", 0),
                   streamed=False, status="ok", session_id=session_id,
                   request_type="cache_hit")
        return JSONResponse(_cache_hit, headers={"X-Cache": "HIT"})

    # --- Run the agent (with LLM-down fallback on error) -----------------
    # Feature #5 — LLM-DOWN FALLBACK: if _run_and_collect raises, try a
    # direct stock_check() lookup for simple stock questions before giving up.
    _agent_error: Optional[Exception] = None
    answer_raw = ""
    try:
        answer_raw = await _run_and_collect(
            request, user, slug, agent_message, session_id, reasoning, scope,
        )
    except Exception as _exc:
        _agent_error = _exc
        logger.warning("apigw: agent call failed (%s), checking fallback", _exc)

    if _agent_error is not None:
        # Attempt the no-LLM stock-lookup fallback (blocking, fail-soft).
        _fb = await _llm_down_fallback(user, message, model, session_id)
        if _fb is not None:
            logger.info("apigw: LLM-down fallback answered for user=%s",
                        (user or {}).get("username"))
            _log_usage(user, model,
                       _fb.get("usage", {}).get("prompt_tokens", 0),
                       _fb.get("usage", {}).get("completion_tokens", 0),
                       streamed=False, status="fallback", session_id=session_id)
            return JSONResponse(_fb, headers={"X-Cache": "MISS", "X-Fallback": "1"})
        # Fallback also failed — re-raise original error so the caller gets a 500.
        raise _agent_error

    # The agent often SWALLOWS a stripped-SQL failure and RETURNS it as text
    # ("I couldn't query the database… encountered an error") instead of raising.
    # Detect that self-reported failure on a store key and recover via the direct
    # stock_check fallback so a medicine-browse question still gets a real answer.
    if _DB_FAIL_RE.search(answer_raw or "") and _is_stock_question(message):
        _fb = await _llm_down_fallback(user, message, model, session_id)
        if _fb is not None:
            logger.info("apigw: recovered swallowed DB-fail via fallback for user=%s",
                        (user or {}).get("username"))
            _log_usage(user, model,
                       _fb.get("usage", {}).get("prompt_tokens", 0),
                       _fb.get("usage", {}).get("completion_tokens", 0),
                       streamed=False, status="fallback", session_id=session_id)
            return JSONResponse(_fb, headers={"X-Cache": "MISS", "X-Fallback": "swallowed"})

    answer = _clean_answer(answer_raw)
    # Drop any residual dashboard meta block (SOURCES/WHY/KPI snapshot/related).
    answer = _strip_api_sections(answer)
    # Belt-and-suspenders cross-store leak guard (store-locked keys only).
    answer = _sanitize_for_scope(answer, user)
    # Warm counter-pharmacist voice for store keys (friendly table, correct tip,
    # no model-computed Total/Summary block). Global/BI keys keep analyst format.
    if (user or {}).get("scope_mode") == "store":
        try:
            answer = _humanize_api_answer(answer, message)
        except Exception:
            logger.debug("apigw: humanize skipped", exc_info=True)

    prompt_toks = _estimate_tokens(message)
    completion_toks = _estimate_tokens(answer)

    # Usage metering (fail-soft) — blocking path. Real cost/latency live on the
    # chat trace (see v_usage_unified); this row keeps request/token telemetry.
    _lat = int((time.time() - _t0) * 1000)
    _log_usage(user, model, prompt_toks, completion_toks, streamed=False,
               session_id=session_id, latency_ms=_lat)
    try:
        from sqlalchemy import text as _t
        from db.session import get_write_engine as _gwe
        with _gwe().connect() as _c:
            _c.execute(_t("UPDATE public.dash_apigw_usage SET latency_ms=:l "
                          "WHERE id = (SELECT max(id) FROM public.dash_apigw_usage "
                          "WHERE key_id=:k)"), {"l": _lat, "k": _user_key_id(user)})
            _c.commit()
    except Exception:
        pass
    _log_bodies(user, session_id, message, answer, masked=False)

    response_payload = {
        "id": f"chatcmpl-{uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_toks,
            "completion_tokens": completion_toks,
            "total_tokens": prompt_toks + completion_toks,
        },
        # Non-standard extras (OpenAI clients ignore unknown fields):
        "x_session_id": session_id,
    }

    # Feature #2 — store the response in Redis for future cache hits.
    _cache_set(user, agent_message, model, response_payload)

    return JSONResponse(response_payload, headers={"X-Cache": "MISS"})


_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CityPharma API — Developer Guide</title>
<style>
  :root{--ink:#1a1614;--muted:#6b6557;--accent:#c96342;--bg:#f8f5f0;--surface:#fff;
        --border:#e2ddd7;--code-bg:#1a1614;--code-fg:#e8e3d6;--radius:6px}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:var(--bg);color:var(--ink);line-height:1.6;font-size:14px}
  .wrap{max-width:860px;margin:0 auto;padding:48px 24px 80px}
  h1{font-size:26px;font-weight:700;margin-bottom:4px}
  .sub{color:var(--muted);font-size:13px;margin-bottom:40px}
  h2{font-size:16px;font-weight:600;margin:32px 0 10px;padding-bottom:6px;
     border-bottom:1px solid var(--border)}
  h3{font-size:13px;font-weight:600;margin:20px 0 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
  p{margin-bottom:12px;color:var(--ink)}
  ul{padding-left:20px;margin-bottom:12px}
  li{margin-bottom:4px}
  pre{background:var(--code-bg);color:var(--code-fg);padding:16px;border-radius:var(--radius);
      overflow-x:auto;font-size:12.5px;line-height:1.55;margin-bottom:16px;font-family:"SF Mono",Consolas,monospace}
  code{background:rgba(201,99,66,.1);color:var(--accent);padding:1px 5px;border-radius:3px;font-size:12.5px;font-family:"SF Mono",Consolas,monospace}
  .badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
  .get{background:#e8f5e9;color:#2e7d32}.post{background:#fff3e0;color:#e65100}
  .tier{border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;margin-bottom:8px}
  .tier strong{display:block;margin-bottom:2px}
  .ok{color:#2e7d32}.no{color:#c62828}
  table{width:100%;border-collapse:collapse;margin-bottom:16px;font-size:13px}
  th{text-align:left;padding:6px 10px;background:var(--bg);font-weight:600;font-size:11px;
     text-transform:uppercase;letter-spacing:.04em;color:var(--muted);border-bottom:1px solid var(--border)}
  td{padding:6px 10px;border-bottom:1px solid var(--border)}
  tr:last-child td{border-bottom:none}
  .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
  .store{background:#fff3e0;color:#e65100}.global{background:#e8f5e9;color:#2e7d32}
  .nav{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);
       padding:12px 24px;display:flex;gap:20px;font-size:12px;overflow-x:auto}
  .nav a{color:var(--muted);text-decoration:none;white-space:nowrap}
  .nav a:hover{color:var(--accent)}
</style>
</head>
<body>
<div class="nav">
  <a href="#quickstart">Quickstart</a>
  <a href="#auth">Auth</a>
  <a href="#endpoints">Endpoints</a>
  <a href="#blocking">Blocking</a>
  <a href="#streaming">Streaming</a>
  <a href="#access">Access model</a>
  <a href="#ratelimit">Rate limit</a>
  <a href="#errors">Errors</a>
  <a href="#examples">Examples</a>
</div>
<div class="wrap">
  <h1>CityPharma API</h1>
  <p class="sub">OpenAI-compatible gateway · base_url = <code>https://&lt;host&gt;/api/v1</code></p>

  <h2 id="quickstart">Quickstart (30 seconds)</h2>
  <pre>curl https://&lt;host&gt;/api/v1/chat/completions \\
  -H "Authorization: Bearer dash-key-XXXX" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "citypharma-analyst",
    "messages": [{"role": "user", "content": "is paracetamol in stock at my branch?"}]
  }'</pre>
  <p>Any standard OpenAI client works — just swap <code>base_url</code> and <code>api_key</code>.</p>

  <h2 id="auth">Authentication</h2>
  <p>Send your key as a Bearer token in every request:</p>
  <pre>Authorization: Bearer dash-key-XXXX</pre>
  <p>Keys are issued by your CityPharma administrator via the Gateway UI (<code>/ui/gateway</code> → Service Keys).<br>
  Two scope modes:</p>
  <ul>
    <li><span class="pill store">store</span> — bound to one outlet (or a set). Sees full stock + cost for own store; availability-only for others.</li>
    <li><span class="pill global">global</span> — no masking. Internal / BI tools only. Not issued to stores.</li>
  </ul>

  <h2 id="endpoints">Endpoints</h2>
  <table>
    <tr><th>Method</th><th>Path</th><th>Description</th></tr>
    <tr><td><span class="badge get">GET</span></td><td><code>/api/v1/models</code></td><td>List available models</td></tr>
    <tr><td><span class="badge post">POST</span></td><td><code>/api/v1/chat/completions</code></td><td>Chat completion (blocking or streaming)</td></tr>
    <tr><td><span class="badge get">GET</span></td><td><code>/api/v1/docs</code></td><td>This page</td></tr>
  </table>
  <p>There is one virtual model: <code>citypharma-analyst</code> (or omit <code>model</code> — it defaults).</p>

  <h2 id="blocking">Blocking request</h2>
  <pre>POST /api/v1/chat/completions
Content-Type: application/json

{
  "model": "citypharma-analyst",
  "messages": [
    {"role": "user", "content": "how much paracetamol do we have?"}
  ]
}</pre>
  <h3>Response</h3>
  <pre>{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1748000000,
  "model": "citypharma-analyst",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Total stock: 928 units ..."},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 12, "completion_tokens": 48, "total_tokens": 60},
  "x_session_id": "api-a1b2c3d4"
}</pre>
  <p>Pass <code>x_session_id</code> back as <code>"user": "&lt;session_id&gt;"</code> on follow-up turns to thread the conversation.</p>

  <h2 id="streaming">Streaming (SSE)</h2>
  <p>Add <code>"stream": true</code>. Response is <code>text/event-stream</code> of OpenAI <code>chat.completion.chunk</code> frames, ending with <code>data: [DONE]</code>.</p>
  <pre>POST /api/v1/chat/completions
Content-Type: application/json

{
  "model": "citypharma-analyst",
  "stream": true,
  "messages": [{"role": "user", "content": "list low stock items"}]
}

---

data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"}}]}
data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","choices":[{"delta":{"content":"Low stock"}}]}
data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","choices":[{"delta":{"content":" at your branch:"}}]}
...
data: {"id":"chatcmpl-xyz","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]</pre>

  <h2 id="access">Access model (3 tiers)</h2>
  <div class="tier">
    <strong>Tier 1 — Own store</strong>
    Row's <code>site_code</code> is in your bound store set.<br>
    <span class="ok">✓</span> Full data: stock qty, cost, price, sales value.
  </div>
  <div class="tier">
    <strong>Tier 2 — Other store</strong>
    Row's <code>site_code</code> is NOT in your bound store set.<br>
    <span class="no">✗</span> Qty + cost stripped. Availability only ("in stock at site X").
  </div>
  <div class="tier">
    <strong>Tier 3 — Reference / global</strong>
    No <code>site_code</code> (drug catalog, substitutes, indications).<br>
    <span class="ok">✓</span> Always visible, no masking.
  </div>
  <p>Raw SQL access is physically absent from store-scoped keys. Scoped pharma tools (<code>stock_check</code>, <code>find_substitutes</code>, <code>alternatives_for_indication</code>) are the only data path — they enforce the tier rules at query time.</p>

  <h2 id="ratelimit">Rate limit</h2>
  <p>Store-scoped keys: <strong>60 requests / minute</strong> by default (Redis fixed-window, global across all workers).<br>
  Cap is live-editable by your admin without a restart.<br>
  Global-scope keys: no rate limit.</p>
  <table>
    <tr><th>Header</th><th>Meaning</th></tr>
    <tr><td><code>Retry-After: 60</code></td><td>Returned on 429. Wait this many seconds.</td></tr>
  </table>

  <h2 id="errors">Errors</h2>
  <table>
    <tr><th>Status</th><th>When</th></tr>
    <tr><td><code>400</code></td><td>Bad request — missing <code>messages</code>, empty user turn, body not JSON</td></tr>
    <tr><td><code>401</code></td><td>Missing or invalid API key</td></tr>
    <tr><td><code>413</code></td><td>Message too long (max 50 000 chars)</td></tr>
    <tr><td><code>429</code></td><td>Rate limit exceeded. Check <code>Retry-After</code> header.</td></tr>
    <tr><td><code>500</code></td><td>Internal error — retryable</td></tr>
  </table>

  <h2 id="examples">Code examples</h2>
  <h3>PHP (cURL)</h3>
  <pre>&lt;?php
$ch = curl_init("https://&lt;host&gt;/api/v1/chat/completions");
curl_setopt_array($ch, [
  CURLOPT_RETURNTRANSFER =&gt; true,
  CURLOPT_HTTPHEADER =&gt; [
    "Authorization: Bearer dash-key-XXXX",
    "Content-Type: application/json",
  ],
  CURLOPT_POSTFIELDS =&gt; json_encode([
    "model"    =&gt; "citypharma-analyst",
    "messages" =&gt; [["role" =&gt; "user", "content" =&gt; "is amoxicillin in stock?"]],
  ]),
]);
$resp = json_decode(curl_exec($ch), true);
echo $resp["choices"][0]["message"]["content"];</pre>

  <h3>PHP (openai-php/client)</h3>
  <pre>&lt;?php
// composer require openai-php/client guzzlehttp/guzzle
$client = OpenAI::factory()
    -&gt;withBaseUri("https://&lt;host&gt;/api/v1")
    -&gt;withApiKey("dash-key-XXXX")
    -&gt;make();

$res = $client-&gt;chat()-&gt;create([
    "model"    =&gt; "citypharma-analyst",
    "messages" =&gt; [["role" =&gt; "user", "content" =&gt; "substitutes for PANADOL?"]],
]);
echo $res-&gt;choices[0]-&gt;message-&gt;content;</pre>

  <h3>Python</h3>
  <pre># pip install openai
from openai import OpenAI

client = OpenAI(base_url="https://&lt;host&gt;/api/v1", api_key="dash-key-XXXX")
res = client.chat.completions.create(
    model="citypharma-analyst",
    messages=[{"role": "user", "content": "low stock antibiotics at my branch"}],
)
print(res.choices[0].message.content)</pre>

  <h3>Python streaming</h3>
  <pre>for chunk in client.chat.completions.create(
    model="citypharma-analyst",
    messages=[{"role": "user", "content": "show me paracetamol stock"}],
    stream=True,
):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)</pre>

  <h3>Node.js</h3>
  <pre>// npm i openai
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "https://&lt;host&gt;/api/v1", apiKey: "dash-key-XXXX" });
const res = await client.chat.completions.create({
  model: "citypharma-analyst",
  messages: [{ role: "user", content: "what antibiotics do we carry?" }],
});
console.log(res.choices[0].message.content);</pre>

  <h3>Multi-turn conversation</h3>
  <pre>// Send x_session_id back as "user" field to thread the conversation
const turn1 = await client.chat.completions.create({
  model: "citypharma-analyst",
  messages: [{ role: "user", content: "is ibuprofen in stock?" }],
});
const sessionId = turn1.x_session_id;

const turn2 = await client.chat.completions.create({
  model: "citypharma-analyst",
  user: sessionId,
  messages: [
    { role: "user",      content: "is ibuprofen in stock?" },
    { role: "assistant", content: turn1.choices[0].message.content },
    { role: "user",      content: "what about paracetamol?" },
  ],
});</pre>
</div>
</body>
</html>"""


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def api_docs():
    """Developer integration guide — human-readable HTML, no auth required."""
    return HTMLResponse(_DOCS_HTML)
