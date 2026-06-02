"""
Central OpenRouter client w/ multi-key rotation pool + retry + connection-pool bounded.

Inspired by OpenWebUI multi-key pattern (semicolon-separated keys, per-key 429 cooldown).
Replaces ad-hoc httpx.post(...openrouter.ai...) call sites that share a single key.

Public API:
    call_openrouter_sync(body: dict, *, timeout: int = 30) -> httpx.Response
    OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

Env vars:
    OPENROUTER_API_KEYS    semicolon-separated key list (preferred)
    OPENROUTER_API_KEY     fallback single key (back-compat)
    OPENROUTER_POOL_MAX_CONNECTIONS         default 100
    OPENROUTER_POOL_MAX_CONNECTIONS_PER_HOST default 30
    OPENROUTER_429_COOLDOWN_SECONDS         default 30
    OPENROUTER_MAX_RETRIES                  default 3
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

_LOG = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

_POOL_MAX = int(os.getenv("OPENROUTER_POOL_MAX_CONNECTIONS") or "100")
_POOL_PER_HOST = int(os.getenv("OPENROUTER_POOL_MAX_CONNECTIONS_PER_HOST") or "30")
_COOLDOWN_S = float(os.getenv("OPENROUTER_429_COOLDOWN_SECONDS") or "30")
_MAX_RETRIES = int(os.getenv("OPENROUTER_MAX_RETRIES") or "3")


@dataclass
class _KeyState:
    key: str
    in_flight: int = 0
    total_429: int = 0
    total_ok: int = 0
    cooldown_until: float = 0.0  # epoch seconds

    def available(self, now: float) -> bool:
        return now >= self.cooldown_until


_REFRESH_TTL_S = float(os.getenv("OPENROUTER_KEY_REFRESH_TTL_S") or "60")


class OpenRouterPool:
    """Multi-key round-robin pool with per-key 429 cooldown + DB hot-reload.

    Pick algorithm: among non-cooled-down keys, choose least-in-flight.
    On 429: mark key cooldown for _COOLDOWN_S seconds (or Retry-After if larger).
    All keys cooled → wait + retry on the soonest-available key.

    Key source priority on each refresh:
      1. DB (dash.dash_llm_keys WHERE enabled=TRUE)  — UI-managed
      2. Env OPENROUTER_API_KEYS (semicolon list)    — legacy
      3. Env OPENROUTER_API_KEY (single)             — legacy
    DB + env are merged (deduped); UI-managed keys preserve their in-flight counters
    across refreshes (keyed by plaintext) so cooldowns persist.
    """

    def __init__(self):
        self._keys: list[_KeyState] = []
        self._lock = threading.Lock()
        self._rr_idx = 0
        self._last_refresh = 0.0
        self._refresh_keys(force=True)

    def _load_env_keys(self) -> list[str]:
        raw = os.getenv("OPENROUTER_API_KEYS") or os.getenv("OPENROUTER_API_KEY") or ""
        return [k.strip() for k in raw.replace(",", ";").split(";") if k.strip()]

    def _load_db_keys(self) -> list[str]:
        try:
            from dash.admin.llm_keys import load_active_plaintext_keys
            return load_active_plaintext_keys()
        except Exception as e:
            _LOG.debug("OpenRouterPool: DB key load skipped: %s", e)
            return []

    def _refresh_keys(self, *, force: bool = False):
        """Reload keys from DB+env. Preserves _KeyState (cooldown, counters) across refreshes."""
        now = time.time()
        if not force and (now - self._last_refresh) < _REFRESH_TTL_S:
            return
        db_keys = self._load_db_keys()
        env_keys = self._load_env_keys()
        # dedupe, DB-first (UI wins)
        seen = set()
        ordered: list[str] = []
        for k in [*db_keys, *env_keys]:
            if k and k not in seen:
                seen.add(k)
                ordered.append(k)
        with self._lock:
            existing = {ks.key: ks for ks in self._keys}
            new_states: list[_KeyState] = []
            for k in ordered:
                if k in existing:
                    new_states.append(existing[k])  # preserve in_flight, cooldown, counters
                else:
                    new_states.append(_KeyState(key=k))
            prev_count = len(self._keys)
            self._keys = new_states
            self._last_refresh = now
            if len(new_states) != prev_count:
                _LOG.info("OpenRouterPool: refreshed %d key(s) (db=%d env=%d, was %d)",
                          len(new_states), len(db_keys), len(env_keys), prev_count)

    def has_keys(self) -> bool:
        self._refresh_keys()
        return bool(self._keys)

    def pick(self) -> Optional[_KeyState]:
        """Return next available key or None if all cooled down."""
        self._refresh_keys()
        with self._lock:
            if not self._keys:
                return None
            now = time.time()
            available = [k for k in self._keys if k.available(now)]
            if not available:
                return None
            # least in-flight; tie-break by round-robin
            available.sort(key=lambda k: (k.in_flight, self._keys.index(k)))
            chosen = available[0]
            chosen.in_flight += 1
            return chosen

    def soonest_cooldown(self) -> float:
        """Seconds until the soonest-available key. 0 if any available now."""
        with self._lock:
            now = time.time()
            if any(k.available(now) for k in self._keys):
                return 0.0
            if not self._keys:
                return 0.0
            return max(0.0, min(k.cooldown_until for k in self._keys) - now)

    def release(self, ks: _KeyState, *, success: bool, retry_after: Optional[float] = None, status: int = 0):
        with self._lock:
            ks.in_flight = max(0, ks.in_flight - 1)
            if status == 429:
                ks.total_429 += 1
                cd = retry_after if (retry_after and retry_after > _COOLDOWN_S) else _COOLDOWN_S
                ks.cooldown_until = time.time() + cd
                _LOG.warning("OpenRouter key 429 → cooldown %.0fs (total 429s: %d)", cd, ks.total_429)
            elif success:
                ks.total_ok += 1

    def stats(self) -> list[dict]:
        with self._lock:
            now = time.time()
            return [
                {
                    "key_suffix": ks.key[-6:],
                    "in_flight": ks.in_flight,
                    "total_ok": ks.total_ok,
                    "total_429": ks.total_429,
                    "cooldown_remaining_s": max(0.0, ks.cooldown_until - now),
                }
                for ks in self._keys
            ]


# Singletons
_POOL: Optional[OpenRouterPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> OpenRouterPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = OpenRouterPool()
    return _POOL


_SYNC_CLIENT: Optional[httpx.Client] = None
_SYNC_LOCK = threading.Lock()


def get_sync_client() -> httpx.Client:
    """Singleton httpx.Client with bounded connection pool.

    Replaces per-request httpx.post() construction.
    Cap is on sockets (httpx.Limits), not coroutines (asyncio.Semaphore).
    """
    global _SYNC_CLIENT
    if _SYNC_CLIENT is None:
        with _SYNC_LOCK:
            if _SYNC_CLIENT is None:
                limits = httpx.Limits(
                    max_connections=_POOL_MAX,
                    max_keepalive_connections=_POOL_PER_HOST,
                    keepalive_expiry=30.0,
                )
                _SYNC_CLIENT = httpx.Client(limits=limits, timeout=httpx.Timeout(60.0))
    return _SYNC_CLIENT


def _parse_retry_after(headers) -> Optional[float]:
    ra = headers.get("retry-after") or headers.get("Retry-After")
    if not ra:
        return None
    try:
        return float(ra)
    except (TypeError, ValueError):
        return None


def call_openrouter_sync(body: dict, *, timeout: int = 30, extra_headers: Optional[dict] = None) -> httpx.Response:
    """Sync POST to OpenRouter chat/completions with multi-key rotation + retry.

    - Rotates key from pool (least-in-flight, skips cooled-down keys).
    - On 429: marks key cooldown, retries on next available key.
    - On 5xx: 1 retry on a different key.
    - Exponential backoff with jitter between retries: 2^attempt + random(0..1) seconds.
    - Total attempts capped at _MAX_RETRIES.
    - Raises last httpx.Response if all attempts exhausted (caller checks status).

    body: full chat/completions payload (model, messages, etc).
    """
    pool = get_pool()
    client = get_sync_client()

    last_response: Optional[httpx.Response] = None
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        ks = pool.pick()
        if ks is None:
            # All keys cooled down — wait for soonest
            wait_s = min(pool.soonest_cooldown() + 0.5, 30.0)
            if wait_s > 0:
                _LOG.warning("OpenRouter: all keys cooled, waiting %.1fs", wait_s)
                time.sleep(wait_s)
                ks = pool.pick()
            if ks is None:
                # No keys at all configured
                raise RuntimeError("OpenRouter: no API keys configured (set OPENROUTER_API_KEY or OPENROUTER_API_KEYS)")

        headers = {
            "Authorization": f"Bearer {ks.key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = client.post(OPENROUTER_BASE, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            pool.release(ks, success=False, status=0)
            last_exc = e
            _LOG.warning("OpenRouter request error (attempt %d): %s", attempt + 1, e)
            if attempt < _MAX_RETRIES - 1:
                backoff = (2 ** attempt) + random.random()
                time.sleep(backoff)
            continue

        status = resp.status_code
        if status == 200:
            pool.release(ks, success=True, status=200)
            return resp
        if status == 429:
            ra = _parse_retry_after(resp.headers)
            pool.release(ks, success=False, status=429, retry_after=ra)
            last_response = resp
            if attempt < _MAX_RETRIES - 1:
                backoff = (2 ** attempt) + random.random()
                time.sleep(backoff)
            continue
        if 500 <= status < 600:
            pool.release(ks, success=False, status=status)
            last_response = resp
            if attempt < _MAX_RETRIES - 1:
                time.sleep(1.0 + random.random())
            continue
        # 4xx other than 429 — caller's problem, don't retry
        pool.release(ks, success=False, status=status)
        return resp

    if last_response is not None:
        return last_response
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter: retries exhausted with no response")


def inject_provider_fallback(body: dict, fallback_models: Optional[list[str]] = None) -> dict:
    """Inject OpenRouter `provider.allow_fallbacks=True` + optional models[] fallback chain.

    Lets OpenRouter route around provider-side 429 without our retry round-trip.
    Idempotent (won't overwrite caller's explicit provider/models).
    """
    if "provider" not in body:
        body["provider"] = {"allow_fallbacks": True}
    else:
        body["provider"].setdefault("allow_fallbacks", True)
    if fallback_models and "models" not in body and "model" in body:
        body["models"] = [body["model"], *fallback_models]
    return body
