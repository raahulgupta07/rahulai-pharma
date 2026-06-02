"""Redis cache layer for embed chat endpoint.

Caches `/api/embed/chat` answers keyed by `(embed_id, site_id, normalized_msg)`.
Common pharma queries ("availability of paracetamol", "Dazit equivalents") get
asked by 100+ shops daily — expected cache hit ratio 30-50%.

Design rules:
- Lazy singleton Redis client (connection pool, fail-soft on connect error)
- Every method wrapped in try/except — Redis down NEVER breaks the chat
- Cache key MUST include site_id (cross-tenant leak prevention)
- TTL default 300s (stock changes within 5min, long enough to be effective)
- LRU eviction at 256MB (configured at server level)
- `EMBED_CACHE_DISABLED=1` env bypass for debugging
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import string
from typing import Any

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"[" + re.escape(string.punctuation) + r"]+")
_WS_RE = re.compile(r"\s+")

_KEY_PREFIX = "embed:chat:"
_HIT_COUNTER = "embed:cache:hits"
_MISS_COUNTER = "embed:cache:misses"

_DEFAULT_REDIS_URL = "redis://dash-redis:6379/2"
_DEFAULT_TTL = 300

# Lazy singleton.
_client: Any = None
_client_init_attempted = False


def _disabled() -> bool:
    return str(os.environ.get("EMBED_CACHE_DISABLED", "")).strip().lower() in ("1", "true", "yes")


def _get_client():
    """Lazy-init Redis singleton with connection pool. Returns None on any error."""
    global _client, _client_init_attempted
    if _disabled():
        return None
    if _client is not None:
        return _client
    if _client_init_attempted:
        return _client  # still None — don't keep retrying tight-loop
    _client_init_attempted = True
    try:
        import redis  # type: ignore
        url = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        # Probe — fail-soft.
        try:
            _client.ping()
        except Exception as e:
            logger.warning("embed_cache: redis ping failed (%s) — caching disabled", e)
            _client = None
    except Exception as e:
        logger.warning("embed_cache: redis client init failed (%s) — caching disabled", e)
        _client = None
    return _client


def normalize(msg: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation."""
    if not msg:
        return ""
    s = msg.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def cache_key(embed_id: str, site_id: str, msg: str) -> str:
    """SHA256 of `embed_id|site_id|normalize(msg)`, truncated to 16 chars + prefix."""
    raw = f"{embed_id}|{site_id}|{normalize(msg)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{_KEY_PREFIX}{digest}"


def get(key: str) -> dict | None:
    """Read + JSON decode. Returns None on miss or any error."""
    c = _get_client()
    if c is None:
        return None
    try:
        raw = c.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("embed_cache.get failed: %s", e)
        return None


def set(key: str, val: dict, ttl: int | None = None) -> bool:  # noqa: A001 — match spec
    """JSON encode + SETEX. Returns success bool."""
    c = _get_client()
    if c is None:
        return False
    try:
        if ttl is None:
            ttl = int(os.environ.get("EMBED_CACHE_TTL", _DEFAULT_TTL))
        payload = json.dumps(val, default=str)
        c.setex(key, int(ttl), payload)
        return True
    except Exception as e:
        logger.warning("embed_cache.set failed: %s", e)
        return False


def incr_hit() -> None:
    """Bump hit counter. Fail-soft."""
    c = _get_client()
    if c is None:
        return
    try:
        c.incr(_HIT_COUNTER)
    except Exception:
        pass


def incr_miss() -> None:
    """Bump miss counter. Fail-soft."""
    c = _get_client()
    if c is None:
        return
    try:
        c.incr(_MISS_COUNTER)
    except Exception:
        pass


def invalidate(embed_id: str, site_id: str | None = None) -> int:
    """SCAN + DEL all keys matching pattern. Returns count.

    Note: cache_key is sha256(embed_id|site_id|msg) so we cannot pattern-match
    on embed_id from the key alone. Instead we iterate ALL keys and rebuild
    the prefix to compare — but since keys are hashed, we cannot reverse them.

    Pragmatic approach: store a secondary index `embed:idx:{embed_id}` -> set
    of cache keys belonging to that embed. Invalidate scans the index set.
    """
    c = _get_client()
    if c is None:
        return 0
    try:
        if site_id:
            idx_key = f"embed:idx:{embed_id}:{site_id}"
        else:
            idx_key = f"embed:idx:{embed_id}"
        # Pattern: idx:{embed_id}* covers all sites if site_id is None.
        pattern = f"embed:idx:{embed_id}*" if site_id is None else idx_key
        deleted = 0
        for k in c.scan_iter(match=pattern, count=200):
            try:
                members = c.smembers(k)
                if members:
                    deleted += c.delete(*members) or 0
                c.delete(k)
            except Exception:
                continue
        return int(deleted)
    except Exception as e:
        logger.warning("embed_cache.invalidate failed: %s", e)
        return 0


def _index_key(key: str, embed_id: str, site_id: str) -> None:
    """Track cache key in per-embed/site index set (for invalidate). Fail-soft."""
    c = _get_client()
    if c is None:
        return
    try:
        idx = f"embed:idx:{embed_id}:{site_id}"
        c.sadd(idx, key)
        # Match TTL roughly to the key TTL so the index doesn't grow forever.
        ttl = int(os.environ.get("EMBED_CACHE_TTL", _DEFAULT_TTL))
        c.expire(idx, ttl * 2)
    except Exception:
        pass


def set_indexed(key: str, val: dict, embed_id: str, site_id: str, ttl: int | None = None) -> bool:
    """SET + index in per-embed/site set so invalidate() can find it."""
    ok = set(key, val, ttl=ttl)
    if ok:
        _index_key(key, embed_id, site_id)
    return ok


def stats() -> dict:
    """Return {hits, misses, hit_ratio, redis_connected}."""
    c = _get_client()
    if c is None:
        return {"hits": 0, "misses": 0, "hit_ratio": 0.0, "redis_connected": False}
    try:
        hits = int(c.get(_HIT_COUNTER) or 0)
        misses = int(c.get(_MISS_COUNTER) or 0)
        total = hits + misses
        ratio = (hits / total) if total > 0 else 0.0
        return {
            "hits": hits,
            "misses": misses,
            "hit_ratio": round(ratio, 4),
            "redis_connected": True,
        }
    except Exception as e:
        logger.warning("embed_cache.stats failed: %s", e)
        return {"hits": 0, "misses": 0, "hit_ratio": 0.0, "redis_connected": False}
