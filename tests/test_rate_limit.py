"""Rate-limit middleware regression test (Track C3).

Verifies:
1. Default route limit (120/min) enforced — 121st req in 60s → 429
2. 429 response includes `Retry-After` header (RFC 6585)
3. Whitelisted paths (/api/health) NEVER rate-limited
4. Per-route override (chat=60/min, upload=10/min)
5. RATE_LIMIT_DISABLED=1 bypasses all limiting

Builds a minimal Starlette app + RateLimitMiddleware via TestClient. No
container required — runs purely in-process so CI can gate on it.
"""
from __future__ import annotations

import importlib
import os

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

pytestmark = pytest.mark.rate_limit


def _ensure_testclient_works() -> None:
    """Skip the suite if the local Starlette/httpx combo is broken (host-env
    issue documented in CLAUDE.md — pytest sometimes picks up a Starlette
    version that passes `app=` to httpx 0.28 which dropped that kwarg).
    CI environment uses requirements.txt-pinned versions and is unaffected."""
    try:
        TestClient(Starlette())
    except TypeError as e:
        pytest.skip(f"local Starlette/httpx incompatible — skipping rate-limit "
                    f"suite (CI uses pinned versions). Detail: {e}")


@pytest.fixture(autouse=True)
def _reset_counter_between_tests():
    """Reset the in-process counter between tests."""
    from app.rate_limit import _COUNTER
    with _COUNTER._lock:
        _COUNTER._buckets.clear()
    yield
    with _COUNTER._lock:
        _COUNTER._buckets.clear()


def _build_client(reload_env: bool = False) -> TestClient:
    """Build a Starlette + RateLimitMiddleware test client."""
    _ensure_testclient_works()
    if reload_env:
        from app import rate_limit as rl_mod
        importlib.reload(rl_mod)
    from app.rate_limit import RateLimitMiddleware

    async def ok(request):  # noqa: ARG001
        return JSONResponse({"ok": True})

    app = Starlette(routes=[
        Route("/api/health", ok),
        Route("/api/echo", ok),
        Route("/api/upload", ok, methods=["GET", "POST"]),
        Route("/api/projects/p/chat", ok, methods=["GET", "POST"]),
        Route("/api/projects/p/retrain", ok, methods=["GET", "POST"]),
    ])
    app.add_middleware(RateLimitMiddleware)
    return TestClient(app)


def test_health_never_rate_limited():
    """Whitelisted /api/health survives well past the default limit."""
    _ensure_testclient_works()
    client = _build_client()
    for _ in range(250):
        r = client.get("/api/health")
        assert r.status_code == 200


def test_default_route_429_after_threshold():
    """Default route limit is 120/min; 121st req → 429 with Retry-After."""
    os.environ.pop("RATE_LIMIT_DEFAULT", None)
    client = _build_client(reload_env=True)
    for i in range(120):
        r = client.get("/api/echo")
        assert r.status_code == 200, f"req {i+1} unexpectedly limited"
    r = client.get("/api/echo")
    assert r.status_code == 429, f"expected 429, got {r.status_code}"
    assert "Retry-After" in r.headers, "429 must include Retry-After (RFC 6585)"
    assert int(r.headers["Retry-After"]) >= 1


def test_chat_route_429_at_61():
    """Chat limit is 60/min — 61st req → 429."""
    os.environ["RATE_LIMIT_CHAT"] = "60/minute"
    client = _build_client(reload_env=True)
    for i in range(60):
        r = client.get("/api/projects/p/chat")
        assert r.status_code == 200, f"chat req {i+1} unexpectedly limited"
    r = client.get("/api/projects/p/chat")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_upload_route_429_at_11():
    """Upload limit is 10/min — 11th req → 429."""
    os.environ["RATE_LIMIT_UPLOAD"] = "10/minute"
    client = _build_client(reload_env=True)
    for i in range(10):
        r = client.get("/api/upload")
        assert r.status_code == 200, f"upload req {i+1} unexpectedly limited"
    r = client.get("/api/upload")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_training_route_429_at_21():
    """Training limit is 20/min — 21st req → 429."""
    os.environ["RATE_LIMIT_TRAINING"] = "20/minute"
    client = _build_client(reload_env=True)
    for i in range(20):
        r = client.get("/api/projects/p/retrain")
        assert r.status_code == 200, f"training req {i+1} unexpectedly limited"
    r = client.get("/api/projects/p/retrain")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_disabled_via_env(monkeypatch):
    """RATE_LIMIT_DISABLED=1 → no requests are limited."""
    _ensure_testclient_works()
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    client = _build_client()
    for _ in range(250):
        r = client.get("/api/echo")
        assert r.status_code == 200
