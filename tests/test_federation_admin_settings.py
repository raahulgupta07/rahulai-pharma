"""Tests for federation admin settings + RBAC-aware resolver.

Validates:
  - 4 new federation entries registered in REGISTRY with correct defaults
  - per-project scoping works for federation flags
  - resolve_with_rbac denies when check_project_permission returns False
  - resolve_with_rbac falls back to allow when permission system unavailable
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub db.session before importing the module — same pattern as test_admin_settings.
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


@pytest.fixture(autouse=True)
def _clear_cache():
    from dash.admin import settings as S
    with S._lock:
        S._CACHE.clear()
    yield
    with S._lock:
        S._CACHE.clear()


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

class TestFederationRegistry:
    def test_enable_federation_join_in_registry(self):
        from dash.admin.settings import REGISTRY
        assert "enable_federation_join" in REGISTRY
        spec = REGISTRY["enable_federation_join"]
        assert spec["type"] == "bool"
        assert spec["default"] is True
        assert spec["scope"] == "both"

    def test_max_cross_source_rows_default_50k(self):
        from dash.admin.settings import REGISTRY
        spec = REGISTRY["max_cross_source_rows"]
        assert spec["type"] == "int"
        assert spec["default"] == 50000
        assert spec["scope"] == "both"

    def test_federation_timeout_default_60s(self):
        from dash.admin.settings import REGISTRY
        spec = REGISTRY["federation_timeout_s"]
        assert spec["type"] == "int"
        assert spec["default"] == 60
        assert spec["scope"] == "global"

    def test_federation_default_engine_enum_choices(self):
        from dash.admin.settings import REGISTRY
        spec = REGISTRY["federation_default_engine"]
        assert spec["type"] == "enum"
        assert spec["default"] == "duckdb"
        assert spec["scope"] == "global"
        assert set(spec["choices"]) == {"duckdb", "pandas"}

    def test_federation_settings_can_be_set_per_project(self):
        """enable_federation_join + max_cross_source_rows have scope='both',
        so they MUST be settable at project scope."""
        from dash.admin.settings import REGISTRY
        for key in ("enable_federation_join", "max_cross_source_rows"):
            assert REGISTRY[key]["scope"] in ("project", "both"), (
                f"{key} should be project-scopable"
            )


# ---------------------------------------------------------------------------
# resolve_with_rbac
# ---------------------------------------------------------------------------

class TestResolveWithRbac:
    def test_resolve_with_rbac_denies_when_no_access(self, monkeypatch):
        """When check_project_permission returns False, all sources rejected."""
        # Use monkeypatch — auto-restored after test, no pollution leak.
        # Patch the function in whatever module already provides it.
        try:
            import app.auth as real_auth
            monkeypatch.setattr(real_auth, "check_project_permission",
                                  MagicMock(return_value=False))
        except Exception:
            # Fallback: install minimal fake via monkeypatch.setitem
            fake_auth = ModuleType("app.auth")
            fake_auth.check_project_permission = MagicMock(return_value=False)
            monkeypatch.setitem(sys.modules, "app.auth", fake_auth)

        from dash.providers.federation.resolver import resolve_with_rbac
        result = resolve_with_rbac(
            ["src_a", "src_b"], "secret_project", user_id=42,
            requesting_agent_scope="analyst", user_role="viewer",
        )
        assert result.all_accessible is False
        assert any("lacks viewer access" in e for e in result.errors)
        assert len(result.sources) == 2
        for s in result.sources:
            assert s.accessible is False
            assert s.error == "rbac_denied"

    def test_resolve_with_rbac_falls_back_when_permission_unavailable(self, monkeypatch):
        """If check_project_permission import or call raises, delegate to resolve()."""
        # Patch app.auth.check_project_permission to raise — triggers fallback path
        try:
            import app.auth as real_auth
            def _raise(*a, **kw):
                raise RuntimeError("permission system unavailable")
            monkeypatch.setattr(real_auth, "check_project_permission", _raise)
        except Exception:
            pass

        # Patch resolve() to observe delegation
        from dash.providers.federation import resolver as R
        sentinel = R.ResolutionResult()
        sentinel.errors.append("delegated")
        with patch.object(R, "resolve", return_value=sentinel) as mock_resolve:
            result = R.resolve_with_rbac(
                ["src_x"], "proj", user_id=1,
                requesting_agent_scope="analyst", user_role="viewer",
            )
            # Fallback path delegated to resolve()
            assert mock_resolve.called
            assert result is sentinel
