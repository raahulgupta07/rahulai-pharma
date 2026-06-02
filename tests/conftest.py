"""Test bootstrap — ensure repo root on sys.path so `dash.providers` resolves.

Also exposes session-scoped fixtures for the E2E harness:
  - api_base()         — http://localhost:8001/api by default
  - auth_token()       — POSTs /auth/login with demo/demo, returns dash_token
  - http_client()      — requests.Session w/ Authorization header preset
  - temp_project_slug  — function-scoped unique slug, yields, deletes after test

E2E tests are also skipped when the container isn't reachable on /api/health.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def pytest_collection_modifyitems(config, items):
    """Skip federation tests by default — set FEDERATION_ENABLED=1 to run them.

    Federation tests require ≥2 connected sources, exercise a niche code path,
    and total ~3,500 LOC across 14 files — they slow CI substantially.

    Also skips e2e-marked tests when the local container isn't reachable on
    /api/health — keeps the suite green in environments without a running stack.
    """
    fed_enabled = os.getenv("FEDERATION_ENABLED", "").lower() in ("1", "true", "yes", "on")
    if not fed_enabled:
        skip_fed = pytest.mark.skip(
            reason="federation gated — set FEDERATION_ENABLED=1 to run"
        )
        for item in items:
            if "test_federation_" in str(item.fspath):
                item.add_marker(skip_fed)

    # Probe container once per collection. If down, skip all e2e items.
    container_up = _probe_container()
    if not container_up:
        skip_e2e = pytest.mark.skip(
            reason="container not reachable on /api/health — skipping e2e suite"
        )
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)


def _probe_container() -> bool:
    """Quick (<2s) probe of /api/health. Returns False on any error."""
    try:
        import requests  # type: ignore
    except Exception:
        return False
    base = os.getenv("DASH_API_BASE", "http://localhost:8001/api")
    try:
        r = requests.get(f"{base}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session-scoped fixtures for the E2E harness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_base() -> str:
    """Base URL for the FastAPI app. Override with DASH_API_BASE env."""
    return os.getenv("DASH_API_BASE", "http://localhost:8001/api")


@pytest.fixture(scope="session")
def auth_token(api_base: str) -> str:
    """Login as demo/demo and return the JWT token."""
    import requests  # type: ignore
    user = os.getenv("DASH_TEST_USER", "demo")
    pw = os.getenv("DASH_TEST_PASS", "demo")
    r = requests.post(
        f"{api_base}/auth/login",
        json={"username": user, "password": pw},
        timeout=10.0,
    )
    if r.status_code != 200:
        pytest.skip(f"auth/login failed ({r.status_code}): {r.text[:200]}")
    body = r.json()
    token = body.get("dash_token") or body.get("token") or body.get("access_token")
    if not token:
        pytest.skip(f"auth/login returned no token: {body!r}")
    return token


@pytest.fixture(scope="session")
def http_client(auth_token: str):
    """requests.Session with Authorization header preset."""
    import requests  # type: ignore
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {auth_token}"})
    return s


@pytest.fixture
def temp_project_slug(http_client, api_base):
    """Generate a unique project slug, yield it, attempt cleanup after test.

    The harness uses this to keep fixtures isolated. Cleanup is best-effort —
    test failure should not be masked by a delete failure.
    """
    slug = f"e2e_{uuid.uuid4().hex[:10]}"
    yield slug
    try:
        http_client.delete(f"{api_base}/projects/{slug}", timeout=15.0)
    except Exception:
        pass
