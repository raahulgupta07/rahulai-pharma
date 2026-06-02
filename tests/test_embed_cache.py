"""Embed cache unit tests — fakeredis-backed.

Run: pytest tests/test_embed_cache.py -v
"""
from __future__ import annotations

import os
import sys
import importlib

import pytest


@pytest.fixture
def ec(monkeypatch):
    """Fresh embed_cache module with fakeredis injected as the client.

    Falls back to monkeypatched in-process dict if fakeredis isn't installed.
    """
    monkeypatch.setenv("EMBED_CACHE_DISABLED", "")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")

    # Import fresh.
    if "dash.cache.embed_cache" in sys.modules:
        del sys.modules["dash.cache.embed_cache"]
    mod = importlib.import_module("dash.cache.embed_cache")

    # Try fakeredis first.
    try:
        import fakeredis  # type: ignore
        client = fakeredis.FakeStrictRedis(decode_responses=True)
    except Exception:
        # Fallback: a minimal in-memory shim with the methods we use.
        class _Mem:
            def __init__(self):
                self.kv: dict = {}
                self.sets: dict = {}
            def get(self, k):
                return self.kv.get(k)
            def setex(self, k, ttl, v):
                self.kv[k] = v
                return True
            def incr(self, k):
                self.kv[k] = str(int(self.kv.get(k, "0")) + 1)
                return int(self.kv[k])
            def sadd(self, k, v):
                self.sets.setdefault(k, set()).add(v)
                return 1
            def smembers(self, k):
                return self.sets.get(k, set())
            def delete(self, *keys):
                n = 0
                for k in keys:
                    if k in self.kv:
                        del self.kv[k]; n += 1
                    if k in self.sets:
                        del self.sets[k]; n += 1
                return n
            def expire(self, k, ttl):
                return True
            def scan_iter(self, match=None, count=200):
                import fnmatch
                for k in list(self.sets.keys()):
                    if match is None or fnmatch.fnmatch(k, match):
                        yield k
            def ping(self):
                return True
        client = _Mem()

    mod._client = client
    mod._client_init_attempted = True
    yield mod


def test_cache_hit_returns_stored(ec):
    key = ec.cache_key("E1", "S1", "paracetamol stock?")
    assert ec.set(key, {"content": "yes 100 units", "external_user": "alice"}, ttl=300) is True
    out = ec.get(key)
    assert out is not None
    assert out["content"] == "yes 100 units"
    assert out["external_user"] == "alice"


def test_cache_miss_returns_none(ec):
    assert ec.get("embed:chat:doesnotexist") is None


def test_normalize_treats_whitespace_same(ec):
    k1 = ec.cache_key("X", "S1", "paracetamol  stock?")
    k2 = ec.cache_key("X", "S1", "paracetamol stock?")
    assert k1 == k2


def test_normalize_treats_punctuation_and_case_same(ec):
    k1 = ec.cache_key("X", "S1", "Paracetamol, stock?")
    k2 = ec.cache_key("X", "S1", "paracetamol stock")
    assert k1 == k2


def test_cache_key_includes_site_id(ec):
    """Cross-tenant leak prevention: Shop A and Shop B must hash to different keys."""
    k_a = ec.cache_key("E1", "shop_a", "paracetamol stock?")
    k_b = ec.cache_key("E1", "shop_b", "paracetamol stock?")
    assert k_a != k_b


def test_redis_down_returns_none_no_raise(ec, monkeypatch):
    class _Broken:
        def get(self, *a, **k):
            raise ConnectionError("redis down")
        def setex(self, *a, **k):
            raise ConnectionError("redis down")
        def ping(self):
            raise ConnectionError("redis down")
    ec._client = _Broken()
    # Must not raise.
    assert ec.get("any") is None
    assert ec.set("any", {"x": 1}) is False


def test_invalidate_clears_by_pattern(ec):
    # Set 5 keys for embed=X, site=S1.
    keys = []
    for i in range(5):
        k = ec.cache_key("X", "S1", f"question {i}")
        ec.set_indexed(k, {"content": f"answer {i}"}, "X", "S1")
        keys.append(k)
    # Sanity: all present.
    for k in keys:
        assert ec.get(k) is not None
    # Invalidate all for embed X.
    n = ec.invalidate("X")
    assert n >= 5
    for k in keys:
        assert ec.get(k) is None


def test_stats_returns_dict(ec):
    ec.incr_hit()
    ec.incr_hit()
    ec.incr_miss()
    s = ec.stats()
    assert "hits" in s and "misses" in s and "hit_ratio" in s and "redis_connected" in s
    assert s["redis_connected"] is True
    assert s["hits"] >= 2
    assert s["misses"] >= 1


def test_disabled_env_returns_none(monkeypatch):
    monkeypatch.setenv("EMBED_CACHE_DISABLED", "1")
    if "dash.cache.embed_cache" in sys.modules:
        del sys.modules["dash.cache.embed_cache"]
    mod = importlib.import_module("dash.cache.embed_cache")
    # No client should init.
    assert mod._get_client() is None
    assert mod.get("any") is None
    assert mod.set("any", {"x": 1}) is False
