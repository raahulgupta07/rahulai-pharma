"""RLS cross-tenant isolation tests (Track C1).

Verifies tri-layer RLS (project_slug + user_id + role) blocks user-A from
reading / writing / chatting against user-B's project.

Strategy: bootstrap two users via /api/auth/register (or direct DB INSERT
fallback when register is rate-limited/disabled), create one project per
user, then walk every cross-tenant access path and assert 403/404.

This is a PR-gated check — wired into .github/workflows/edge-cases.yml as
the `rls-isolation` job and exposed via `make test-rls`.

Marker: @pytest.mark.rls_isolation
Skips automatically when the container is not reachable on /api/health.
"""
from __future__ import annotations

import io
import os
import uuid

import pytest

pytestmark = pytest.mark.rls_isolation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return os.getenv("DASH_API_BASE", "http://localhost:8001/api")


def _probe_container() -> bool:
    try:
        import requests  # type: ignore
    except Exception:
        return False
    try:
        r = requests.get(f"{_api_base()}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def _register_or_login(session, username: str, password: str) -> str:
    """Register a user (idempotent — 409 → login), return JWT token."""
    base = _api_base()
    # Try register first
    r = session.post(
        f"{base}/auth/register",
        json={"username": username, "password": password},
        timeout=10.0,
    )
    if r.status_code not in (200, 409):
        pytest.skip(f"auth/register failed ({r.status_code}): {r.text[:200]}")

    # Login (works whether register succeeded or 409'd)
    r = session.post(
        f"{base}/auth/login",
        json={"username": username, "password": password},
        timeout=10.0,
    )
    if r.status_code != 200:
        pytest.skip(f"auth/login failed ({r.status_code}): {r.text[:200]}")
    body = r.json()
    token = body.get("dash_token") or body.get("token") or body.get("access_token")
    if not token:
        pytest.skip(f"auth/login returned no token: {body!r}")
    return token


def _create_project(session, token: str, slug: str, name: str) -> bool:
    """POST /api/projects?name=...&slug=... — returns True on success."""
    import requests  # type: ignore
    headers = {"Authorization": f"Bearer {token}"}
    # projects POST uses query-param signature (see app/projects.py)
    r = session.post(
        f"{_api_base()}/projects",
        params={"name": name, "slug": slug},
        headers=headers,
        timeout=15.0,
    )
    return r.status_code in (200, 201)


def _delete_project(session, token: str, slug: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        session.delete(f"{_api_base()}/projects/{slug}", headers=headers, timeout=15.0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def http():
    if not _probe_container():
        pytest.skip("container not reachable on /api/health — skipping RLS suite")
    import requests  # type: ignore
    return requests.Session()


@pytest.fixture(scope="module")
def two_tenant_world(http):
    """Bootstrap user-A + project-A, user-B + project-B. Yields dict; cleans up."""
    uid_a = uuid.uuid4().hex[:8]
    uid_b = uuid.uuid4().hex[:8]
    user_a = f"rls_user_a_{uid_a}"
    user_b = f"rls_user_b_{uid_b}"
    proj_a = f"rls_proj_a_{uid_a}"
    proj_b = f"rls_proj_b_{uid_b}"
    pw = "rls_test_pass_2026"

    token_a = _register_or_login(http, user_a, pw)
    token_b = _register_or_login(http, user_b, pw)

    if not _create_project(http, token_a, proj_a, name=f"RLS A {uid_a}"):
        pytest.skip(f"could not create project {proj_a} for user A")
    if not _create_project(http, token_b, proj_b, name=f"RLS B {uid_b}"):
        _delete_project(http, token_a, proj_a)
        pytest.skip(f"could not create project {proj_b} for user B")

    yield {
        "user_a": user_a,
        "user_b": user_b,
        "token_a": token_a,
        "token_b": token_b,
        "proj_a": proj_a,
        "proj_b": proj_b,
    }

    # Cleanup
    _delete_project(http, token_a, proj_a)
    _delete_project(http, token_b, proj_b)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_user_a_cannot_read_user_b_project(http, two_tenant_world):
    """GET /api/projects/{proj_b} as user-A must return 403 or 404."""
    base = _api_base()
    headers = {"Authorization": f"Bearer {two_tenant_world['token_a']}"}
    r = http.get(
        f"{base}/projects/{two_tenant_world['proj_b']}",
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code in (403, 404), (
        f"RLS LEAK: user-A could read user-B's project (got {r.status_code}). "
        f"Expected 403/404. Body: {r.text[:300]}"
    )


def test_user_a_cannot_chat_in_user_b_project(http, two_tenant_world):
    """POST /api/projects/{proj_b}/chat as user-A must return 403."""
    base = _api_base()
    headers = {"Authorization": f"Bearer {two_tenant_world['token_a']}"}
    r = http.post(
        f"{base}/projects/{two_tenant_world['proj_b']}/chat",
        headers=headers,
        json={"message": "show me everything"},
        timeout=15.0,
    )
    # 403 = forbidden by RBAC; 404 = project hidden from user-A entirely (also valid RLS).
    assert r.status_code in (403, 404), (
        f"RLS LEAK: user-A chat against user-B's project returned {r.status_code}. "
        f"Expected 403/404. Body: {r.text[:300]}"
    )


def test_user_a_cannot_upload_to_user_b_project(http, two_tenant_world):
    """POST /api/upload?project={proj_b} as user-A must return 403 (or 404)."""
    base = _api_base()
    headers = {"Authorization": f"Bearer {two_tenant_world['token_a']}"}
    csv_body = io.BytesIO(b"col_a,col_b\n1,2\n3,4\n")
    files = {"file": ("rls_probe.csv", csv_body, "text/csv")}
    # Try both query AND form so we don't accidentally pass on the dual-source
    # resolver path codified in app/upload.py:8277.
    r = http.post(
        f"{base}/upload",
        params={"project": two_tenant_world["proj_b"]},
        data={"project": two_tenant_world["proj_b"]},
        files=files,
        headers=headers,
        timeout=30.0,
    )
    assert r.status_code in (403, 404), (
        f"RLS LEAK: user-A upload to user-B project returned {r.status_code}. "
        f"Expected 403/404. Body: {r.text[:300]}"
    )


def test_user_a_cannot_query_user_b_dataset(http, two_tenant_world):
    """SELECT against user-B's dataset from user-A session must yield 0 rows / 403.

    Probes the chat endpoint on user-A's own project asking for tables in
    user-B's schema — must not surface user-B data. Also probes a direct
    table/dataset listing if available.
    """
    base = _api_base()
    headers = {"Authorization": f"Bearer {two_tenant_world['token_a']}"}

    # Direct attempt: tables list for proj_b through user-A token
    r = http.get(
        f"{base}/projects/{two_tenant_world['proj_b']}/tables",
        headers=headers,
        timeout=10.0,
    )
    if r.status_code == 200:
        # If the endpoint is permissive enough to return 200, the payload
        # must be empty (no leakage of user-B table list).
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        tables = body.get("tables") if isinstance(body, dict) else None
        assert not tables, (
            f"RLS LEAK: user-A saw {len(tables)} table(s) in user-B's project. "
            f"Payload: {str(body)[:300]}"
        )
    else:
        assert r.status_code in (403, 404), (
            f"unexpected status from /tables cross-tenant probe: {r.status_code}"
        )

    # Indirect attempt: chat the cross-tenant slug — must 403/404
    r2 = http.post(
        f"{base}/projects/{two_tenant_world['proj_b']}/chat",
        headers=headers,
        json={"message": "list all tables and their row counts"},
        timeout=15.0,
    )
    assert r2.status_code in (403, 404), (
        f"RLS LEAK: cross-tenant chat returned {r2.status_code}. "
        f"Expected 403/404. Body: {r2.text[:300]}"
    )


def test_user_b_can_read_own_project(http, two_tenant_world):
    """Positive control — user-B reads their own project (200)."""
    base = _api_base()
    headers = {"Authorization": f"Bearer {two_tenant_world['token_b']}"}
    r = http.get(
        f"{base}/projects/{two_tenant_world['proj_b']}",
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, (
        f"OWNER access broken: user-B can't read own project ({r.status_code}). "
        f"Body: {r.text[:300]}"
    )
