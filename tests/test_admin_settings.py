"""Tests for dash/admin/settings.py runtime config.

Mocks: SQL engine (no real DB).
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub db.session before importing the module — same pattern as test_scheduler.
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
    """Clear in-process cache before each test."""
    from dash.admin import settings as S
    with S._lock:
        S._CACHE.clear()
    yield
    with S._lock:
        S._CACHE.clear()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registry_contains_expected_keys(self):
        from dash.admin.settings import REGISTRY
        for key in (
            "enable_self_learning",
            "daily_cron_time",
            "max_questions_per_cycle",
            "pii_default_action",
            "rate_limit_per_minute",
            "enable_in_process_scheduler",
        ):
            assert key in REGISTRY

    def test_registry_specs_have_required_fields(self):
        from dash.admin.settings import REGISTRY
        for key, spec in REGISTRY.items():
            assert "type" in spec, f"{key} missing type"
            assert "scope" in spec, f"{key} missing scope"
            assert spec["type"] in (
                "bool", "int", "float", "string", "json", "cron", "enum"
            )
            assert spec["scope"] in ("global", "project", "both")


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

class TestCoerce:
    def test_coerce_bool_truthy_strings(self):
        from dash.admin.settings import _coerce
        for s in ("true", "True", "1", "yes", "ON", "y", "t"):
            assert _coerce(s, "bool") is True

    def test_coerce_bool_falsy_strings(self):
        from dash.admin.settings import _coerce
        for s in ("false", "0", "no", "off", ""):
            assert _coerce(s, "bool") is False

    def test_coerce_int(self):
        from dash.admin.settings import _coerce
        assert _coerce("42", "int") == 42
        assert _coerce("not-a-number", "int") == 0

    def test_coerce_float(self):
        from dash.admin.settings import _coerce
        assert _coerce("3.14", "float") == 3.14
        assert _coerce("nope", "float") == 0.0

    def test_coerce_json(self):
        from dash.admin.settings import _coerce
        assert _coerce('{"a": 1}', "json") == {"a": 1}
        assert _coerce({"already": "dict"}, "json") == {"already": "dict"}
        assert _coerce("not-json", "json") is None

    def test_coerce_string_passthrough(self):
        from dash.admin.settings import _coerce
        assert _coerce("hello", "string") == "hello"
        assert _coerce("0 4 * * *", "cron") == "0 4 * * *"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidate:
    def test_validate_unknown_key(self):
        from dash.admin.settings import _validate
        ok, err = _validate("nonexistent_key", "x")
        assert ok is False
        assert "unknown" in err

    def test_validate_enum_accepts_choice(self):
        from dash.admin.settings import _validate
        ok, _ = _validate("pii_default_action", "flag")
        assert ok is True

    def test_validate_enum_rejects_bad(self):
        from dash.admin.settings import _validate
        ok, err = _validate("pii_default_action", "explode")
        assert ok is False
        assert "one of" in err

    def test_validate_cron_5_fields(self):
        from dash.admin.settings import _validate
        ok, _ = _validate("daily_cron_time", "0 4 * * *")
        assert ok is True
        ok2, err = _validate("daily_cron_time", "0 4 *")
        assert ok2 is False
        assert "5 fields" in err

    def test_validate_int(self):
        from dash.admin.settings import _validate
        ok, _ = _validate("max_questions_per_cycle", "20")
        assert ok is True
        ok2, _ = _validate("max_questions_per_cycle", "twenty")
        assert ok2 is False


# ---------------------------------------------------------------------------
# get_setting resolution layers
# ---------------------------------------------------------------------------

class TestGetSetting:
    def test_falls_back_to_registry_default_when_no_db_no_env(self):
        from dash.admin import settings as S
        with patch.object(S, "_read_db", return_value=None), \
             patch.dict("os.environ", {}, clear=False):
            # ensure env var not set
            S._CACHE.clear()
            v = S.get_setting("max_questions_per_cycle")
            assert v == 20

    def test_uses_env_var_when_no_db(self):
        from dash.admin import settings as S
        S._CACHE.clear()
        with patch.object(S, "_read_db", return_value=None), \
             patch.dict("os.environ", {"RATE_LIMIT": "999"}, clear=False):
            v = S.get_setting("rate_limit_per_minute")
            assert v == 999

    def test_global_db_overrides_env(self):
        from dash.admin import settings as S
        S._CACHE.clear()

        def fake_read(key, scope, slug):
            if scope == "global" and slug is None:
                return "777"
            return None

        with patch.object(S, "_read_db", side_effect=fake_read), \
             patch.dict("os.environ", {"RATE_LIMIT": "999"}, clear=False):
            v = S.get_setting("rate_limit_per_minute")
            assert v == 777

    def test_project_db_overrides_global(self):
        from dash.admin import settings as S
        S._CACHE.clear()

        def fake_read(key, scope, slug):
            if scope == "project" and slug == "myproj":
                return "5"
            if scope == "global":
                return "10"
            return None

        with patch.object(S, "_read_db", side_effect=fake_read):
            v = S.get_setting("max_questions_per_cycle", project_slug="myproj")
            assert v == 5

    def test_cache_returns_same_value_within_ttl(self):
        from dash.admin import settings as S
        S._CACHE.clear()
        calls = {"n": 0}

        def fake_read(key, scope, slug):
            calls["n"] += 1
            return "3"

        with patch.object(S, "_read_db", side_effect=fake_read):
            S.get_setting("max_questions_per_cycle")
            S.get_setting("max_questions_per_cycle")
            S.get_setting("max_questions_per_cycle")
        # First call runs both project (skipped, no slug) + global = 1
        # Subsequent 2 calls hit cache.
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# set_setting
# ---------------------------------------------------------------------------

class TestSetSetting:
    def _fake_engine(self):
        eng = MagicMock()
        conn = MagicMock()
        eng.connect.return_value.__enter__.return_value = conn
        eng.connect.return_value.__exit__.return_value = False
        return eng, conn

    def test_unknown_key_rejected(self):
        from dash.admin.settings import set_setting
        ok, err = set_setting("does_not_exist", "x")
        assert ok is False
        assert "unknown" in err

    def test_invalid_scope_rejected(self):
        from dash.admin.settings import set_setting
        ok, err = set_setting("max_questions_per_cycle", 10, scope="weird")
        assert ok is False
        assert "scope" in err

    def test_project_scope_requires_slug(self):
        from dash.admin.settings import set_setting
        ok, err = set_setting("max_questions_per_cycle", 10, scope="project")
        assert ok is False
        assert "project_slug" in err

    def test_project_scope_on_global_only_setting_rejected(self):
        from dash.admin.settings import set_setting
        # daily_cron_time is global-only
        ok, err = set_setting(
            "daily_cron_time", "0 4 * * *",
            scope="project", project_slug="x",
        )
        assert ok is False
        assert "global-only" in err

    def test_persist_invalidates_cache(self):
        from dash.admin import settings as S
        eng, conn = self._fake_engine()
        S._CACHE[("max_questions_per_cycle", None)] = (9999.0, 99)
        with patch("db.session.get_sql_engine", return_value=eng):
            ok, err = S.set_setting("max_questions_per_cycle", 7)
        assert ok is True, err
        assert ("max_questions_per_cycle", None) not in S._CACHE
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_invalid_value_rejected(self):
        from dash.admin.settings import set_setting
        ok, err = set_setting("pii_default_action", "explode")
        assert ok is False
        assert "one of" in err


# ---------------------------------------------------------------------------
# reset_setting
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_executes_delete(self):
        from dash.admin import settings as S
        eng = MagicMock()
        conn = MagicMock()
        eng.connect.return_value.__enter__.return_value = conn
        eng.connect.return_value.__exit__.return_value = False
        with patch("db.session.get_sql_engine", return_value=eng):
            ok = S.reset_setting("max_questions_per_cycle")
        assert ok is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# list_settings
# ---------------------------------------------------------------------------

class TestList:
    def test_list_returns_all_registry_keys(self):
        from dash.admin import settings as S
        with patch.object(S, "_read_db", return_value=None):
            out = S.list_settings()
        assert len(out) == len(S.REGISTRY)
        keys = {row["key"] for row in out}
        assert "enable_self_learning" in keys
        assert "rate_limit_per_minute" in keys

    def test_list_includes_effective_value_and_metadata(self):
        from dash.admin import settings as S
        with patch.object(S, "_read_db", return_value=None):
            out = S.list_settings()
        row = next(r for r in out if r["key"] == "max_questions_per_cycle")
        assert row["type"] == "int"
        assert row["default"] == 20
        assert row["effective_value"] == 20
        assert "description" in row


# ---------------------------------------------------------------------------
# Serialize
# ---------------------------------------------------------------------------

class TestSerialize:
    def test_serialize_bool(self):
        from dash.admin.settings import _serialize
        assert _serialize(True, "bool") == "true"
        assert _serialize(False, "bool") == "false"

    def test_serialize_json(self):
        from dash.admin.settings import _serialize
        assert _serialize({"a": 1}, "json") == '{"a": 1}'

    def test_serialize_int_as_string(self):
        from dash.admin.settings import _serialize
        assert _serialize(42, "int") == "42"
